"""AegisForge.app -- the local READ side: a tiny loopback HTTP server.

The native window (``run_window``) is a pywebview/WKWebView pointed at this
server (a 127.0.0.1 HTTP origin, never file://, the fleet's Sentinel pattern),
which serves the app page (``assets/webui/``) and its READ-only JSON:

  /api/status     vitals + FORGE findings + alerts (the 60s sampler's rows)
  /api/history    the last n samples (sparklines)
  /api/health     macmon health: score + checks
  /api/processes  the process table (+ a ``protected`` flag per row)
  /api/security   the security scan: score + findings
  /api/docker     overview + containers + images + volumes
  /api/disk       top-level usage of a directory
  /api/bigfiles   the largest files under a directory
  /api/sentinel   sampler state, opt-in flags, thresholds, trends

It is GET-only by design and exposes NO mutating endpoint: a POST here would
be reachable through any DNS-rebinding read, so there is none. ACTIONS (kill,
clean, prune, pause ...) live in ``app_api.Api`` and reach the engine ONLY
over pywebview's ``js_api`` bridge, which exists inside the app window and
nowhere else. Bound to 127.0.0.1 only, never 0.0.0.0, and every request must
carry a loopback ``Host`` header (403 otherwise) so a rebinding page cannot
read the host telemetry. Opened in a plain browser (no bridge) the page is a
read-only dashboard and says so.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

DEFAULT_PORT = 9137  # AegisForge's own port -- never 9101/9102 (fleet Sentinel SOC)
_HOST = "127.0.0.1"
# Host header hostnames a request may carry (port stripped). A DNS-rebinding
# page reaches this port with ITS OWN hostname in Host, which is refused.
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
# The only severities the page's `sev-<x>` class / label may carry; anything
# else (a foreign Finding with a made-up level) is shown as "info".
_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info", "ok"})

# The page. Static files under assets/webui (bundled into the .app by
# build_aegisforge.sh's --add-data "assets:assets"; REPO resolves to the
# PyInstaller bundle root when frozen, exactly like app_menubar.MENUBAR_ICON).
REPO = Path(__file__).resolve().parent.parent
WEBUI_DIR = REPO / "assets" / "webui"
_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/mark.svg": ("mark.svg", "image/svg+xml"),
}
# No remote script/style/font ever: the app is offline and self-contained.
# Scripts come from 'self' only (no inline, no remote). 'unsafe-eval' is
# REQUIRED by pywebview's bridge: it builds every js_api method with
# `new Function(...)` and runs evaluate_js through eval() -- verified on
# WKWebView / macOS 26, where a plain 'self' policy leaves window.pywebview.api
# empty and the funnels dead. It only lets already-running trusted code eval a
# string; the page has no HTML-string sink (see app.js), so no engine data can
# reach one. 'unsafe-inline' on styles is for element.style bar widths.
_CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'")


def _age_label(age_s):
    """Human sample age + stale flag (STALE past 180s -- the sampler is 60s)."""
    if age_s is None:
        return "no sample yet", True
    age_s = int(age_s)
    txt = f"sample {age_s}s ago" if age_s < 90 else f"sample {age_s // 60}m ago"
    stale = age_s > 180
    return (txt + " / STALE" if stale else txt), stale


def _severity(sev) -> str:
    """Server-side whitelist: the page interpolates the severity into a CSS
    class and a label, so only a known token may leave here."""
    sev = str(sev).lower()
    return sev if sev in _SEVERITIES else "info"


def status_dict() -> dict:
    """gather() shaped for JSON: findings as {severity,text}, plus a sample age."""
    from . import app_menubar as mb
    snap = mb.gather()
    findings = [{"severity": _severity(mb._finding_severity(f)), "text": mb._finding_text(f)}
                for f in snap.get("findings", [])]
    label, stale = _age_label(snap.get("age_s"))
    return {
        "vitals": snap.get("vitals", {}),
        "worst": _severity(snap.get("worst", "ok")),
        "findings": findings,
        "alerts": snap.get("alerts", []),
        "age_label": label,
        "stale": stale,
        "error": snap.get("error"),
    }


def status_json() -> bytes:
    """``status_dict()`` as strict JSON. A NaN/Infinity vital would serialize
    as a bare ``NaN`` token, which ``JSON.parse`` rejects -- the page would then
    keep showing the LAST good figures as if live. So: ``allow_nan=False``, and
    the one broken case is reported as an engine error flagged STALE."""
    try:
        return json.dumps(status_dict(), allow_nan=False).encode("utf-8")
    except Exception as e:
        return json.dumps({"error": str(e), "vitals": {}, "findings": [], "alerts": [],
                           "worst": "ok", "age_label": "engine error", "stale": True},
                          allow_nan=False).encode("utf-8")


# ── the read builders (engine -> plain dicts; no mutation anywhere) ───────

_HISTORY_KEYS = ("ts", "cpu", "ram", "swap_gb", "load1", "disk_free_gb")


def history_dict(n: int = 60) -> dict:
    from . import sentinel
    n = max(2, min(int(n or 60), 600))
    rows = sentinel._load_tail(n) or []
    return {"rows": [{k: r.get(k) for k in _HISTORY_KEYS} for r in rows if isinstance(r, dict)]}


def health_dict() -> dict:
    from . import health
    checks = health._run_all_checks()
    return {"score": health._calculate_score(checks), "checks": checks, "at": time.time()}


def processes_dict(sort_by: str = "cpu") -> dict:
    from . import processes
    from .utils import format_size
    sort_by = sort_by if sort_by in ("cpu", "ram", "name", "runtime") else "cpu"
    procs = processes.collect_processes(sort_by=sort_by)
    now = time.time()
    out = []
    for p in procs[:150]:
        out.append({
            "pid": p["pid"], "ppid": p["ppid"], "name": p["name"], "cpu": round(float(p["cpu"] or 0), 1),
            "ram": int(p["ram"] or 0), "ram_label": format_size(int(p["ram"] or 0)),
            "status": p["status"], "user": p["user"], "category": p["category"],
            "created": p["created"], "age_s": int(now - p["created"]) if p["created"] else None,
            "protected": processes._is_protected_target(p["pid"], p["name"]),
        })
    return {"processes": out, "total": len(procs), "sort": sort_by, "at": now}


def security_dict() -> dict:
    from . import security
    from .app_api import engine_call
    from .platform_compat import require_os
    # The checks (csrutil / spctl / fdesetup / launchctl / defaults) are macOS
    # tools: off macOS they all read "could not check" and the tab would show a
    # confident ~95/100 on a box whose firewall/encryption were never inspected.
    # Gate it like the CLI (security.run_security) instead of pretending.
    msg = require_os("macOS")
    if msg:
        return {"score": None, "findings": [], "error": msg, "at": time.time()}
    with engine_call():
        score, findings = security._security_checks()
    return {"score": score, "findings": findings, "at": time.time()}


def docker_dict() -> dict:
    from . import docker_mgr
    from .app_api import engine_call
    with engine_call():
        if not docker_mgr._docker_available():
            return {"available": False, "overview": {}, "containers": [], "images": [], "volumes": []}
    return {"available": True, "overview": docker_mgr._overview_data(),
            "containers": docker_mgr._containers_data(), "images": docker_mgr._images_data(),
            "volumes": docker_mgr._volumes_data(), "at": time.time()}


# At most a couple of full-disk walks at once: /api/disk and /api/bigfiles each
# os.walk a directory tree (seconds to a minute on a large home). Without a bound
# a page firing dozens of these in parallel would make this app the load; the
# semaphore serializes them and a caller that cannot get a slot is told to retry.
_DISK_SEM = threading.BoundedSemaphore(2)


def disk_dict(path: str = "~") -> dict:
    from . import disk
    from .utils import format_size
    base = Path(path or "~").expanduser()
    if not base.is_dir():
        return {"error": f"{base} is not a directory", "path": str(base), "entries": [], "total": 0}
    if not _DISK_SEM.acquire(timeout=45):
        return {"error": "disk scanner busy -- try again", "path": str(base), "entries": [], "total": 0}
    try:
        entries = disk._disk_entries(base)
    except (OSError, PermissionError) as e:
        return {"error": f"Cannot read {base}: {e.__class__.__name__}", "path": str(base), "entries": [], "total": 0}
    finally:
        _DISK_SEM.release()
    total = sum(e["size"] for e in entries)
    for e in entries:
        e["size_label"] = format_size(e["size"])
        e["pct"] = round(e["size"] / total * 100, 1) if total else 0
    return {"path": str(base), "entries": entries[:25], "total": total, "total_label": format_size(total)}


def bigfiles_dict(path: str = "~", min_size: str = "50MB") -> dict:
    from . import disk
    from .utils import format_size
    base = Path(path or "~").expanduser()
    if not base.is_dir():
        return {"error": f"{base} is not a directory", "files": []}
    try:
        min_bytes = disk._parse_size(min_size or "50MB")
    except Exception:
        return {"error": f"invalid size {min_size!r}", "files": []}
    if not _DISK_SEM.acquire(timeout=45):
        return {"error": "disk scanner busy -- try again", "files": []}
    try:
        files = disk._scan_big_files(base, min_bytes)
    finally:
        _DISK_SEM.release()
    for f in files:
        f["size_label"] = format_size(f["size"])
    return {"path": str(base), "min_bytes": min_bytes, "files": files,
            "total": sum(f["size"] for f in files), "total_label": format_size(sum(f["size"] for f in files))}


_THRESHOLD_KEYS = ("swap_used_gb", "ram_pct", "disk_free_gb", "ram_critical", "swap_critical_gb",
                   "trend_window", "swap_eta_min", "ram_eta_min", "load_factor", "load_sustain",
                   "orphan_alert_min", "reap_idle_samples", "fleet_keep")


def sentinel_dict() -> dict:
    from . import aegis, sentinel
    from .app_api import AUTO_KEYS, AUTO_LABELS
    from .platform_compat import IS_MAC
    cfg = sentinel._conf()
    rows = sentinel._load_tail(int(cfg.get("trend_window") or 10) + 2) or []
    now = time.time()
    t = aegis.trends(rows, cfg, now) or {}
    trends = {}
    if t:
        s, r, ld, lk = t.get("swap", {}), t.get("ram", {}), t.get("load", {}), t.get("leaks", {})
        trends = {
            "points": t.get("points"), "span_min": t.get("span_min"),
            "swap": {"gb": s.get("gb"), "slope": s.get("slope"), "eta": s.get("eta"), "crit": s.get("crit")},
            "ram": {"pct": r.get("pct"), "slope": r.get("slope"), "eta": r.get("eta"), "crit": r.get("crit")},
            "load": {"load1": ld.get("load1"), "ncpu": ld.get("ncpu"), "ratio": ld.get("ratio"),
                     "sustained": ld.get("sustained"), "factor": ld.get("factor")},
            "leaks": {"leak": lk.get("leak"), "p1": lk.get("p1"), "names": lk.get("names", [])},
            "swarm": t.get("swarm", {}), "spawn": t.get("spawn"),
        }
    try:
        samples = len(sentinel._load(100000))
    except Exception:
        samples = 0
    sampler = sentinel._scheduler_has(sentinel.MONITOR_LABEL)
    return {
        "sampler_active": sampler,
        "weekly_active": sentinel._scheduler_has(sentinel.WEEKLY_LABEL) if IS_MAC else None,
        "is_mac": IS_MAC,
        "auto": {k: aegis._bool(cfg, k) for k in AUTO_KEYS},
        "auto_labels": dict(AUTO_LABELS),
        "purge_ready": sentinel._purge_nopasswd_ready() if IS_MAC else False,
        "thresholds": {k: cfg.get(k) for k in _THRESHOLD_KEYS},
        "detectors": [
            {"key": "swap_trend", "name": "Swap climbing", "what": "swap high and rising toward the critical line (slope + ETA)"},
            {"key": "ram_trend", "name": "Memory trend", "what": "RAM% rising toward ram_critical within ram_eta_min"},
            {"key": "load", "name": "Load catastrophe", "what": f"load1 above cores x {cfg.get('load_factor')} for {cfg.get('load_sustain')} samples"},
            {"key": "swarm", "name": "Process swarm", "what": "a runaway family (headless browsers, node, python) and its spawner"},
            {"key": "leaks", "name": "Leaked orphans", "what": "dev processes under PID 1 whose parent the sentinel watched die"},
        ],
        "samples": samples, "trends": trends, "at": now,
        "metrics_path": str(sentinel.METRICS), "conf_path": str(sentinel.CONF),
    }


def _json(d) -> bytes:
    try:
        return json.dumps(d, allow_nan=False, default=str).encode("utf-8")
    except Exception as e:
        return json.dumps({"error": str(e)}, allow_nan=False).encode("utf-8")


def host_allowed(host_header) -> bool:
    """True when the request's ``Host`` names this loopback origin. The page
    always fetches with the loopback Host; a DNS-rebinding page carries its own
    hostname (the browser never lets it forge Host), so that is refused."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):                    # bracketed IPv6 literal: never loopback here
        return False
    hostname = host.rsplit(":", 1)[0] if ":" in host else host
    return hostname in _ALLOWED_HOSTS


def fetch_site_allowed(sec_fetch_site) -> bool:
    """Reject a CROSS-SITE request even when its Host is loopback. A page on
    another origin can point ``<img src=http://127.0.0.1:PORT/api/bigfiles?...>``
    at us -- the browser fills a loopback Host, so the Host gate alone passes it
    (no body is readable, but the walk still runs). A modern browser labels that
    ``Sec-Fetch-Site: cross-site``; our own page's fetch is ``same-origin`` and a
    direct navigation is ``none``. A browser that omits the header (old) falls
    back to the Host gate."""
    v = (sec_fetch_site or "").strip().lower()
    return v in ("", "same-origin", "none")


def page_html() -> str:
    """The app page (index.html) as text -- what ``/`` serves."""
    return (WEBUI_DIR / "index.html").read_text(encoding="utf-8")


def _read_static(name: str) -> bytes | None:
    try:
        return (WEBUI_DIR / name).read_bytes()
    except OSError:
        return None


class _Handler(BaseHTTPRequestHandler):
    timeout = 10  # per-connection socket timeout: a stalled client cannot pin a thread

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", _CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _api(self, path: str, q: dict):
        """Route a READ. Returns the JSON body (bytes) or None for 404.
        Every builder is a GET of engine state; none mutates anything."""
        one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
        if path == "/api/status":
            return status_json()
        if path == "/api/history":
            return _json(history_dict(int(one("n", 60) or 60)))
        if path == "/api/health":
            return _json(health_dict())
        if path == "/api/processes":
            return _json(processes_dict(one("sort", "cpu")))
        if path == "/api/security":
            return _json(security_dict())
        if path == "/api/docker":
            return _json(docker_dict())
        if path == "/api/disk":
            return _json(disk_dict(one("path", "~")))
        if path == "/api/bigfiles":
            return _json(bigfiles_dict(one("path", "~"), one("min", "50MB")))
        if path == "/api/sentinel":
            return _json(sentinel_dict())
        return None

    def do_GET(self):
        if not host_allowed(self.headers.get("Host")):
            self._send(403, b"forbidden: loopback host only", "text/plain")
            return
        if not fetch_site_allowed(self.headers.get("Sec-Fetch-Site")):
            self._send(403, b"forbidden: cross-site request", "text/plain")
            return
        path, _, query = self.path.partition("?")
        if path in _STATIC:
            name, ctype = _STATIC[path]
            body = _read_static(name)
            if body is None:
                self._send(500, b"app page missing (assets/webui)", "text/plain")
            else:
                self._send(200, body, ctype)
            return
        if path.startswith("/api/"):
            try:
                body = self._api(path, parse_qs(query))
            except (ValueError, TypeError) as e:
                self._send(400, _json({"error": str(e)}), "application/json")
                return
            except Exception as e:  # an engine failure is reported, never a hung request
                self._send(500, _json({"error": f"{e.__class__.__name__}: {e}"}), "application/json")
                return
            if body is None:
                self._send(404, b"not found", "text/plain")
            else:
                self._send(200, body, "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass  # keep the console quiet


class _Server(ThreadingHTTPServer):
    # ThreadingHTTPServer defaults allow_reuse_address=True. On Windows that makes
    # SO_REUSEADDR behave like SO_REUSEPORT, so binding an already-LISTENING port
    # SUCCEEDS instead of raising -- the port-in-use fallback below would then never
    # trigger and two servers would share 9137. False makes a taken port raise
    # OSError on every platform, so the fallback is consistent (macOS/Linux already
    # raise). Cost: a rapid restart while the port is in TIME_WAIT falls back to an
    # ephemeral port, which is fine -- callers use the returned port, not the literal.
    allow_reuse_address = False
    daemon_threads = True


def _bind(port: int) -> ThreadingHTTPServer:
    """Bind 127.0.0.1:port, falling back to a free ephemeral port if taken."""
    try:
        return _Server((_HOST, port), _Handler)
    except OSError:
        return _Server((_HOST, 0), _Handler)  # 0 -> OS picks a free port


def serve(port: int = DEFAULT_PORT) -> tuple[ThreadingHTTPServer, int]:
    """Start a NEW dashboard server in a daemon thread. Returns (server, port).
    Callers that want the app's single shared server use ``ensure_server``."""
    srv = _bind(port)
    real_port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, real_port


# The ONE server the app runs. Both the native window (``run_window``) and the
# menu-bar fallback (``app_menubar._ensure_web``) go through this cache, so a
# window that fell back to the menu bar never leaves two servers bound.
_shared: tuple[ThreadingHTTPServer, int] | None = None
_shared_lock = threading.Lock()


def ensure_server(port_pref: int = DEFAULT_PORT) -> tuple[ThreadingHTTPServer, int]:
    """Start the shared dashboard server once (idempotent); returns (server, port).
    Raises if it cannot bind (the caller decides how to report that)."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = serve(port_pref)
        return _shared


def stop_server() -> None:
    """Shut the shared server down and release its port (idempotent)."""
    global _shared
    with _shared_lock:
        shared, _shared = _shared, None
    if shared is not None:
        srv, _port = shared
        try:
            srv.shutdown()
        finally:
            srv.server_close()


def url_for(port: int) -> str:
    return f"http://{_HOST}:{port}/"


def open_in_browser(port: int) -> None:
    """Open the dashboard in the default browser. `open` on macOS (reliable from
    a bundled app, per the fleet's Sentinel), webbrowser elsewhere."""
    u = url_for(port)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", u])
        else:
            import webbrowser
            webbrowser.open(u)
    except Exception:
        pass


def run_window(port_pref: int = DEFAULT_PORT, width: int = 1180, height: int = 780) -> bool:
    """Open the NATIVE app window (pywebview/WKWebView) rendering the local
    app -- the fleet Sentinel pattern: a real window, not a browser tab,
    pointed at a 127.0.0.1 HTTP origin (never file://). The window carries
    the ``app_api.Api`` bridge (``js_api``): that bridge, and only it, is how
    the page's funnels reach a mutating engine call; the HTTP server under
    it stays read-only. Blocks on the main thread until the window closes,
    then shuts the shared server down. Returns False (without blocking, and
    without binding a server) if pywebview is unavailable, so the caller can
    fall back to the menu-bar app."""
    try:
        import webview
    except ImportError:
        return False
    from . import aegis
    from .app_api import Api
    _srv, port = ensure_server(port_pref)  # the app's single server (shared with the fallback)
    try:
        webview.create_window(aegis.BRAND, url_for(port), js_api=Api(), width=width, height=height,
                              min_size=(820, 560), background_color=aegis.C["ground"])
        webview.start()  # blocks on the main thread until the window closes
    finally:
        stop_server()   # srv.shutdown() + srv.server_close(): no port left bound
    return True


def _free_port_note() -> int:
    """(used only by tests) a definitely-free port so a fallback can be forced."""
    s = socket.socket()
    s.bind((_HOST, 0))
    p = s.getsockname()[1]
    s.close()
    return p
