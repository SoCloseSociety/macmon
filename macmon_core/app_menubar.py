"""AegisForge.app -- native macOS menu-bar app wrapping the macmon/Aegis engine.

Presented to the owner as AegisForge ("fleet health, forged"). This is the
menu-bar face of the SAME engine the CLI/sentinel use: it does NOT sample or
remediate on its own -- the `co.soclose.macmon.monitor` LaunchAgent keeps doing
the 60s sampling + notify-only detection. This app READS what the sampler wrote
(``~/.macmon/metrics.jsonl`` + the alerts log), runs the Aegis trend detectors
on those rows, and shows a glanceable status icon + a dropdown. Destructive
actions are explicit menu clicks only; nothing here ever auto-kills a workload.

Actions reach the engine two ways. Frozen (the PyInstaller .app) there is no
interpreter to spawn -- ``sys.executable`` IS the app and ``macmon.py`` is not
in the bundle -- so they call the engine's functions in-process on a worker
thread. In dev mode they run the installed ``macmon`` launcher (else this
interpreter on the repo's ``macmon.py``) as a subprocess. Either way the
outcome is reported by notification: a menu-bar app has no console, so nothing
here may fail silently.

Optional dependency: ``rumps`` (a thin wrapper over pyobjc/NSStatusBar). It is
NOT a core runtime dep of the macmon CLI -- install it with ``pip install
-e ".[app]"`` or via ``build_aegisforge.sh`` which freezes this into
``AegisForge.app`` with PyInstaller. Run in dev mode with
``python -m macmon_core.app_menubar``.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from rich.text import Text

try:  # the menu-bar layer is optional; the CLI never imports rumps
    import rumps
except ImportError:  # pragma: no cover - exercised via the packaged app only
    rumps = None

from . import aegis, sentinel
from .utils import _applescript_escape, console

REFRESH_S = 30                       # menu refresh cadence (the sampler is 60s)
STALE_S = 180                        # a sample older than this is flagged STALE
REPO = Path(__file__).resolve().parent.parent
# Menu-bar template glyph (monochrome, tinted by macOS). Rendered by
# build_aegisforge.sh from the brand mono mark; falls back to a text title.
MENUBAR_ICON = REPO / "assets" / "aegisforge-menubar.png"
LIVE_ANCHOR = "Open live dashboard"  # the live status block sits above this item

# Worst-first ordering so the status icon reflects the most severe finding.
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "ok": -1}
# Menu-bar title prefix per worst severity (icon is monochrome; the glyph before
# the value gives an at-a-glance read without relying on colour alone).
_SEV_MARK = {"critical": "◉", "high": "◉", "medium": "▲",
             "low": "▸", "info": "", "ok": ""}


def _cfg() -> dict:
    """Sentinel config: DEFAULTS + the user's sentinel.conf overlay. The file is
    JSON (written by ``sentinel._write_conf``), so it is read with the
    sentinel's own loader -- a TOML parse of it fails and silently yields the
    defaults, i.e. the app would ignore every threshold the owner set."""
    return sentinel._conf()


def _finding_severity(f) -> str:
    """``aegis.Finding`` carries ``.level``; the older ``.severity``/``.sev``
    spellings are accepted too so a foreign finding still ranks."""
    sev = getattr(f, "level", None) or getattr(f, "severity", None) or getattr(f, "sev", None) or "info"
    return str(sev).lower()


def _finding_text(f) -> str:
    return getattr(f, "msg", None) or getattr(f, "what", None) or str(f)


def sample_age(latest: dict, now: float):
    """Seconds since the latest sample; None when there is none / no timestamp."""
    ts = latest.get("ts") if latest else None
    return max(0.0, now - ts) if isinstance(ts, (int, float)) else None


def age_text(age_s) -> str:
    """'sample 3m ago', flagged STALE past STALE_S: the sampler is a 60s job,
    so a stale row means it is paused, wedged or uninstalled -- and every
    figure under it is old news, which the dropdown must say."""
    if age_s is None:
        return "sample: none yet"
    ago = f"{age_s:.0f}s ago" if age_s < 120 else f"{age_s / 60:.0f}m ago"
    return f"sample {ago}" + (" / STALE" if age_s > STALE_S else "")


def gather() -> dict:
    """Read the sampler's output and derive the current picture. Never raises:
    on any error it returns a safe 'unknown' snapshot so the menu still renders."""
    out = {"vitals": {}, "findings": [], "alerts": [], "worst": "ok", "age_s": None, "error": None}
    try:
        rows = sentinel._load_tail(16) or []
        now = time.time()
        cfg = _cfg()
        latest = rows[-1] if rows else {}
        out["age_s"] = sample_age(latest, now)
        out["vitals"] = {
            "cpu": latest.get("cpu"),
            "ram": latest.get("ram"),
            "swap_gb": latest.get("swap_gb"),
            "load1": latest.get("load1"),
            "disk_free_gb": latest.get("disk_free_gb"),
        }
        t = aegis.trends(rows, cfg, now)
        findings = []
        for det in (aegis.detect_swap_trend, aegis.detect_memory_trend, aegis.detect_load):
            f = det(t, cfg)
            if f:
                findings.append(f)
        try:
            findings.extend(aegis.detect_swarm(t, cfg) or [])
        except Exception:
            pass
        f = aegis.detect_leaks(t, cfg)
        if f:
            findings.append(f)
        out["findings"] = findings
        out["worst"] = worst_severity(findings)
        out["alerts"] = _recent_alerts(3)
    except Exception as e:  # never let a bad sample break the menu bar
        out["error"] = str(e)
    return out


def worst_severity(findings) -> str:
    worst = "ok"
    for f in findings:
        sev = _finding_severity(f)
        if _SEV_RANK.get(sev, 0) > _SEV_RANK.get(worst, -1):
            worst = sev
    return worst


def title_for(vitals: dict, worst: str) -> str:
    """Menu-bar title: quiet when healthy, surfaces the pressure when not. The
    mark never depends on a swap figure being present -- a critical load
    finding on a row without swap must still mark the icon."""
    mark = _SEV_MARK.get(worst, "")
    swap = vitals.get("swap_gb")
    if worst in ("critical", "high"):
        return f"{mark} {swap:.0f}G".strip() if isinstance(swap, (int, float)) else mark
    if worst in ("medium", "low"):
        return mark
    return ""  # healthy -> icon only


def _recent_alerts(n: int) -> list[str]:
    """The last n alert lines, tail-read: the log grows for months and this
    runs on the main thread every tick, so it must not read the whole file."""
    try:
        return [ln.strip() for ln in sentinel._tail_lines(sentinel.ALERTS_LOG, n) if ln.strip()]
    except Exception:
        return []


def menu_lines(snap: dict) -> list[str]:
    """The dropdown body as plain strings (also the unit-tested contract)."""
    v = snap.get("vitals", {})
    lines = [age_text(snap.get("age_s"))]

    def _fmt(label, val, unit=""):
        return f"{label}: {val}{unit}" if val is not None else f"{label}: --"

    lines.append(_fmt("CPU", v.get("cpu"), "%"))
    lines.append(_fmt("RAM", v.get("ram"), "%"))
    sg = v.get("swap_gb")
    lines.append(f"Swap: {sg:.1f} GB" if sg is not None else "Swap: --")
    lines.append(_fmt("Load", v.get("load1")))
    df = v.get("disk_free_gb")
    lines.append(f"Disk free: {df:.0f} GB" if df is not None else "Disk free: --")

    findings = snap.get("findings", [])
    if findings:
        lines.append("--")
        for f in findings:
            lines.append(f"[{_finding_severity(f)}] {_finding_text(f)}")
    else:
        lines.append("--")
        lines.append("All clear -- forge cold")

    alerts = snap.get("alerts", [])
    if alerts:
        lines.append("--")
        lines.append("Recent alerts:")
        lines.extend(f"  {a}" for a in alerts)
    return lines


# ── actions (explicit clicks only; never auto-invoked) ───────────────────

def _notify(title: str, msg: str):
    """Tell the owner. A menu-bar app has no console, so every outcome a CLI
    user would read on stdout goes out as a notification: rumps' centre first,
    the sentinel's notifier (branded applet / osascript) as fallback. Never raises."""
    try:
        if rumps is not None:
            rumps.notification(title, "", msg)
            return
    except Exception:
        pass
    try:
        sentinel._notify(title, msg)
    except Exception:
        pass


def _last_line(text: str) -> str:
    """The last non-empty line of console output, ANSI stripped -- the one-line
    outcome a CLI user would have read last ("Purge failed: sudo needs a password")."""
    lines = [ln.strip() for ln in Text.from_ansi(text or "").plain.splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "done"


def _in_process(args: list[str]):
    """The in-process form of ``macmon <args>`` for the frozen app: the bundle
    has no interpreter to spawn (``sys.executable`` IS the app -- spawning it
    just opened a second AegisForge) and no ``macmon.py``, so each action calls
    the engine function the CLI subcommand would. None for anything else."""
    from . import cleaner, processes   # lazy: keep the menu-bar import light
    table = {
        ("clean", "--all", "-y"): lambda: cleaner.run_cleaner(all_clean=True, force_yes=True),
        ("purge",): processes.purge_ram,
        ("sentinel", "--pause"): sentinel.pause,
        ("sentinel", "--resume"): sentinel.resume,
    }
    return table.get(tuple(args))


def _cli_command(args: list[str]):
    """argv for ``macmon <args>`` outside the frozen app: the installed launcher
    when there is one, else this interpreter on the repo's macmon.py. None when
    neither exists (the caller notifies -- never silent)."""
    exe = sentinel.find_macmon()
    if exe:
        return [exe, *args]
    script = REPO / "macmon.py"
    if script.exists():
        return [sys.executable, str(script), *args]
    return None


def _run_action(args: list[str]):
    """The worker body (synchronous; unit-tested directly). Frozen: in-process.
    Dev: a subprocess. Either way the outcome is reported, never swallowed."""
    label = "macmon " + " ".join(args)
    try:
        if getattr(sys, "frozen", False):
            fn = _in_process(args)
            if fn is None:
                _notify(f"{aegis.BRAND}: action unavailable", f"{label} has no in-app form.")
                return
            with console.capture() as cap:
                fn()
            _notify(aegis.BRAND, f"{label}: {_last_line(cap.get())}")
            return
        cmd = _cli_command(args)
        if cmd is None:
            _notify(f"{aegis.BRAND}: macmon not found",
                    f"Install the CLI (install.sh or pip install -e .) to run {label}.")
            return
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            _notify(f"{aegis.BRAND}: {label} failed", _last_line(r.stderr or r.stdout) or f"exit {r.returncode}")
    except Exception as e:
        _notify(f"{aegis.BRAND}: {label} failed", str(e)[:200] or type(e).__name__)


def _run_cli(args: list[str]):
    """Run a macmon subcommand in the background (dev + frozen safe)."""
    threading.Thread(target=_run_action, args=(list(args),), daemon=True).start()


def _confirm(title: str, message: str, ok: str = "OK") -> bool:
    """Explicit confirmation for a destructive action (a rumps alert with a
    Cancel button). Without rumps there is no dialog to ask in, so: no."""
    if rumps is None:
        return False
    try:
        return rumps.alert(title, message, ok=ok, cancel=True) == 1
    except Exception:
        return False


def open_dashboard(_=None):
    """Open the live TUI dashboard in a Terminal window."""
    exe = sentinel.find_macmon()
    if exe:
        shell = f"{shlex.quote(exe)} dashboard"
    elif not getattr(sys, "frozen", False) and (REPO / "macmon.py").exists():
        shell = f"cd {shlex.quote(str(REPO))} && {shlex.quote(sys.executable)} macmon.py dashboard"
    else:
        _notify(f"{aegis.BRAND}: macmon not found",
                "Install the CLI (install.sh or pip install -e .) to open the dashboard.")
        return
    script = f'tell application "Terminal" to do script "{_applescript_escape(shell)}"'
    try:
        subprocess.Popen(["osascript", "-e", 'tell application "Terminal" to activate',
                          "-e", script])
    except Exception as e:
        _notify(f"{aegis.BRAND}: dashboard failed", str(e)[:200] or type(e).__name__)


def clean_caches(_=None):
    # Destructive => explicit: this dialog is the confirmation that `-y` skips.
    if not _confirm("Clean caches?",
                    "Runs 'macmon clean --all': system, browser, app and user caches go "
                    "to the Trash (nothing is deleted permanently).", ok="Clean"):
        return
    _run_cli(["clean", "--all", "-y"])


def purge_ram(_=None):
    # `macmon purge` runs `sudo -n purge`: it never prompts (there is no GUI
    # password dialog for it) and fails fast unless `macmon sentinel
    # --setup-purge` installed the sudoers rule. The outcome is notified.
    _run_cli(["purge"])


def pause_sentinel(_=None):
    _run_cli(["sentinel", "--pause"])


def resume_sentinel(_=None):
    _run_cli(["sentinel", "--resume"])


# ── the rumps app ────────────────────────────────────────────────────────

def _icon_or_none():
    return str(MENUBAR_ICON) if MENUBAR_ICON.exists() else None


def render_live(menu, snap: dict, live_keys) -> list:
    """Replace the live status block at the top of `menu` with `snap`'s lines.

    rumps keys a MenuItem by its title at insert time and a separator by a
    generated 'SeparatorMenuItem_<n>' name, so no prefix filter can find what
    the previous tick inserted: this deletes exactly the keys it inserted last
    time (`live_keys`) and returns this tick's keys for the next one. Each item
    gets a unique key ('live:<i>') and only THEN its display title -- two
    identical lines would otherwise collide (rumps then holds an NSMenuItem
    its dict no longer tracks) and the key would show as the title."""
    for key in live_keys:
        if key in menu:
            del menu[key]
    keys = []
    for i, ln in enumerate(menu_lines(snap)):
        before = set(menu.keys())
        if ln == "--":
            menu.insert_before(LIVE_ANCHOR, rumps.separator)
        else:
            item = rumps.MenuItem(f"live:{i}")
            menu.insert_before(LIVE_ANCHOR, item)
            item.title = ln[:120]
        keys.extend(set(menu.keys()) - before)
    return keys


if rumps is not None:  # pragma: no cover - requires a GUI session

    class AegisForgeApp(rumps.App):
        def __init__(self):
            icon = _icon_or_none()
            super().__init__(
                aegis.BRAND, title=None if icon else "AF",
                icon=icon, template=True, quit_button=None,
            )
            self.menu = [
                rumps.MenuItem(LIVE_ANCHOR, callback=open_dashboard),
                None,
                rumps.MenuItem("Clean caches", callback=clean_caches),
                rumps.MenuItem("Purge RAM (sudo)", callback=purge_ram),
                None,
                rumps.MenuItem("Pause sentinel", callback=pause_sentinel),
                rumps.MenuItem("Resume sentinel", callback=resume_sentinel),
                None,
                rumps.MenuItem(f"Quit {aegis.BRAND}", callback=rumps.quit_application),
            ]
            self._live_keys: list = []
            self._refresh(None)

        @rumps.timer(REFRESH_S)
        def _refresh(self, _):
            snap = gather()
            self.title = title_for(snap.get("vitals", {}), snap.get("worst", "ok"))
            self._live_keys = render_live(self.menu, snap, self._live_keys)


def main():
    if rumps is None:
        print("AegisForge.app needs the menu-bar extra: pip install -e \".[app]\" "
              "(rumps). The CLI works without it: `macmon sentinel`.", file=sys.stderr)
        return 1
    AegisForgeApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
