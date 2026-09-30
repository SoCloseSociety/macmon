"""AegisForge.app -- the ONE mutation path: pywebview's ``js_api`` bridge.

Everything that CHANGES the machine from the app window (kill / suspend a
process, clean, purge, prune, quarantine, pause / resume the sentinel, the
opt-in auto-remediation toggles) goes through the ``Api`` object handed to
``webview.create_window(js_api=...)``. It is reachable ONLY from JavaScript
running inside the native window (``window.pywebview.api.*``): unlike the
loopback HTTP server it is not an HTTP origin, so a DNS-rebinding page has no
route to it. The HTTP server (``app_webui``) stays GET-only and read-only.

Guardrails, all enforced HERE (the page is untrusted input, not a gate):
  - a mutating method runs only when called -- nothing here runs on a timer
    or at import; the page calls a method from a funnel's confirm step only
  - process signals refuse ``processes._is_protected_target`` (PID <= 1, the
    app itself / its parent, launchd, kernel_task, WindowServer, loginwindow,
    Finder, Dock, SystemUIServer) and re-check the PID's identity
    (create_time) so a recycled PID is never hit; a process the sweep-style
    ``aegis.never_touch`` guard names (IDE, agent, LLM runtime, container,
    the fleet mesh, a user app ...) needs an explicit ``override`` the modal
    only sets after the owner ticks the acknowledgement
  - cleaning is preview-first: ``clean_execute`` only ever touches the
    categories the LAST ``clean_scan`` produced, Trash-first through
    ``cleaner._trash_or_rm`` (``permanent`` is never passed)
  - the docker prune is dangling images only (``_docker_prune_dangling``),
    never ``-a``, never volumes
  - every engine call runs under one lock with the console captured, so the
    outcome a CLI user would read is returned to the page, never lost

macOS 26 / WKWebView pitfall (confirmed on the fleet's Sentinel): a js_api
method's return value reaches JavaScript as a JSON *string*. Every method
here returns a plain dict; the page ``JSON.parse``s whatever it gets.
"""
from __future__ import annotations

import contextlib
import subprocess
import sys
import threading
import time

import psutil
from rich.text import Text

from . import aegis, cleaner, docker_mgr, processes, security, sentinel
from .utils import console, format_size

# The only sentinel.conf keys the page may flip: the four opt-in remediation
# levels (all default OFF in sentinel.DEFAULTS). Thresholds stay CLI/file-only.
AUTO_KEYS = ("auto_purge", "auto_unload_ollama", "auto_trim_fleet", "auto_reap_orphans")
AUTO_LABELS = {
    "auto_purge": "Auto-purge inactive RAM (sudo -n purge, non-destructive)",
    "auto_unload_ollama": "Auto-unload idle ollama models (reload on demand)",
    "auto_trim_fleet": "Auto-trim idle AI sessions when RAM is critical (level 2)",
    "auto_reap_orphans": "Auto-reap lineage-proven leaked orphans (level 2, SIGTERM only)",
}

# A clean preview older than this must be re-scanned before it can run: the
# sizes and paths it holds describe a machine that has moved on.
SCAN_TTL_S = 15 * 60

_ENGINE_LOCK = threading.RLock()


@contextlib.contextmanager
def engine_call():
    """Serialize engine calls and capture what they print. Yields the Rich
    capture; ``.get()`` after the block is the console text (ANSI stripped
    by ``_lines``). One lock for the whole app: the engine's console is a
    shared global and two captures at once would interleave."""
    with _ENGINE_LOCK:
        with console.capture() as cap:
            yield cap


def _lines(text: str) -> list[str]:
    """Console output as plain, non-empty lines (ANSI stripped)."""
    return [ln.strip() for ln in Text.from_ansi(text or "").plain.splitlines() if ln.strip()]


def _last(text: str, default: str = "done") -> str:
    lines = _lines(text)
    return lines[-1][:240] if lines else default


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _proc_dict(p: psutil.Process) -> dict:
    """The ``aegis.scan()`` shape for one process (what ``never_touch`` reads)."""
    with p.oneshot():
        try:
            cmd = " ".join(p.cmdline() or [])
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            cmd = ""
        try:
            exe = p.exe() or ""
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            exe = ""
        try:
            user = p.username() or ""
        except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
            user = ""
        return {"pid": p.pid, "ppid": p.ppid(), "name": p.name() or "", "cmd": cmd,
                "exe": exe, "user": user, "ct": float(p.create_time() or 0.0)}


class Api:
    """The js_api object. Every public method is callable from the page as
    ``window.pywebview.api.<name>(...)`` and returns a plain dict."""

    def __init__(self):
        self._scan: list[dict] | None = None   # the last clean preview (never stale-executed)
        self._scan_at = 0.0

    # ── bridge check ────────────────────────────────────────────────────

    def ping(self) -> dict:
        return {"ok": True, "brand": aegis.BRAND, "frozen": bool(getattr(sys, "frozen", False))}

    # ── processes ───────────────────────────────────────────────────────

    def _target(self, pid, create_time):
        """Resolve + guard a signal target. Returns (proc, dict, None) or
        (None, None, refusal-dict). The refusal explains itself: the page shows
        it as the funnel's error state, never as silence."""
        pid = _int(pid)
        if pid is None or pid <= 1:
            return None, None, {"ok": False, "refused": "protected", "detail": "PID 0/1 is never a target."}
        try:
            p = psutil.Process(pid)
            name = p.name() or ""
        except psutil.NoSuchProcess:
            return None, None, {"ok": False, "refused": "gone", "detail": f"PID {pid} is not running."}
        except (psutil.AccessDenied, psutil.ZombieProcess) as e:
            return None, None, {"ok": False, "refused": "denied", "detail": f"PID {pid}: {e.__class__.__name__}."}
        if processes._is_protected_target(pid, name):
            return None, None, {"ok": False, "refused": "protected", "pid": pid, "name": name,
                                "detail": f"{name} (PID {pid}) is protected: never signalled."}
        ct = _float(create_time)
        try:
            live_ct = float(p.create_time() or 0.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return None, None, {"ok": False, "refused": "gone", "detail": f"PID {pid} vanished."}
        if ct is not None and not aegis._same(live_ct, ct):
            return None, None, {"ok": False, "refused": "recycled", "pid": pid, "name": name,
                                "detail": f"PID {pid} now belongs to another process ({name}); refresh the list."}
        try:
            d = _proc_dict(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            d = {"pid": pid, "ppid": 0, "name": name, "cmd": "", "exe": "", "user": "", "ct": live_ct}
        return p, d, None

    def process_guard(self, pid, create_time=None) -> dict:
        """What the confirm modal shows BEFORE asking: is it protected (hard
        no), does the sweep-style guard name it (needs the acknowledgement),
        is it serving anyone right now (informational)."""
        p, d, err = self._target(pid, create_time)
        if err:
            return err
        reason = aegis.never_touch(d)
        try:
            served = aegis._in_service(p.pid, aegis.family_of(d))
        except Exception:
            served = None
        return {"ok": True, "pid": p.pid, "name": d["name"], "protected": False,
                "guard": reason, "in_service": served, "user": d["user"],
                "cmd": (d["cmd"] or "")[:200]}

    def _signal(self, verb: str, pid, create_time, override) -> dict:
        p, d, err = self._target(pid, create_time)
        if err:
            return err
        reason = aegis.never_touch(d)
        if reason and not override:
            return {"ok": False, "refused": "guarded", "pid": p.pid, "name": d["name"], "guard": reason,
                    "detail": f"{d['name']} (PID {p.pid}) is guarded ({reason}): acknowledge to proceed by hand."}
        fn = {"kill": lambda: processes.kill_process(str(p.pid), force_yes=True),
              "suspend": lambda: processes.suspend_process(str(p.pid), force_yes=True),
              "resume": lambda: processes.resume_process(str(p.pid))}[verb]
        with engine_call() as cap:
            fn()
        out = cap.get()
        low = out.lower()
        ok = not ("error" in low or "no process found" in low)
        return {"ok": ok, "pid": p.pid, "name": d["name"], "verb": verb,
                "detail": _last(out, f"{verb}: done"), "override": bool(override and reason)}

    def kill_process(self, pid, create_time=None, override=False) -> dict:
        """SIGTERM (graceful, never SIGKILL) via ``processes.kill_process``."""
        return self._signal("kill", pid, create_time, override)

    def suspend_process(self, pid, create_time=None, override=False) -> dict:
        return self._signal("suspend", pid, create_time, override)

    def resume_process(self, pid, create_time=None) -> dict:
        return self._signal("resume", pid, create_time, True)

    def purge_ram(self) -> dict:
        """``sudo -n purge`` -- never prompts; fails fast unless the sudoers
        rule from ``macmon sentinel --setup-purge`` is installed."""
        with engine_call() as cap:
            processes.purge_ram()
        out = cap.get()
        return {"ok": "failed" not in out.lower(), "detail": _last(out), "lines": _lines(out)[-4:]}

    # ── clean (preview -> confirm -> Trash-first execute) ───────────────

    def clean_scan(self) -> dict:
        """Step 1 of the funnel: the itemized preview. Touches nothing."""
        with engine_call() as cap:
            results = cleaner._scan_all()
        self._scan, self._scan_at = results, time.time()
        cats = []
        for i, r in enumerate(results):
            size = int(r.get("size") or 0)
            if size <= 0:
                continue
            cats.append({"id": i, "name": r.get("name", "?"), "size": size, "size_label": format_size(size),
                         "count": int(r.get("count") or 0)})
        cats.sort(key=lambda c: c["size"], reverse=True)
        total = sum(c["size"] for c in cats)
        return {"ok": True, "categories": cats, "total": total, "total_label": format_size(total),
                "notes": _lines(cap.get())[:6], "scanned_at": self._scan_at}

    def clean_execute(self, ids) -> dict:
        """Step 3: clean ONLY the previewed categories the owner ticked.
        Trash-first (``cleaner._trash_or_rm`` skips, never escalates)."""
        if not self._scan:
            return {"ok": False, "refused": "no-scan", "detail": "Scan first: nothing has been previewed."}
        if time.time() - self._scan_at > SCAN_TTL_S:
            self._scan = None
            return {"ok": False, "refused": "stale", "detail": "The preview is stale (15 min): scan again."}
        wanted = {_int(i) for i in (ids or [])}
        wanted.discard(None)
        selected = [r for i, r in enumerate(self._scan) if i in wanted and (r.get("size") or 0) > 0]
        if not selected:
            return {"ok": False, "refused": "empty", "detail": "Nothing selected."}
        with engine_call() as cap:
            freed = cleaner._execute_clean(selected, permanent=False)
        self._scan = None   # a preview runs once; the next clean re-scans
        notes = [ln for ln in _lines(cap.get()) if "skipped" in ln.lower()]
        return {"ok": True, "freed": int(freed), "freed_label": format_size(int(freed)),
                "cleaned": [r.get("name") for r in selected], "skipped": len(notes), "notes": notes[:8]}

    # ── docker ──────────────────────────────────────────────────────────

    def docker_prune_dangling(self) -> dict:
        """Dangling images only -- the engine's guarded prune."""
        with engine_call() as cap:
            if not docker_mgr._docker_available():
                return {"ok": False, "refused": "no-docker", "detail": "Docker is not running."}
            r = docker_mgr._docker_prune_dangling(force_yes=True)
        r = dict(r)
        r.setdefault("detail", _last(cap.get()))
        return r

    # ── security ────────────────────────────────────────────────────────

    def quarantine(self, pid, create_time=None) -> dict:
        """Kill + firewall-block one process (``security._quarantine_process``,
        which refuses the protected set itself; the identity check is ours).
        The firewall step needs sudo and reports honestly when it cannot."""
        p, d, err = self._target(pid, create_time)
        if err:
            return err
        with engine_call() as cap:
            security._quarantine_process(str(p.pid), force_yes=True)
        out = cap.get()
        return {"ok": "killed" in out.lower(), "pid": p.pid, "name": d["name"], "lines": _lines(out)[-5:],
                "detail": _last(out)}

    # ── sentinel ────────────────────────────────────────────────────────

    def sentinel_pause(self) -> dict:
        with engine_call() as cap:
            sentinel.pause()
        return {"ok": True, "detail": _last(cap.get())}

    def sentinel_resume(self) -> dict:
        with engine_call() as cap:
            sentinel.resume()
        out = cap.get()
        return {"ok": "failed" not in out.lower(), "detail": _last(out)}

    def sentinel_set_auto(self, key, on) -> dict:
        """Flip ONE opt-in remediation level in sentinel.conf (the sentinel's
        own writer). Only the four AUTO_KEYS; everything else is refused."""
        if key not in AUTO_KEYS:
            return {"ok": False, "refused": "key", "detail": f"{key!r} is not a remediation toggle."}
        on = bool(on) and str(on).lower() not in ("false", "0", "off", "")
        with engine_call():
            sentinel._write_conf({key: on})
            cfg = sentinel._conf()
        return {"ok": True, "key": key, "on": aegis._bool(cfg, key),
                "auto": {k: aegis._bool(cfg, k) for k in AUTO_KEYS},
                "purge_ready": sentinel._purge_nopasswd_ready() if key == "auto_purge" and on else None}

    # ── disk ────────────────────────────────────────────────────────────

    def reveal(self, path) -> dict:
        """Reveal a path in Finder (macOS ``open -R``). Read-only for the
        filesystem; spawns Finder, hence a bridge method and not a GET."""
        if sys.platform != "darwin":
            return {"ok": False, "detail": "Reveal in Finder is macOS only."}
        import os
        path = os.path.expanduser(str(path or ""))
        if not path or not os.path.exists(path):
            return {"ok": False, "detail": "Path does not exist."}
        try:
            subprocess.Popen(["open", "-R", path])
        except OSError as e:
            return {"ok": False, "detail": str(e)}
        return {"ok": True, "detail": f"Revealed {path}"}
