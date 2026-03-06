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

DAEMON_PID_FILE = MACMON_DIR / "daemon.pid"
FOCUS_SESSION_FILE = MACMON_DIR / "focus_session.json"
AUTOPILOT_LOG = MACMON_DIR / "autopilot.log"


# ── Autopilot Daemon ─────────────────────────────────────────────────────

def run_autopilot(start: bool = False, stop: bool = False, status: bool = False, log: bool = False):
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


def _start_daemon():
    if DAEMON_PID_FILE.exists():
        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            if psutil.pid_exists(pid):
                console.print(f"[yellow]Daemon already running (PID {pid})[/]")
                return
        except (ValueError, OSError):
            pass

    console.print("[cyan]Starting autopilot daemon...[/]")

    # Fork to background
    try:
        pid = os.fork()
        if pid > 0:
            # Parent
            DAEMON_PID_FILE.write_text(str(pid))
            console.print(f"[green]Autopilot daemon started (PID {pid})[/]")
            console.print(f"[dim]Log: {AUTOPILOT_LOG}[/]")
            log_action("autopilot_start", f"PID {pid}")
            return
    except OSError as e:
        console.print(f"[red]Failed to fork: {e}[/]")
        return

    # Child process — become daemon
    os.setsid()
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError:
        sys.exit(1)

    # Redirect stdout/stderr
    sys.stdout.flush()
    sys.stderr.flush()
    with open(str(AUTOPILOT_LOG), "a") as log_file:
        os.dup2(log_file.fileno(), sys.stdout.fileno())
        os.dup2(log_file.fileno(), sys.stderr.fileno())

    DAEMON_PID_FILE.write_text(str(os.getpid()))

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
    cpu_pct = psutil.cpu_percent(interval=1)
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
            run_cmd(["purge"], sudo=True, timeout=30)
            _record_fire(db, "RAM Critical", "purge")
            send_notification("macmon", "RAM critical! Ran purge.", cfg.get("notifications", {}).get("style", "osascript"))

    # Rule: Kill Zombies
    if zombies > 0:
        if _can_fire(db, "Kill Zombies", 2):
            _autopilot_log(f"Killing {zombies} zombies")
            for p in psutil.process_iter(["pid", "ppid", "status"]):
                try:
                    if p.info["status"] == psutil.STATUS_ZOMBIE:
                        try:
                            parent = psutil.Process(p.info["ppid"])
                            parent.terminate()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            os.kill(p.info["pid"], signal.SIGKILL)
                except (psutil.NoSuchProcess, psutil.AccessDenied, ProcessLookupError):
                    pass
            _record_fire(db, "Kill Zombies", f"killed {zombies}")

    # Rule: Kill Orphans
    if orphan_count > 3:
        if _can_fire(db, "Kill Orphans", 10):
            _autopilot_log(f"Killing {orphan_count} orphans")
            _record_fire(db, "Kill Orphans", f"found {orphan_count}")

    # Rule: Low Disk
    if disk_free_gb < 10:
        if _can_fire(db, "Low Disk", 60):
            msg = f"Low disk! {disk_free_gb:.1f}GB free. Run `macmon gc --all`"
            _autopilot_log(msg)
            send_notification("macmon", msg, cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Low Disk", msg)

    # Rule: CPU Runaway
    for p in psutil.process_iter(["pid", "name", "cpu_percent"]):
        try:
            if (p.info.get("cpu_percent") or 0) > 95:
                if _can_fire(db, f"CPU Runaway {p.info['pid']}", 5):
                    try:
                        proc = psutil.Process(p.info["pid"])
                        proc.nice(15)
                        _autopilot_log(f"Reniced {p.info['name']} (PID {p.info['pid']}) to 15")
                        _record_fire(db, f"CPU Runaway {p.info['pid']}", f"reniced {p.info['name']}")
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
            _record_fire(db, "Browser RAM Hog", msg)

    # Rule: Weekly clean reminder
    last_clean = _get_hours_since(db, "clean")
    if last_clean is None or last_clean > 168:
        if _can_fire(db, "Weekly Clean Reminder", 1440):  # Once per day max
            send_notification("macmon", "7+ days since last clean. Run `macmon clean --all`", cfg.get("notifications", {}).get("style", "osascript"))
            _record_fire(db, "Weekly Clean Reminder", "notified")

    # ── Thermal Rules ───────────────────────────────────────────────────
    _evaluate_thermal_rules(db, cfg)

    # ── Security Rules ──────────────────────────────────────────────────
    _evaluate_security_rules(db, cfg)

    db.close()


def _evaluate_thermal_rules(db, cfg: dict):
    """Thermal management: prevent overheating and excessive fan noise."""
    notify_style = cfg.get("notifications", {}).get("style", "osascript")

    # Estimate CPU temp from load (or real temp if available)
    cpu_pct = psutil.cpu_percent(interval=0)
    estimated_temp = 40 + (cpu_pct / 100) * 55

    try:
        import subprocess
        out = subprocess.run(["osx-cpu-temp"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            estimated_temp = float(out.stdout.strip().replace("°C", "").replace("C", "").strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Rule: Thermal Critical (>92°C) — renice top CPU hogs aggressively
    if estimated_temp > 92:
        if _can_fire(db, "Thermal Critical", 3):
            _autopilot_log(f"THERMAL CRITICAL: {estimated_temp:.0f}°C — renicing top CPU hogs")
            _renice_top_cpu_hogs(priority=15, max_procs=5)
            send_notification("macmon THERMAL", f"CPU at {estimated_temp:.0f}°C! Throttling heavy processes.", notify_style)
            _record_fire(db, "Thermal Critical", f"{estimated_temp:.0f}°C reniced top 5")

    # Rule: Thermal Warning (>82°C) — renice top hog gently
    elif estimated_temp > 82:
        if _can_fire(db, "Thermal Warning", 5):
            _autopilot_log(f"THERMAL WARNING: {estimated_temp:.0f}°C — renicing top CPU hog")
            _renice_top_cpu_hogs(priority=10, max_procs=2)
            _record_fire(db, "Thermal Warning", f"{estimated_temp:.0f}°C reniced top 2")

    # Rule: Sustained high CPU (>85% for this cycle) — purge RAM to reduce pressure
    if cpu_pct > 85:
        mem = psutil.virtual_memory()
        if mem.percent > 75:
            if _can_fire(db, "High Load Purge", 10):
                _autopilot_log(f"High load: CPU {cpu_pct:.0f}% RAM {mem.percent:.0f}% — purging")
                run_cmd(["purge"], sudo=True, timeout=30)
                _record_fire(db, "High Load Purge", f"CPU {cpu_pct:.0f}% RAM {mem.percent:.0f}%")

    # Rule: Fan noise reduction — if CPU load is moderate but sustained,
    # reduce nice of background dev processes to let fans slow down
    load1, _, _ = psutil.getloadavg()
    core_count = psutil.cpu_count() or 1
    if load1 > core_count * 0.9:
        if _can_fire(db, "Fan Reduction", 10):
            _autopilot_log(f"High load average ({load1:.1f}) — renicing background devtools")
            _renice_background_devtools()
            _record_fire(db, "Fan Reduction", f"load {load1:.1f}")


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
                    _record_fire(db, f"Suspicious Port {conn.raddr.port}", msg)
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
                            _record_fire(db, f"Remote Tool {p.info['name']}", msg)
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
                            _record_fire(db, f"Crypto Miner {p.info['pid']}", msg)
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
                    _record_fire(db, f"TmpExec {p.info['pid']}", msg)
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


def _record_fire(db, rule_name: str, details: str):
    cooldown_until = (datetime.now() + timedelta(minutes=5)).isoformat()
    db.execute(
        "INSERT INTO autopilot_log (rule_name, action, details, cooldown_until) VALUES (?, ?, ?, ?)",
        (rule_name, "fired", details, cooldown_until),
    )
    db.commit()


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
        with open(AUTOPILOT_LOG, "a") as f:
            f.write(f"{ts} | {msg}\n")
    except OSError:
        pass


def _stop_daemon():
    if not DAEMON_PID_FILE.exists():
        console.print("[yellow]No daemon running.[/]")
        return

    try:
        pid = int(DAEMON_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Stopped autopilot daemon (PID {pid})[/]")
        log_action("autopilot_stop", f"PID {pid}")
    except (ValueError, ProcessLookupError, PermissionError):
        console.print("[yellow]Daemon process not found. Cleaning up.[/]")

    DAEMON_PID_FILE.unlink(missing_ok=True)


def _show_status():
    running = False
    pid = None

    if DAEMON_PID_FILE.exists():
        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            running = psutil.pid_exists(pid)
        except (ValueError, OSError):
            pass

    if running:
        console.print(f"[green]Autopilot daemon is RUNNING (PID {pid})[/]")
    else:
        console.print("[yellow]Autopilot daemon is NOT running[/]")
        if DAEMON_PID_FILE.exists():
            DAEMON_PID_FILE.unlink(missing_ok=True)

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

def enter_focus():
    cfg = load_config()
    kill_list = cfg.get("focus_mode", {}).get("kill_on_focus", [])
    essential = cfg.get("focus_mode", {}).get("essential_apps", [])

    console.print(Panel("[bold]macmon focus[/] -- Entering Focus Mode", border_style="yellow"))

    killed_apps = []

    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = p.info["name"].lower()
            for app in kill_list:
                if app.lower() in pname:
                    try:
                        # Graceful quit via osascript
                        run_cmd(["osascript", "-e", f'tell application "{app}" to quit'], timeout=5)
                        killed_apps.append(app)
                        console.print(f"  [yellow]Quit {app}[/]")
                    except Exception:
                        pass
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Purge RAM
    console.print("[cyan]Purging RAM...[/]")
    run_cmd(["purge"], sudo=True, timeout=30)

    # Save session
    session = {
        "timestamp": datetime.now().isoformat(),
        "killed_apps": list(set(killed_apps)),
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

    # Disable notifications
    run_cmd(["defaults", "write", "com.apple.notificationcenterui", "doNotDisturb", "-bool", "true"])
    run_cmd(["killall", "NotificationCenter"], timeout=5)
    console.print("[dim]Do Not Disturb enabled.[/]")


def restore_focus():
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
    run_cmd(["defaults", "write", "com.apple.notificationcenterui", "doNotDisturb", "-bool", "false"])
    run_cmd(["killall", "NotificationCenter"], timeout=5)

    FOCUS_SESSION_FILE.unlink(missing_ok=True)
    console.print(f"\n[green bold]Restored {len(apps)} apps. Focus mode ended.[/]")
    log_action("restore", f"restored {len(apps)} apps")
