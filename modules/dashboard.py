"""Live system dashboard for macmon — rich visual TUI."""

import os
import select
import subprocess
import sys
import termios
import threading
import tty
import time
from datetime import datetime

import psutil
from rich.align import Align
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import load_config
from .utils import (
    CATEGORY_EMOJI,
    categorize_process,
    console,
    format_duration,
    format_size,
    get_db,
    run_cmd,
    smart_suggestions,
)

# ── Cached state (refreshed in background) ──────────────────────────────
_security_cache = {"score": None, "findings": [], "last_update": 0}
_docker_cache = {"running": [], "stopped": 0, "images": 0, "volumes": 0, "available": None, "last_update": 0}
_thermal_cache = {"cpu_temp": None, "fan_speed": None, "gpu_temp": None, "throttled": False, "last_update": 0}

# ── Rate tracking ───────────────────────────────────────────────────────
_prev_net = None
_prev_net_time = 0
_prev_disk_io = None
_prev_disk_time = 0
_net_history = {"up": [0]*30, "down": [0]*30}
_cpu_history = [0]*60
_temp_history = [0]*30

# ── Action status message (shown in footer) ─────────────────────────────
_action_status = {"msg": "", "time": 0}

# ── Top processes cache for 1-9 kill ────────────────────────────────────
_top_procs = []


def _spark(values, width=20):
    blocks = " _.:oO@#"
    if not values:
        return ""
    mn, mx = min(values), max(values)
    rng = mx - mn if mx > mn else 1
    return "".join(blocks[min(len(blocks)-1, int((v - mn) / rng * (len(blocks)-1)))] for v in values[-width:])


def _bar(pct, width=20, color="green"):
    filled = int(pct / 100 * width)
    bar = "\u2588" * filled + "\u2591" * (width - filled)
    if pct > 90:
        color = "red bold"
    elif pct > 70:
        color = "yellow"
    return f"[{color}]{bar}[/] {pct:.1f}%"


def _set_status(msg):
    global _action_status
    _action_status = {"msg": msg, "time": time.time()}


def _get_net_rate():
    global _prev_net, _prev_net_time
    net = psutil.net_io_counters()
    now = time.time()
    up_rate = down_rate = 0
    if _prev_net and (now - _prev_net_time) > 0:
        dt = now - _prev_net_time
        up_rate = (net.bytes_sent - _prev_net.bytes_sent) / dt
        down_rate = (net.bytes_recv - _prev_net.bytes_recv) / dt
    _prev_net = net
    _prev_net_time = now
    _net_history["up"].append(up_rate)
    _net_history["down"].append(down_rate)
    _net_history["up"] = _net_history["up"][-30:]
    _net_history["down"] = _net_history["down"][-30:]
    return up_rate, down_rate, net.bytes_sent, net.bytes_recv


def _get_disk_rate():
    global _prev_disk_io, _prev_disk_time
    try:
        dio = psutil.disk_io_counters()
    except Exception:
        return 0, 0
    now = time.time()
    r_rate = w_rate = 0
    if _prev_disk_io and (now - _prev_disk_time) > 0:
        dt = now - _prev_disk_time
        r_rate = (dio.read_bytes - _prev_disk_io.read_bytes) / dt
        w_rate = (dio.write_bytes - _prev_disk_io.write_bytes) / dt
    _prev_disk_io = dio
    _prev_disk_time = now
    return r_rate, w_rate


def _get_battery_info() -> dict:
    bat = psutil.sensors_battery()
    if bat is None:
        return {}
    return {
        "percent": bat.percent,
        "charging": bat.power_plugged,
        "secs_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else -1,
    }


# ── Thermal monitoring ──────────────────────────────────────────────────

def _refresh_thermal_cache():
    global _thermal_cache
    now = time.time()
    if now - _thermal_cache["last_update"] < 5:
        return
    try:
        # macOS: use powermetrics or IOKit via subprocess
        # Try smc-based approach first (faster)
        temp = _get_cpu_temp_osx()
        fan = _get_fan_speed_osx()
        _thermal_cache["cpu_temp"] = temp
        _thermal_cache["fan_speed"] = fan
        _thermal_cache["throttled"] = (temp or 0) > 90
        if temp:
            _temp_history.append(temp)
            if len(_temp_history) > 30:
                _temp_history.pop(0)
        _thermal_cache["last_update"] = now
    except Exception:
        pass


def _get_cpu_temp_osx():
    """Get CPU temp via multiple methods on macOS."""
    # Method 1: try osx-cpu-temp if installed
    try:
        out = subprocess.run(["osx-cpu-temp"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            # e.g. "65.2°C"
            val = out.stdout.strip().replace("°C", "").replace("C", "").strip()
            return float(val)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Method 2: try istats if installed
    try:
        out = subprocess.run(["istats", "cpu", "temp", "--value-only"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Method 3: estimate from CPU usage (rough heuristic)
    cpu = psutil.cpu_percent(interval=0)
    # Rough estimation: idle ~40°C, full load ~95°C
    return 40 + (cpu / 100) * 55


def _get_fan_speed_osx():
    """Get fan speed via istats or estimation."""
    try:
        out = subprocess.run(["istats", "fan", "speed", "--value-only"], capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            lines = out.stdout.strip().splitlines()
            return int(float(lines[0]))
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, IndexError):
        pass
    # Estimate from CPU - typical MacBook: 0-6200 RPM
    cpu = psutil.cpu_percent(interval=0)
    if cpu < 20:
        return 0
    return int((cpu / 100) * 5500)


# ── Security cache ──────────────────────────────────────────────────────

def _refresh_security_cache():
    global _security_cache
    now = time.time()
    if now - _security_cache["last_update"] < 15:
        return
    try:
        findings = []
        score = 100

        out, _, rc = run_cmd(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"], timeout=3)
        if not (rc == 0 and "enabled" in (out or "").lower()):
            findings.append(("[yellow]WARN[/]", "Firewall off/unknown"))
            score -= 10

        out, _, rc = run_cmd(["csrutil", "status"], timeout=3)
        if not ("enabled" in (out or "").lower()):
            findings.append(("[red]FAIL[/]", "SIP disabled"))
            score -= 15

        try:
            from .security import SUSPICIOUS_PORTS
            for conn in psutil.net_connections(kind="inet"):
                if conn.raddr and conn.raddr.port in SUSPICIOUS_PORTS:
                    desc = SUSPICIOUS_PORTS[conn.raddr.port]
                    findings.append(("[red]ALERT[/]", f"Port {conn.raddr.port} ({desc})"))
                    score -= 10
        except (psutil.AccessDenied, PermissionError, ImportError):
            pass

        try:
            from .security import KNOWN_REMOTE_TOOLS
            for p in psutil.process_iter(["name"]):
                try:
                    pname = p.info["name"].lower()
                    for tool in KNOWN_REMOTE_TOOLS:
                        if tool in pname:
                            findings.append(("[yellow]WARN[/]", f"Remote: {p.info['name']}"))
                            score -= 5
                            break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            pass

        try:
            conns = psutil.net_connections(kind="inet")
            _security_cache["established"] = sum(1 for c in conns if c.status == "ESTABLISHED")
            _security_cache["listening"] = sum(1 for c in conns if c.status == "LISTEN")
        except (psutil.AccessDenied, PermissionError):
            _security_cache["established"] = "?"
            _security_cache["listening"] = "?"

        _security_cache["score"] = max(0, score)
        _security_cache["findings"] = findings[:5]
        _security_cache["last_update"] = now
    except Exception:
        pass


# ── Docker cache ────────────────────────────────────────────────────────

def _refresh_docker_cache():
    global _docker_cache
    now = time.time()
    if now - _docker_cache["last_update"] < 20:
        return
    try:
        _, _, rc = run_cmd(["docker", "info"], timeout=3)
        if rc != 0:
            _docker_cache["available"] = False
            _docker_cache["last_update"] = now
            return

        _docker_cache["available"] = True

        out, _, rc = run_cmd(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"], timeout=5)
        running = []
        if rc == 0 and out.strip():
            for line in out.strip().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    running.append({"name": parts[0][:18], "image": parts[1][:20], "status": parts[2][:15]})
        _docker_cache["running"] = running

        out, _, rc = run_cmd(["docker", "ps", "-a", "--filter", "status=exited", "-q"], timeout=5)
        _docker_cache["stopped"] = len(out.strip().splitlines()) if rc == 0 and out.strip() else 0

        out, _, rc = run_cmd(["docker", "images", "-q"], timeout=5)
        _docker_cache["images"] = len(out.strip().splitlines()) if rc == 0 and out.strip() else 0

        out, _, rc = run_cmd(["docker", "images", "-f", "dangling=true", "-q"], timeout=5)
        _docker_cache["dangling"] = len(out.strip().splitlines()) if rc == 0 and out.strip() else 0

        out, _, rc = run_cmd(["docker", "volume", "ls", "-q"], timeout=5)
        _docker_cache["volumes"] = len(out.strip().splitlines()) if rc == 0 and out.strip() else 0

        _docker_cache["last_update"] = now
    except Exception:
        pass


# ── Panel builders ──────────────────────────────────────────────────────

def _build_header() -> Panel:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    uptime = format_duration(time.time() - psutil.boot_time())
    load1, load5, load15 = psutil.getloadavg()

    header = Text()
    header.append("  MACMON ", style="bold white on blue")
    header.append("  ", style="")
    header.append(f" {now} ", style="bold cyan")
    header.append(f"  Up: {uptime} ", style="dim")
    header.append(f"  Load: {load1:.1f}/{load5:.1f}/{load15:.1f} ", style="dim")

    return Panel(Align.center(header), style="bold", border_style="bright_blue", padding=(0, 0))


def _build_shortcuts_bar() -> Panel:
    """Keyboard shortcuts bar."""
    t = Text()
    t.append(" [S]", style="bold cyan")
    t.append("weep ", style="dim")
    t.append("[P]", style="bold cyan")
    t.append("urge ", style="dim")
    t.append("[C]", style="bold green")
    t.append("lean ", style="dim")
    t.append("[G]", style="bold green")
    t.append("C ", style="dim")
    t.append("[H]", style="bold yellow")
    t.append("ealth ", style="dim")
    t.append("[K]", style="bold red")
    t.append("sec ", style="dim")
    t.append("[D]", style="bold blue")
    t.append("ocker ", style="dim")
    t.append("[F]", style="bold magenta")
    t.append("ocus ", style="dim")
    t.append("[1-9]", style="bold white")
    t.append("kill ", style="dim")
    t.append("[Q]", style="bold red")
    t.append("uit", style="dim")
    return Panel(Align.center(t), border_style="bright_blue", padding=(0, 0))


def _build_cpu_panel() -> Panel:
    global _cpu_history
    cpu_pct = psutil.cpu_percent(interval=0)
    _cpu_history.append(cpu_pct)
    _cpu_history = _cpu_history[-60:]
    cpu_per_core = psutil.cpu_percent(percpu=True)

    lines = []
    lines.append(f"  Overall  {_bar(cpu_pct, 25)}")
    lines.append(f"  History  [dim]{_spark(_cpu_history, 30)}[/]")
    lines.append("")

    mid = (len(cpu_per_core) + 1) // 2
    for i in range(mid):
        left = f"  Core {i:<2} {_bar(cpu_per_core[i], 12)}"
        right_idx = i + mid
        right = f"  Core {right_idx:<2} {_bar(cpu_per_core[right_idx], 12)}" if right_idx < len(cpu_per_core) else ""
        lines.append(f"{left}  {right}")

    return Panel("\n".join(lines), title="[bold green]CPU[/]", border_style="green", padding=(0, 0))


def _build_memory_panel() -> Panel:
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    ram_color = "red" if mem.percent > 88 else "yellow" if mem.percent > 70 else "green"

    lines = []
    lines.append(f"  RAM      {_bar(mem.percent, 25, ram_color)}")
    lines.append(f"           [dim]{format_size(mem.used)} / {format_size(mem.total)}  (Avail: {format_size(mem.available)})[/]")
    lines.append("")

    swap_pct = swap.percent if swap.total > 0 else 0
    swap_color = "red" if swap_pct > 80 else "yellow" if swap_pct > 50 else "green"
    lines.append(f"  Swap     {_bar(swap_pct, 25, swap_color)}")
    lines.append(f"           [dim]{format_size(swap.used)} / {format_size(swap.total)}[/]")

    try:
        lines.append("")
        lines.append(f"  [dim]Active: {format_size(mem.active)}  Wired: {format_size(mem.wired)}  Inactive: {format_size(mem.inactive)}[/]")
    except AttributeError:
        pass

    return Panel("\n".join(lines), title=f"[bold {ram_color}]Memory[/]", border_style=ram_color, padding=(0, 0))


def _build_disk_panel() -> Panel:
    disk = psutil.disk_usage("/")
    disk_free_gb = disk.free / (1024**3)
    disk_color = "red" if disk_free_gb < 5 else "yellow" if disk_free_gb < 15 else "green"
    r_rate, w_rate = _get_disk_rate()

    lines = []
    lines.append(f"  Usage    {_bar(disk.percent, 25, disk_color)}")
    lines.append(f"           [dim]{format_size(disk.used)} / {format_size(disk.total)}  Free: {format_size(disk.free)}[/]")
    lines.append("")
    lines.append(f"  [cyan]Read:[/]  {format_size(int(r_rate))}/s    [magenta]Write:[/] {format_size(int(w_rate))}/s")

    return Panel("\n".join(lines), title=f"[bold {disk_color}]Disk[/]", border_style=disk_color, padding=(0, 0))


def _build_network_battery_panel() -> Panel:
    up_rate, down_rate, total_up, total_down = _get_net_rate()

    lines = []
    lines.append(f"  [green]\u2191 Up[/]   {format_size(int(up_rate))}/s  [dim]{_spark(_net_history['up'], 15)}[/]")
    lines.append(f"  [cyan]\u2193 Down[/] {format_size(int(down_rate))}/s  [dim]{_spark(_net_history['down'], 15)}[/]")

    bat = _get_battery_info()
    if bat:
        pct = bat["percent"]
        bat_color = "red" if pct < 20 else "yellow" if pct < 50 else "green"
        icon = "\u26a1" if bat.get("charging") else "\U0001f50b"
        secs = bat.get("secs_left", -1)
        time_left = f" ({format_duration(secs)})" if secs > 0 else ""
        lines.append(f"  {icon} Bat    {_bar(pct, 12, bat_color)}{time_left}")

    return Panel("\n".join(lines), title="[bold blue]Net & Battery[/]", border_style="blue", padding=(0, 0))


def _build_thermal_panel() -> Panel:
    _refresh_thermal_cache()

    temp = _thermal_cache.get("cpu_temp")
    fan = _thermal_cache.get("fan_speed")
    throttled = _thermal_cache.get("throttled", False)

    lines = []
    if temp is not None:
        t_color = "red bold" if temp > 90 else "yellow" if temp > 75 else "green"
        t_pct = min(100, (temp / 105) * 100)
        lines.append(f"  CPU      {_bar(t_pct, 15, t_color)} {temp:.0f}\u00b0C")
        lines.append(f"  [dim]{_spark(_temp_history, 20)}[/]")
    else:
        lines.append("  [dim]No temp data[/]")

    if fan is not None:
        fan_pct = min(100, (fan / 6500) * 100)
        fan_color = "red" if fan > 5000 else "yellow" if fan > 3000 else "green"
        lines.append(f"  Fan      [{fan_color}]{fan} RPM[/]")

    if throttled:
        lines.append("  [red bold]\u26a0 THERMAL THROTTLING[/]")

    return Panel("\n".join(lines), title="[bold red]\U0001f321 Thermal[/]", border_style="red" if throttled else "yellow", padding=(0, 0))


def _build_process_panel(max_procs: int = 15) -> Panel:
    global _top_procs
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status", "create_time", "username"]):
        try:
            info = p.info
            if info["cpu_percent"] is None:
                continue
            cat = categorize_process(info["name"])
            cpu = info["cpu_percent"] or 0
            ram = info["memory_info"].rss if info["memory_info"] else 0
            if cat == "other" and cpu < 1.0 and ram < 50 * 1024 * 1024:
                continue
            procs.append({
                "pid": info["pid"],
                "name": info["name"],
                "cpu": cpu,
                "ram": ram,
                "status": info["status"],
                "created": info["create_time"] or 0,
                "user": info["username"] or "",
                "category": cat,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x["cpu"], reverse=True)
    procs = procs[:max_procs]
    _top_procs = procs[:9]  # Cache for 1-9 kill

    table = Table(box=None, padding=(0, 1), show_edge=False)
    table.add_column("#", width=2, style="bold cyan")
    table.add_column("", width=2)
    table.add_column("PID", style="dim", width=6, justify="right")
    table.add_column("Name", width=18)
    table.add_column("CPU%", width=6, justify="right")
    table.add_column("RAM", width=9, justify="right")
    table.add_column("St", width=4)
    table.add_column("Time", width=7)

    for i, p in enumerate(procs):
        emoji = CATEGORY_EMOJI.get(p["category"], CATEGORY_EMOJI["other"])
        cpu_color = "red bold" if p["cpu"] > 90 else "yellow" if p["cpu"] > 50 else "green" if p["cpu"] > 5 else "white"
        runtime = format_duration(time.time() - p["created"]) if p["created"] > 0 else "?"
        status_icon = "\u25cf" if p["status"] == "running" else "\u25cb" if p["status"] == "sleeping" else "\u2716"
        status_color = "green" if p["status"] == "running" else "dim" if p["status"] == "sleeping" else "red"
        row_style = "on grey15" if i % 2 == 0 else ""
        idx = str(i + 1) if i < 9 else ""

        table.add_row(
            idx, emoji, str(p["pid"]),
            Text(p["name"][:18], style=row_style),
            Text(f"{p['cpu']:.1f}", style=cpu_color),
            format_size(p["ram"]),
            Text(status_icon, style=status_color),
            runtime,
            style=row_style,
        )

    # Category summary
    cat_summary = {}
    for p in procs:
        cat = p["category"]
        if cat not in cat_summary:
            cat_summary[cat] = {"cpu": 0, "ram": 0, "count": 0}
        cat_summary[cat]["cpu"] += p["cpu"]
        cat_summary[cat]["ram"] += p["ram"]
        cat_summary[cat]["count"] += 1

    footer_parts = []
    for cat, stats in sorted(cat_summary.items(), key=lambda x: x[1]["ram"], reverse=True)[:6]:
        emoji = CATEGORY_EMOJI.get(cat, "")
        footer_parts.append(f"{emoji}{cat}:{stats['count']}({format_size(stats['ram'])})")

    content = Group(table, Text(f"\n  {'  '.join(footer_parts)}", style="dim"))
    return Panel(content, title="[bold magenta]Processes [1-9 to kill][/]", border_style="magenta", padding=(0, 0))


def _build_alerts_panel() -> Panel:
    cpu_pct = psutil.cpu_percent(interval=0)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    disk_free_gb = disk.free / (1024**3)
    temp = _thermal_cache.get("cpu_temp")

    lines = []

    if (temp or 0) > 90:
        lines.append("  [red bold]\u26a0 CRITICAL[/] Overheating!")
    elif (temp or 0) > 80:
        lines.append("  [yellow]\u26a0 WARNING[/]  CPU hot")

    if cpu_pct > 90:
        lines.append("  [red bold]\u26a0 CRITICAL[/] CPU > 90%")
    elif cpu_pct > 70:
        lines.append("  [yellow]\u26a0 WARNING[/]  CPU > 70%")

    if mem.percent > 88:
        lines.append("  [red bold]\u26a0 CRITICAL[/] RAM > 88%")
    elif mem.percent > 70:
        lines.append("  [yellow]\u26a0 WARNING[/]  RAM > 70%")

    if disk_free_gb < 5:
        lines.append(f"  [red bold]\u26a0 CRITICAL[/] Disk < 5GB")
    elif disk_free_gb < 15:
        lines.append(f"  [yellow]\u26a0 WARNING[/]  Disk < 15GB")

    swap = psutil.swap_memory()
    if swap.percent > 80:
        lines.append(f"  [red bold]\u26a0 CRITICAL[/] Swap > 80%")

    if not lines:
        lines.append("  [green]\u2714 All systems nominal[/]")

    # Zombie/Orphan
    zombies = orphans = 0
    try:
        for p in psutil.process_iter(["pid", "ppid", "status", "name"]):
            try:
                if p.info["status"] == psutil.STATUS_ZOMBIE:
                    zombies += 1
                if p.info["ppid"] == 1 and categorize_process(p.info.get("name", "")) != "other":
                    orphans += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    lines.append("")
    z_color = "red" if zombies > 0 else "green"
    o_color = "yellow" if orphans > 3 else "green"
    lines.append(f"  Zombies: [{z_color}]{zombies}[/]  Orphans: [{o_color}]{orphans}[/]")

    tips = smart_suggestions(cpu_pct, mem.percent, zombies, orphans)
    if tips:
        lines.append("")
        for t in tips[:2]:
            lines.append(f"  [dim cyan]\u25b6 {t}[/]")

    border = "red" if any("CRITICAL" in l for l in lines) else "yellow" if any("WARNING" in l for l in lines) else "green"
    return Panel("\n".join(lines), title="[bold]Alerts & Tips[/]", border_style=border, padding=(0, 0))


def _build_security_panel() -> Panel:
    _refresh_security_cache()
    lines = []
    score = _security_cache.get("score")
    if score is None:
        lines.append("  [dim]Scanning...[/]")
    else:
        score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"
        lines.append(f"  Score    {_bar(score, 15, score_color)}")
        est = _security_cache.get("established", "?")
        lis = _security_cache.get("listening", "?")
        lines.append(f"  [dim]Conn: [cyan]{est}[/] active [cyan]{lis}[/] listen[/]")
        findings = _security_cache.get("findings", [])
        if findings:
            for severity, msg in findings[:3]:
                lines.append(f"  {severity} {msg}")
        else:
            lines.append("  [green]\u2714 No threats[/]")

    return Panel("\n".join(lines), title="[bold red]\U0001f6e1 Security[/]", border_style="red", padding=(0, 0))


def _build_docker_panel() -> Panel:
    _refresh_docker_cache()
    lines = []
    if _docker_cache.get("available") is None:
        lines.append("  [dim]Checking...[/]")
    elif not _docker_cache["available"]:
        lines.append("  [dim]Docker not running[/]")
    else:
        running = _docker_cache.get("running", [])
        stopped = _docker_cache.get("stopped", 0)
        images = _docker_cache.get("images", 0)
        dangling = _docker_cache.get("dangling", 0)

        r_color = "green" if running else "dim"
        lines.append(f"  [{r_color}]\u25cf {len(running)} run[/] [yellow]{stopped} stop[/] [dim]{images} img[/]")
        if dangling > 0:
            lines.append(f"  [yellow]\u26a0 {dangling} dangling[/]")
        if running:
            for c in running[:4]:
                lines.append(f"  [green]\u25b8[/] {c['name'][:14]}")
        else:
            lines.append("  [dim]No containers[/]")

    return Panel("\n".join(lines), title="[bold cyan]\U0001f40b Docker[/]", border_style="cyan", padding=(0, 0))


def _build_footer() -> Panel:
    now = datetime.now().strftime("%H:%M:%S")
    cpu = psutil.cpu_percent(interval=0)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    temp = _thermal_cache.get("cpu_temp")

    parts = Text()
    parts.append(f" \u23f1 {now}", style="dim")
    parts.append(f"  CPU:", style="dim")
    parts.append(f"{cpu:.0f}%", style="red bold" if cpu > 90 else "yellow" if cpu > 70 else "green")
    parts.append(f"  RAM:", style="dim")
    parts.append(f"{mem.percent:.0f}%", style="red bold" if mem.percent > 88 else "yellow" if mem.percent > 70 else "green")
    parts.append(f"  Disk:", style="dim")
    parts.append(f"{format_size(disk.free)}", style="green")

    if temp:
        t_color = "red bold" if temp > 90 else "yellow" if temp > 75 else "green"
        parts.append(f"  Temp:", style="dim")
        parts.append(f"{temp:.0f}\u00b0C", style=t_color)

    # Show action status for 5 seconds
    if _action_status["msg"] and (time.time() - _action_status["time"]) < 5:
        parts.append(f"  \u25b6 ", style="dim")
        parts.append(_action_status["msg"], style="bold green")

    return Panel(parts, border_style="bright_blue", padding=(0, 0))


# ── Keyboard actions (REAL actions, not just scans) ─────────────────────

def _get_key_nonblocking():
    if select.select([sys.stdin], [], [], 0.0)[0]:
        return sys.stdin.read(1)
    return None


def _run_action_overlay(live, action_name, action_fn):
    """Pause dashboard, run action with full output, resume."""
    live.stop()
    console.print(f"\n[bold cyan]\u25b6 {action_name}[/]\n")
    try:
        action_fn()
    except Exception as e:
        console.print(f"[red]Error: {e}[/]")
    console.print(f"\n[dim]Press any key to return...[/]")
    sys.stdin.read(1)
    live.start()


def _action_sweep_execute(live):
    """Kill zombies, orphans, stale locks, dead ports."""
    from .processes import run_sweep
    _run_action_overlay(live, "SWEEP -- Killing zombies, orphans, stale locks", lambda: run_sweep(force_yes=True))
    _set_status("Sweep complete")


def _action_purge_execute(live):
    """Purge RAM."""
    from .processes import purge_ram
    _run_action_overlay(live, "PURGE -- Freeing inactive RAM", purge_ram)
    _set_status("RAM purged")


def _action_clean_execute(live):
    """Full system clean (not just scan)."""
    from .cleaner import run_cleaner
    _run_action_overlay(live, "CLEAN -- System junk, caches, browsers, apps", lambda: run_cleaner(all_clean=True, force_yes=True))
    _set_status("System cleaned")


def _action_gc_execute(live):
    """Dev garbage collector (full clean)."""
    from .gc import run_gc
    _run_action_overlay(live, "GC -- node_modules, venvs, docker, caches", lambda: run_gc(all_gc=True, force_yes=True))
    _set_status("Dev GC complete")


def _action_health_execute(live):
    """Health check with auto-fix."""
    from .health import run_health
    _run_action_overlay(live, "HEALTH -- Check + Auto-fix", lambda: run_health(fix=True))
    _set_status("Health check done")


def _action_security_execute(live):
    """Full security scan."""
    from .security import run_security
    _run_action_overlay(live, "SECURITY -- Full audit", run_security)
    _set_status("Security scan done")
    _security_cache["last_update"] = 0  # Force refresh


def _action_docker_execute(live):
    """Docker overview + optional prune."""
    from .docker_mgr import run_docker
    _run_action_overlay(live, "DOCKER -- Overview + management", run_docker)
    _set_status("Docker check done")
    _docker_cache["last_update"] = 0


def _action_focus_execute(live):
    """Enter focus mode."""
    from .autopilot import enter_focus
    _run_action_overlay(live, "FOCUS -- Quit non-essentials, purge RAM, DND", enter_focus)
    _set_status("Focus mode active")


def _action_kill_process(live, index):
    """Kill process by index in the top list."""
    if index >= len(_top_procs):
        _set_status(f"No process at #{index + 1}")
        return
    proc = _top_procs[index]
    try:
        p = psutil.Process(proc["pid"])
        pname = proc["name"]
        p.terminate()
        _set_status(f"Killed {pname} (PID {proc['pid']})")
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        _set_status(f"Cannot kill PID {proc['pid']}: {e}")


# ── Main dashboard loop ─────────────────────────────────────────────────

def run_dashboard(refresh: int = 2):
    cfg = load_config()
    refresh = cfg.get("dashboard", {}).get("refresh_seconds", refresh)
    max_procs = cfg.get("dashboard", {}).get("max_processes", 15)

    # Initial measurements
    psutil.cpu_percent(interval=0.1)
    _get_net_rate()
    _get_disk_rate()

    old_settings = termios.tcgetattr(sys.stdin)

    try:
        tty.setcbreak(sys.stdin.fileno())

        with Live(console=console, refresh_per_second=2, screen=True) as live:
            while True:
                layout = Layout()

                layout.split_column(
                    Layout(name="header", size=3),
                    Layout(name="shortcuts", size=3),
                    Layout(name="body"),
                    Layout(name="footer", size=3),
                )

                layout["header"].update(_build_header())
                layout["shortcuts"].update(_build_shortcuts_bar())

                # Body: left (system metrics) + right (processes + info panels)
                layout["body"].split_row(
                    Layout(name="left", ratio=2),
                    Layout(name="right", ratio=3),
                )

                # Left: CPU, Memory, Disk, Net+Bat, Thermal
                layout["left"].split_column(
                    Layout(_build_cpu_panel(), name="cpu"),
                    Layout(_build_memory_panel(), name="mem"),
                    Layout(name="left_bottom"),
                )

                layout["left_bottom"].split_row(
                    Layout(name="left_bottom_l"),
                    Layout(name="left_bottom_r"),
                )

                layout["left_bottom_l"].split_column(
                    Layout(_build_disk_panel(), name="disk"),
                    Layout(_build_network_battery_panel(), name="net"),
                )

                layout["left_bottom_r"].split_column(
                    Layout(_build_thermal_panel(), name="thermal"),
                    Layout(_build_security_panel(), name="security"),
                    Layout(_build_docker_panel(), name="docker"),
                )

                # Right: processes + alerts
                layout["right"].split_column(
                    Layout(_build_process_panel(max_procs), name="procs", ratio=3),
                    Layout(_build_alerts_panel(), name="alerts", ratio=1),
                )

                layout["footer"].update(_build_footer())

                live.update(layout)

                # Keypress polling
                for _ in range(int(refresh * 10)):
                    key = _get_key_nonblocking()
                    if key:
                        k = key.lower()
                        if k == "q":
                            return
                        elif k == "s":
                            _action_sweep_execute(live)
                        elif k == "p":
                            _action_purge_execute(live)
                        elif k == "c":
                            _action_clean_execute(live)
                        elif k == "g":
                            _action_gc_execute(live)
                        elif k == "h":
                            _action_health_execute(live)
                        elif k == "k":
                            _action_security_execute(live)
                        elif k == "d":
                            _action_docker_execute(live)
                        elif k == "f":
                            _action_focus_execute(live)
                        elif k in "123456789":
                            _action_kill_process(live, int(k) - 1)
                        break
                    time.sleep(0.1)

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        console.print("\n[dim]Dashboard stopped.[/]")
