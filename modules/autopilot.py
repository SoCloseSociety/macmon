"""Autopilot daemon, focus mode, and rules engine for macmon."""

import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .utils import (
    MACMON_DIR,
    categorize_process,
    confirm_action,
    console,
    format_size,
    get_db,
    log_action,
    run_cmd,
    send_notification,
)
from .platform_compat import load_average, require_os

DAEMON_PID_FILE = MACMON_DIR / "daemon.pid"
FOCUS_SESSION_FILE = MACMON_DIR / "focus_session.json"
AUTOPILOT_LOG = MACMON_DIR / "autopilot.log"
MAX_AUTOPILOT_LOG_SIZE = 5 * 1024 * 1024  # 5 MB


# ── Autopilot Daemon ─────────────────────────────────────────────────────

def run_autopilot(start: bool = False, stop: bool = False, status: bool = False, log: bool = False):
    msg = require_os("macOS")
    if msg:
        console.print(f"[yellow]{msg}[/]")
        return
    if start:
        _start_daemon()
    elif stop:
        _stop_daemon()
    elif status:
        _show_status()
    elif log:
        _tail_log()
    else:
        _show_status()


def _is_macmon_process(pid: int) -> bool:
    """Return True if the PID belongs to a running macmon process."""
    try:
        cmdline = psutil.Process(pid).cmdline()
        return any("macmon" in part for part in cmdline)
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _read_daemon_pid():
    """Read and verify the daemon PID file.

    Returns the PID if it points at a live macmon process; otherwise
    removes the stale file and returns None (PIDs get reused).
    """
    if not DAEMON_PID_FILE.exists():
        return None
    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        return None
    if psutil.pid_exists(pid) and _is_macmon_process(pid):
        return pid
    # Stale or reused PID -- not our daemon
    DAEMON_PID_FILE.unlink(missing_ok=True)
    return None


def _start_daemon():
    pid = _read_daemon_pid()
    if pid is not None:
        console.print(f"[yellow]Daemon already running (PID {pid})[/]")
        return

    console.print("[cyan]Starting autopilot daemon...[/]")

    # Fork to background. NOTE: fork() is only safe because macmon is still
    # single-threaded here -- nothing threaded or ObjC-backed may be imported
    # before daemonizing.
    try:
        pid = os.fork()
        if pid > 0:
            # Parent -- the real daemon (grandchild) writes its own PID file.
            # Wait up to ~2s for it to appear.
            daemon_pid = None
            for _ in range(20):
                time.sleep(0.1)
                if DAEMON_PID_FILE.exists():
                    try:
                        daemon_pid = int(DAEMON_PID_FILE.read_text().strip())
                        break
                    except (ValueError, OSError):
                        pass
            if daemon_pid:
                console.print(f"[green]Autopilot daemon started (PID {daemon_pid})[/]")
                console.print(f"[dim]Log: {AUTOPILOT_LOG}[/]")
                log_action("autopilot_start", f"PID {daemon_pid}")
            else:
                console.print(f"[red]Daemon did not start (no PID file). Check {AUTOPILOT_LOG}[/]")
            return
    except OSError as e:
        console.print(f"[red]Failed to fork: {e}[/]")
        return

    # First child -- new session, then fork again so the daemon is a grandchild
    os.setsid()
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    # Grandchild -- the daemon. Detach from the launch directory.
    os.chdir("/")

    # Redirect stdin from /dev/null, stdout/stderr to the log
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull) as devnull:
        os.dup2(devnull.fileno(), sys.stdin.fileno())
    with open(str(AUTOPILOT_LOG), "a") as log_file:
        os.dup2(log_file.fileno(), sys.stdout.fileno())
        os.dup2(log_file.fileno(), sys.stderr.fileno())

    # Write our own PID exclusively -- guards against concurrent double-start
    try:
        fd = os.open(str(DAEMON_PID_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        _autopilot_log("Another daemon already owns the PID file -- exiting")
        sys.exit(1)
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))

    # Signal handler for clean shutdown
    def _shutdown(signum, frame):
        DAEMON_PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Main daemon loop
    _daemon_loop()


def _daemon_loop():
    cfg = load_config()
    interval = cfg.get("autopilot", {}).get("interval_seconds", 30)

    _prune_autopilot_db()

    while True:
        try:
            cfg = load_config()
            if not cfg.get("autopilot", {}).get("enabled", True):
                time.sleep(interval)
                continue

            _evaluate_rules(cfg)
            time.sleep(interval)
        except Exception as e:
            _autopilot_log(f"Error in daemon loop: {e}")
            time.sleep(interval)


def _evaluate_rules(cfg: dict):
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    disk_free_gb = disk.free / (1024**3)

    zombies = sum(1 for p in psutil.process_iter(["status"]) if p.info.get("status") == psutil.STATUS_ZOMBIE)
    orphan_count = sum(
        1 for p in psutil.process_iter(["ppid", "name"])
        if p.info.get("ppid") == 1 and categorize_process(p.info.get("name", "")) in {"llm", "ide", "node", "python", "build"}
    )

    db = get_db()

    # Rule: RAM Critical
    if mem.percent > 88:
        if _can_fire(db, "RAM Critical", 5):
            _autopilot_log("RAM Critical: running purge")
            if _daemon_purge(db, cfg):
                _record_fire(db, "RAM Critical", "purge", 5)
                send_notification("macmon", "RAM critical! Ran purge.", cfg.get("notifications", {}).get("style", "osascript"))
            else:
                _record_fire(db, "RAM Critical", "purge failed", 5)

    # Rule: Kill Zombies -- nudge parents to reap, never terminate them
    if zombies > 0:
        if _can_fire(db, "Kill Zombies", 2):
            _autopilot_log(f"Found {zombies} zombies -- signaling parents to reap")
            for p in psutil.process_iter(["pid", "ppid", "status"]):
                try:
                    if p.info["status"] == psutil.STATUS_ZOMBIE:
                        ppid = p.info.get("ppid") or 0
                        if ppid > 1:
                            os.kill(ppid, signal.SIGCHLD)
                except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError, PermissionError):
                    pass
            send_notification("macmon", f"{zombies} zombies found, run `macmon sweep`", cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Kill Zombies", f"found {zombies}, notified", 2)

    # Rule: Kill Orphans -- detection only, suggest `macmon sweep`
    if orphan_count > 3:
        if _can_fire(db, "Kill Orphans", 10):
            _autopilot_log(f"Detected {orphan_count} orphans")
            send_notification("macmon", f"{orphan_count} orphans detected, run `macmon sweep`", cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Kill Orphans", f"detected {orphan_count}", 10)

    # Rule: Low Disk
    if disk_free_gb < 10:
        if _can_fire(db, "Low Disk", 60):
            msg = f"Low disk! {disk_free_gb:.1f}GB free. Run `macmon gc --all`"
            _autopilot_log(msg)
            send_notification("macmon", msg, cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Low Disk", msg, 60)

    # Rule: CPU Runaway
    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            if (p.info.get("cpu_percent") or 0) > 95:
                if _can_fire(db, f"CPU Runaway {p.info['pid']}", 5):
                    try:
                        proc = psutil.Process(p.info["pid"])
                        proc.nice(15)
                        _autopilot_log(f"Reniced {p.info['name']} (PID {p.info['pid']}) to 15")
                        _record_fire(db, f"CPU Runaway {p.info['pid']}", f"reniced {p.info['name']}", 5)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Rule: Browser RAM Hog
    browser_ram = 0
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            if categorize_process(p.info.get("name", "")) == "browser":
                browser_ram += (p.info.get("memory_info").rss if p.info.get("memory_info") else 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if browser_ram > 4 * 1024**3:  # 4GB
        if _can_fire(db, "Browser RAM Hog", 30):
            msg = f"Browser using {format_size(browser_ram)} RAM -- close some tabs"
            send_notification("macmon", msg, cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Browser RAM Hog", msg, 30)

    # Rule: Weekly clean reminder
    last_clean = _get_hours_since(db, "clean")
    if last_clean is None or last_clean > 168:
        if _can_fire(db, "Weekly Clean Reminder", 1440):  # Once per day max
            send_notification("macmon", "7+ days since last clean. Run `macmon clean --all`", cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Weekly Clean Reminder", "notified", 1440)

    # ── Thermal Rules ───────────────────────────────────────────────────
    _evaluate_thermal_rules(db, cfg)

    # ── Security Rules ──────────────────────────────────────────────────
    _evaluate_security_rules(db, cfg)

    db.close()


def _evaluate_thermal_rules(db, cfg: dict):
    """Thermal management: prevent overheating and excessive fan noise."""
    notify_style = cfg.get("notifications", {}).get("style", "osascript")

    # Only act on a REAL sensor reading -- a load-derived estimate would
    # renice legitimate heavy workloads (builds, exports) on healthy machines
    cpu_pct = psutil.cpu_percent(interval=0)
    real_temp = None

    try:
        import subprocess
        out = subprocess.run(["osx-cpu-temp"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            parsed = float(out.stdout.strip().replace("°C", "").replace("C", "").strip())
            if parsed > 0:  # 0.0 means no sensor (Apple Silicon)
                real_temp = parsed
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Rule: Thermal Critical (>92°C) -- renice top CPU hogs aggressively
    if real_temp is not None and real_temp > 92:
        if _can_fire(db, "Thermal Critical", 3):
            _autopilot_log(f"THERMAL CRITICAL: {real_temp:.0f}°C -- renicing top CPU hogs")
            _renice_top_cpu_hogs(priority=15, max_procs=5)
            send_notification("macmon THERMAL", f"CPU at {real_temp:.0f}°C! Throttling heavy processes.", notify_style)
            _record_fire(db, "Thermal Critical", f"{real_temp:.0f}°C reniced top 5", 3)

    # Rule: Thermal Warning (>82°C) -- renice top hog gently
    elif real_temp is not None and real_temp > 82:
        if _can_fire(db, "Thermal Warning", 5):
            _autopilot_log(f"THERMAL WARNING: {real_temp:.0f}°C -- renicing top CPU hog")
            _renice_top_cpu_hogs(priority=10, max_procs=2)
            _record_fire(db, "Thermal Warning", f"{real_temp:.0f}°C reniced top 2", 5)

    # Rule: Sustained high CPU (>85% for this cycle) -- purge RAM to reduce pressure
    if cpu_pct > 85:
        mem = psutil.virtual_memory()
        if mem.percent > 75:
            if _can_fire(db, "High Load Purge", 10):
                _autopilot_log(f"High load: CPU {cpu_pct:.0f}% RAM {mem.percent:.0f}% -- purging")
                if _daemon_purge(db, cfg):
                    _record_fire(db, "High Load Purge", f"CPU {cpu_pct:.0f}% RAM {mem.percent:.0f}%", 10)
                else:
                    _record_fire(db, "High Load Purge", "purge failed", 10)

    # Rule: Fan noise reduction -- if CPU load is moderate but sustained,
    # reduce nice of background dev processes to let fans slow down
    load1, _, _ = load_average()
    core_count = psutil.cpu_count() or 1
    if load1 > core_count * 0.9:
        if _can_fire(db, "Fan Reduction", 10):
            _autopilot_log(f"High load average ({load1:.1f}) -- renicing background devtools")
            _renice_background_devtools()
            _record_fire(db, "Fan Reduction", f"load {load1:.1f}", 10)


def _renice_top_cpu_hogs(priority: int = 10, max_procs: int = 3):
    """Renice the top CPU-consuming non-system processes."""
    PROTECTED = {"kernel_task", "WindowServer", "loginwindow", "launchd", "Finder", "Dock", "SystemUIServer"}
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "username"]):
        try:
            info = p.info
            if (info.get("cpu_percent") or 0) > 20 and info.get("username") != "root":
                if info["name"] not in PROTECTED:
                    procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda p: p.info.get("cpu_percent", 0), reverse=True)
    for p in procs[:max_procs]:
        try:
            old_nice = p.nice()
            if old_nice < priority:
                p.nice(priority)
                _autopilot_log(f"  Reniced {p.info['name']} (PID {p.info['pid']}) {old_nice} -> {priority}")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass


def _renice_background_devtools():
    """Gently renice dev background processes (build tools, watchers)."""
    DEV_KEYWORDS = {"webpack", "esbuild", "tsc", "vite", "turbo", "jest", "cargo", "rustc", "gradle", "maven"}
    for p in psutil.process_iter(["pid", "name", "nice"]):
        try:
            pname = p.info["name"].lower()
            if any(kw in pname for kw in DEV_KEYWORDS):
                if (p.info.get("nice") or 0) < 5:
                    p.nice(5)
                    _autopilot_log(f"  Reniced devtool {p.info['name']} (PID {p.info['pid']}) to 5")
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue


def _evaluate_security_rules(db, cfg: dict):
    """Security-focused autopilot rules."""
    notify_style = cfg.get("notifications", {}).get("style", "osascript")

    # Rule: Detect suspicious network connections on known bad ports
    try:
        from .security import SUSPICIOUS_PORTS
        for conn in psutil.net_connections(kind="inet"):
            if conn.raddr and conn.raddr.port in SUSPICIOUS_PORTS:
                port_desc = SUSPICIOUS_PORTS[conn.raddr.port]
                if _can_fire(db, f"Suspicious Port {conn.raddr.port}", 10):
                    msg = f"Suspicious connection to port {conn.raddr.port} ({port_desc}) -> {conn.raddr.ip}"
                    _autopilot_log(msg)
                    send_notification("macmon SECURITY", msg, notify_style)
                    _record_fire(db, f"Suspicious Port {conn.raddr.port}", msg, 10)
    except (psutil.AccessDenied, PermissionError, ImportError):
        pass

    # Rule: Detect remote access tools
    try:
        from .security import KNOWN_REMOTE_TOOLS
        for p in psutil.process_iter(["pid", "name"]):
            try:
                pname = p.info["name"].lower()
                for tool in KNOWN_REMOTE_TOOLS:
                    if tool in pname:
                        if _can_fire(db, f"Remote Tool {p.info['name']}", 60):
                            msg = f"Remote access tool detected: {p.info['name']} (PID {p.info['pid']})"
                            _autopilot_log(msg)
                            send_notification("macmon SECURITY", msg, notify_style)
                            _record_fire(db, f"Remote Tool {p.info['name']}", msg, 60)
                        break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    # Rule: Detect crypto miners (high CPU + suspicious name/cmdline)
    try:
        from .security import SUSPICIOUS_PROCESS_NAMES
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "cmdline"]):
            try:
                cpu = p.info.get("cpu_percent") or 0
                if cpu > 80:
                    pname = p.info["name"].lower()
                    cmdline = " ".join(str(c) for c in (p.info.get("cmdline") or [])).lower()
                    if any(kw in pname or kw in cmdline for kw in ["xmrig", "minerd", "cryptonight", "stratum+tcp"]):
                        if _can_fire(db, f"Crypto Miner {p.info['pid']}", 5):
                            msg = f"Possible crypto miner: {p.info['name']} (PID {p.info['pid']}, CPU {cpu:.0f}%)"
                            _autopilot_log(msg)
                            send_notification("macmon SECURITY", msg, notify_style)
                            _record_fire(db, f"Crypto Miner {p.info['pid']}", msg, 5)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass

    # Rule: Detect processes running from /tmp
    for p in psutil.process_iter(["pid", "name", "exe"]):
        try:
            exe = p.info.get("exe") or ""
            if exe and ("/tmp/" in exe or "/var/tmp/" in exe):
                if _can_fire(db, f"TmpExec {p.info['pid']}", 30):
                    msg = f"Process running from temp dir: {p.info['name']} ({exe})"
                    _autopilot_log(msg)
                    send_notification("macmon SECURITY", msg, notify_style)
                    _record_fire(db, f"TmpExec {p.info['pid']}", msg, 30)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _can_fire(db, rule_name: str, cooldown_minutes: int) -> bool:
    row = db.execute(
        "SELECT cooldown_until FROM autopilot_log WHERE rule_name=? ORDER BY id DESC LIMIT 1",
        (rule_name,),
    ).fetchone()
    if row and row["cooldown_until"]:
        try:
            until = datetime.fromisoformat(row["cooldown_until"])
            if datetime.now() < until:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _record_fire(db, rule_name: str, details: str, cooldown_minutes: int = 5):
    cooldown_until = (datetime.now() + timedelta(minutes=cooldown_minutes)).isoformat()
    db.execute(
        "INSERT INTO autopilot_log (rule_name, action, details, cooldown_until) VALUES (?, ?, ?, ?)",
        (rule_name, "fired", details, cooldown_until),
    )
    db.commit()


def _daemon_purge(db, cfg: dict) -> bool:
    """Run purge non-interactively (sudo -n). Returns True on success.

    The daemon has no TTY, so a plain `sudo purge` would hang or fail
    silently. On failure, log it and notify once per day that passwordless
    sudo is required -- never claim success.
    """
    _, err, rc = run_cmd(["sudo", "-n", "purge"], timeout=30)
    if rc == 0:
        return True
    _autopilot_log(f"purge failed (rc={rc}): {err.strip() or 'passwordless sudo required'}")
    if _can_fire(db, "Purge Sudo Warning", 1440):
        style = cfg.get("notifications", {}).get("style", "osascript")
        send_notification("macmon", "Autopilot cannot run `purge` -- passwordless sudo required.", style)
        _record_fire(db, "Purge Sudo Warning", "notified", 1440)
    return False


def _prune_autopilot_db():
    """Prune autopilot_log rows older than 30 days (run once per daemon start)."""
    try:
        db = get_db()
        db.execute("DELETE FROM autopilot_log WHERE timestamp < datetime('now', '-30 days')")
        db.commit()
        db.close()
    except Exception as e:
        _autopilot_log(f"autopilot_log prune failed: {e}")


def _get_hours_since(db, scan_type: str):
    row = db.execute(
        "SELECT timestamp FROM scan_history WHERE scan_type=? ORDER BY id DESC LIMIT 1",
        (scan_type,),
    ).fetchone()
    if row:
        try:
            dt = datetime.fromisoformat(row["timestamp"])
            return (datetime.now() - dt).total_seconds() / 3600
        except (ValueError, TypeError):
            pass
    return None


def _autopilot_log(msg: str):
    ts = datetime.now().isoformat()
    try:
        if AUTOPILOT_LOG.exists() and AUTOPILOT_LOG.stat().st_size > MAX_AUTOPILOT_LOG_SIZE:
            AUTOPILOT_LOG.replace(AUTOPILOT_LOG.with_name(AUTOPILOT_LOG.name + ".1"))
        with open(AUTOPILOT_LOG, "a") as f:
            f.write(f"{ts} | {msg}\n")
    except OSError:
        pass


def _stop_daemon():
    pid = _read_daemon_pid()
    if pid is None:
        console.print("[yellow]No daemon running.[/]")
        return

    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Stopped autopilot daemon (PID {pid})[/]")
        log_action("autopilot_stop", f"PID {pid}")
    except (ProcessLookupError, PermissionError):
        console.print("[yellow]Daemon process not found. Cleaning up.[/]")

    DAEMON_PID_FILE.unlink(missing_ok=True)


def _show_status():
    pid = _read_daemon_pid()

    if pid is not None:
        console.print(f"[green]Autopilot daemon is RUNNING (PID {pid})[/]")
    else:
        console.print("[yellow]Autopilot daemon is NOT running[/]")

    # Show last 10 actions
    try:
        db = get_db()
        rows = db.execute(
            "SELECT timestamp, rule_name, action, details FROM autopilot_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        db.close()

        if rows:
            table = Table(title="Last 10 Autopilot Actions", border_style="cyan")
            table.add_column("Time", width=20)
            table.add_column("Rule", width=25)
            table.add_column("Details", width=35)
            for r in rows:
                table.add_row(r["timestamp"], r["rule_name"], r["details"] or "")
            console.print(table)
    except Exception:
        pass


def _tail_log():
    if not AUTOPILOT_LOG.exists():
        console.print("[dim]No autopilot log found.[/]")
        return
    lines = AUTOPILOT_LOG.read_text().splitlines()
    console.print(Panel("\n".join(lines[-30:]), title="Autopilot Log (last 30)", border_style="dim"))


# ── Focus Mode ───────────────────────────────────────────────────────────

def _toggle_dnd(cfg: dict, enable: bool):
    """Toggle Do Not Disturb via a user-configured Shortcut.

    `defaults write com.apple.notificationcenterui doNotDisturb` has not
    worked since macOS 12, so we run a Shortcuts shortcut named in config
    (focus_mode.dnd_shortcut, default none). If none is configured or it
    fails, hint that DND must be toggled manually -- never claim success.
    """
    shortcut = cfg.get("focus_mode", {}).get("dnd_shortcut", "")
    action = "enable" if enable else "disable"
    if shortcut:
        _, err, rc = run_cmd(["shortcuts", "run", shortcut], timeout=10)
        if rc == 0:
            console.print(f"[dim]Ran DND shortcut '{shortcut}'.[/]")
            return
        console.print(f"[dim]DND shortcut '{shortcut}' failed: {err.strip() or 'unknown error'}[/]")
    console.print(f"[dim]Toggle Do Not Disturb manually to {action} it (set focus_mode.dnd_shortcut in config to automate).[/]")


def enter_focus():
    msg = require_os("macOS")
    if msg:
        console.print(f"[yellow]{msg}[/]")
        return
    cfg = load_config()
    kill_list = cfg.get("focus_mode", {}).get("kill_on_focus", [])
    essential = cfg.get("focus_mode", {}).get("essential_apps", [])

    console.print(Panel("[bold]macmon focus[/] -- Entering Focus Mode", border_style="yellow"))

    killed_apps = []

    # Build the set of matched apps first, then quit each once
    to_quit = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = p.info["name"].lower()
            if any(ess.lower() in pname for ess in essential):
                continue
            for app in kill_list:
                if app.lower() in pname:
                    if app not in to_quit:
                        to_quit.append(app)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for app in to_quit:
        # Graceful quit via osascript; escape quotes/backslashes for AppleScript
        safe_app = app.replace("\\", "\\\\").replace('"', '\\"')
        _, _, rc = run_cmd(["osascript", "-e", f'tell application "{safe_app}" to quit'], timeout=5)
        if rc == 0:
            killed_apps.append(app)
            console.print(f"  [yellow]Quit {app}[/]")
        else:
            console.print(f"  [dim]Could not quit {app}[/]")

    # Purge RAM
    console.print("[cyan]Purging RAM...[/]")
    run_cmd(["-n", "purge"], sudo=True, timeout=30)  # sudo -n: fail fast, never hang on an invisible password prompt

    # Save session -- merge with any existing session so running focus
    # twice does not destroy the restore list
    previous = []
    if FOCUS_SESSION_FILE.exists():
        try:
            previous = json.loads(FOCUS_SESSION_FILE.read_text()).get("killed_apps", [])
        except (json.JSONDecodeError, OSError):
            previous = []
    session = {
        "timestamp": datetime.now().isoformat(),
        "killed_apps": sorted(set(previous) | set(killed_apps)),
    }
    FOCUS_SESSION_FILE.write_text(json.dumps(session, indent=2))

    # Save to DB
    try:
        db = get_db()
        db.execute(
            "INSERT INTO focus_session (killed_apps) VALUES (?)",
            (json.dumps(killed_apps),),
        )
        db.commit()
        db.close()
    except Exception:
        pass

    console.print(f"\n[green bold]Focus mode active. Quit {len(set(killed_apps))} apps.[/]")
    console.print("[dim]Run `macmon restore` to reopen them.[/]")
    log_action("focus", f"killed {len(set(killed_apps))} apps")

    # Do Not Disturb
    _toggle_dnd(cfg, enable=True)


def restore_focus():
    msg = require_os("macOS")
    if msg:
        console.print(f"[yellow]{msg}[/]")
        return
    if not FOCUS_SESSION_FILE.exists():
        console.print("[yellow]No focus session found.[/]")
        return

    session = json.loads(FOCUS_SESSION_FILE.read_text())
    apps = session.get("killed_apps", [])

    if not apps:
        console.print("[dim]No apps to restore.[/]")
        return

    console.print(Panel("[bold]macmon restore[/] -- Restoring Apps", border_style="green"))

    for app in apps:
        run_cmd(["open", "-a", app], timeout=5)
        console.print(f"  [green]Reopened {app}[/]")

    # Re-enable notifications
    _toggle_dnd(load_config(), enable=False)

    FOCUS_SESSION_FILE.unlink(missing_ok=True)
    console.print(f"\n[green bold]Restored {len(apps)} apps. Focus mode ended.[/]")
    log_action("restore", f"restored {len(apps)} apps")
