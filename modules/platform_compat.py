"""Cross-platform abstraction layer for macmon.

macmon began as a macOS-only tool. This module lets it run on Windows and
Linux too: the portable features (process/disk/dupes/network/docker) work
everywhere, while macOS-only features degrade gracefully via require_os().

Everything here is dependency-light (stdlib + psutil) and import-safe on every
platform -- importing this module must never raise, regardless of OS.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt" or sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

if IS_MAC:
    OS_NAME = "macOS"
elif IS_WINDOWS:
    OS_NAME = "Windows"
elif IS_LINUX:
    OS_NAME = "Linux"
else:
    import platform as _p
    OS_NAME = _p.system() or "unknown"


# ── Load average (os.getloadavg is Unix-only) ────────────────────────────

def load_average() -> tuple[float, float, float]:
    """(1m, 5m, 15m) load average.

    Windows has no native load average: fall back to psutil, which emulates it
    with a background sampler, and to a CPU%-scaled estimate as a last resort.
    """
    try:
        return os.getloadavg()          # Unix
    except (AttributeError, OSError):
        pass
    try:
        import psutil
        return psutil.getloadavg()      # psutil emulates this on Windows
    except Exception:
        pass
    try:
        import psutil
        pct = (psutil.cpu_percent(interval=None) or 0.0) / 100.0
        n = psutil.cpu_count(logical=True) or 1
        v = round(pct * n, 2)
        return (v, v, v)
    except Exception:
        return (0.0, 0.0, 0.0)


# ── Feature gating ───────────────────────────────────────────────────────

def require_os(*supported: str) -> str | None:
    """Return an explanatory message if the current OS is not in `supported`
    (e.g. require_os('macOS')), else None. Callers print the message and return."""
    if OS_NAME in supported:
        return None
    want = " or ".join(supported)
    return f"This feature requires {want} (current platform: {OS_NAME})."


# ── Platform directories ─────────────────────────────────────────────────

def cache_dirs() -> list[Path]:
    home = Path.home()
    if IS_MAC:
        return [home / "Library/Caches"]
    if IS_WINDOWS:
        # %LOCALAPPDATA% itself is NOT a cache dir -- it is the Windows
        # equivalent of ~/Library/Application Support and holds real user data
        # (LOCALAPPDATA\Google = Chrome profile, LOCALAPPDATA\Programs = where
        # VS Code is installed). Only return genuine cache locations.
        out = []
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(Path(local) / "Microsoft/Windows/INetCache")
            out.append(Path(local) / "Temp")
        return [p for p in out] or [home / "AppData/Local/Temp"]
    return [Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))]


def temp_dirs() -> list[Path]:
    dirs = [Path(tempfile.gettempdir())]
    if IS_MAC:
        for p in ("/private/tmp",):
            dirs.append(Path(p))
    elif IS_LINUX:
        dirs.append(Path("/tmp"))
    # de-dup while preserving order
    seen, out = set(), []
    for d in dirs:
        if d not in seen and d.exists():
            seen.add(d); out.append(d)
    return out


def log_dirs() -> list[Path]:
    home = Path.home()
    if IS_MAC:
        return [home / "Library/Logs"]
    if IS_WINDOWS:
        return []  # Windows apps log under LOCALAPPDATA (covered by cache_dirs)
    # /var/log is system-owned (under sudo we would delete rotated syslog /
    # journal / audit archives) and ~/.local/state is XDG app state (nvim undo
    # history, tool DBs), not logs. Only the XDG log subdir is safe to sweep.
    return [home / ".local/state/log"]


def app_support_dir() -> Path:
    home = Path.home()
    if IS_MAC:
        return home / "Library/Application Support"
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
    return Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))


# ── Notifications ────────────────────────────────────────────────────────

def _escape_applescript(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _escape_ps(s: str) -> str:
    """Escape a value for a single-quoted PowerShell literal.

    PowerShell escapes a single quote by doubling it. Required because alert
    text embeds data read off the machine (process names, ollama model names)
    and macmon's own alerts contain quotes, e.g. "lancez 'macmon sentinel'".
    """
    return s.replace("'", "''")


def notify(title: str, message: str):
    """Best-effort desktop notification on any platform."""
    try:
        if IS_MAC:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{_escape_applescript(message)}" with title "{_escape_applescript(title)}"'],
                capture_output=True, timeout=5)
        elif IS_WINDOWS:
            # PowerShell balloon/toast via the shell (no external deps).
            # ShowBalloonTip is async: without the sleep, PowerShell exits and
            # disposes the NotifyIcon before the balloon renders (rc=0, so the
            # failure is silent). Hold the process open, then dispose cleanly.
            ps = (
                "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') > $null;"
                "$n = New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon = [System.Drawing.SystemIcons]::Information;"
                "$n.Visible = $true;"
                f"$n.ShowBalloonTip(5000, '{_escape_ps(title)}', '{_escape_ps(message)}', 'Info');"
                "Start-Sleep -Seconds 6;"
                "$n.Dispose();"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=12)
        elif IS_LINUX:
            subprocess.run(["notify-send", title, message], capture_output=True, timeout=5)
    except Exception:
        pass


# ── DNS flush ────────────────────────────────────────────────────────────

def dns_flush_cmds() -> list[list[str]]:
    if IS_MAC:
        return [["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]]
    if IS_WINDOWS:
        return [["ipconfig", "/flushdns"]]
    # linux: depends on resolver; try common ones
    return [["resolvectl", "flush-caches"]]
