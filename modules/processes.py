"""Process manager, sweep, and port management for macmon."""

import fcntl
import json
import os
import signal
import time
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .utils import (
    CATEGORY_EMOJI,
    categorize_process,
    confirm_action,
    console,
    format_duration,
    format_size,
    log_action,
    run_cmd,
)


# ── Process Listing ──────────────────────────────────────────────────────

def list_processes(filter_cat: str = None, sort_by: str = "cpu", tree: bool = False, json_out: bool = False):
    # First pass primes per-process CPU counters (first cpu_percent always reads 0.0),
    # second pass after a short delay reads real values
    cached = list(psutil.process_iter(["pid", "ppid", "name", "cpu_percent", "memory_info", "status", "create_time", "username"]))
    time.sleep(0.5)
    procs = []
    for p in cached:
        try:
            info = p.info
            name = info["name"] or ""
            cat = categorize_process(name)
            if filter_cat and cat != filter_cat:
                continue
            try:
                cpu = p.cpu_percent(interval=None)
            except (psutil.ZombieProcess, psutil.AccessDenied):
                cpu = info["cpu_percent"] or 0
            # Skip low-usage "other" processes
            ram = info["memory_info"].rss if info["memory_info"] else 0
            if cat == "other" and cpu < 1.0 and ram < 50 * 1024 * 1024:
                continue
            procs.append({
                "pid": info["pid"],
                "ppid": info["ppid"],
                "name": name,
                "cpu": cpu,
                "ram": ram,
                "status": info["status"],
                "created": info["create_time"] or 0,
                "user": info["username"] or "",
                "category": cat,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    sort_key = {"cpu": "cpu", "ram": "ram", "name": "name", "runtime": "created"}.get(sort_by, "cpu")
    reverse = sort_by != "name" and sort_by != "runtime"
    procs.sort(key=lambda x: x[sort_key], reverse=reverse)

    if json_out:
        console.print_json(json.dumps(procs[:50], default=str))
        return

    if tree:
        _print_tree(procs)
        return

    table = Table(title="Dev Processes", border_style="magenta")
    table.add_column("", width=2)
    table.add_column("PID", style="dim", width=7)
    table.add_column("Name", width=24)
    table.add_column("CPU%", width=7, justify="right")
    table.add_column("RAM", width=10, justify="right")
    table.add_column("Status", width=10)
    table.add_column("Runtime", width=10)
    table.add_column("User", width=12, style="dim")
    table.add_column("Cat", width=8, style="dim")

    for p in procs[:50]:
        emoji = CATEGORY_EMOJI.get(p["category"], CATEGORY_EMOJI["other"])
        cpu_color = "red" if p["cpu"] > 90 else "yellow" if p["cpu"] > 50 else "white"
        runtime = format_duration(time.time() - p["created"]) if p["created"] > 0 else "?"
        table.add_row(
            emoji, str(p["pid"]), p["name"][:24],
            f"[{cpu_color}]{p['cpu']:.1f}[/]",
            format_size(p["ram"]),
            p["status"][:10], runtime,
            p["user"][:12], p["category"],
        )

    console.print(table)
    console.print(f"[dim]Showing {min(len(procs), 50)} of {len(procs)} processes[/]")


def _print_tree(procs: list[dict]):
    by_ppid: dict[int, list[dict]] = {}
    pids = {p["pid"] for p in procs}
    for p in procs:
        by_ppid.setdefault(p["ppid"], []).append(p)

    visited: set[int] = set()  # guards pid==ppid self-recursion (kernel_task pid 0)

    def _render(pid: int, indent: int = 0):
        children = by_ppid.get(pid, [])
        for child in children:
            if child["pid"] in visited:
                continue
            visited.add(child["pid"])
            prefix = "  " * indent + ("|- " if indent > 0 else "")
            emoji = CATEGORY_EMOJI.get(child["category"], CATEGORY_EMOJI["other"])
            console.print(
                f"{prefix}{emoji} [bold]{child['name']}[/] "
                f"(PID:{child['pid']} CPU:{child['cpu']:.1f}% RAM:{format_size(child['ram'])})"
            )
            _render(child["pid"], indent + 1)

    console.print("[bold]Process Tree:[/]")
    _render(1)  # Start from launchd (PID 1)
    _render(0)  # Also root processes
    # Processes whose parent was filtered out become additional roots
    for p in procs:
        if p["pid"] in visited or p["ppid"] in pids:
            continue
        visited.add(p["pid"])
        emoji = CATEGORY_EMOJI.get(p["category"], CATEGORY_EMOJI["other"])
        console.print(
            f"{emoji} [bold]{p['name']}[/] "
            f"(PID:{p['pid']} CPU:{p['cpu']:.1f}% RAM:{format_size(p['ram'])})"
        )
        _render(p["pid"], 1)


# ── Kill / Suspend / Resume / Nice ───────────────────────────────────────

def _find_process(target: str) -> list[psutil.Process]:
    """Find process by PID or name. Never matches macmon itself."""
    matches = []
    try:
        pid = int(target)
        if pid != os.getpid():
            try:
                matches.append(psutil.Process(pid))
            except psutil.NoSuchProcess:
                pass
    except ValueError:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["pid"] == os.getpid():
                    continue
                if target.lower() in (p.info["name"] or "").lower():
                    matches.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    return matches


def kill_process(target: str, category: str = None, force_yes: bool = False):
    if category:
        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["pid"] == os.getpid():
                    continue
                if categorize_process(p.info["name"] or "") == category:
                    procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        if not procs:
            console.print(f"[yellow]No processes in category '{category}'[/]")
            return
        console.print(f"[red]Found {len(procs)} processes in '{category}':[/]")
        for p in procs:
            console.print(f"  PID {p.pid}: {p.name()}")
        if confirm_action(f"Kill all {len(procs)} processes?", force_yes=force_yes):
            for p in procs:
                try:
                    p.terminate()
                    log_action("kill", f"PID {p.pid} ({p.name()}) - category {category}")
                except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                    console.print(f"  [red]Failed to kill {p.pid}: {e}[/]")
            console.print(f"[green]Terminated {len(procs)} processes.[/]")
        return

    matches = _find_process(target)
    if not matches:
        console.print(f"[yellow]No process found matching '{target}'[/]")
        return

    for p in matches:
        try:
            name = p.name()
            pid = p.pid
            if confirm_action(f"Kill {name} (PID {pid})?", force_yes=force_yes):
                p.terminate()
                log_action("kill", f"PID {pid} ({name})")
                console.print(f"[green]Terminated {name} (PID {pid})[/]")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            console.print(f"[red]Error: {e}[/]")


def suspend_process(target: str):
    matches = _find_process(target)
    for p in matches:
        try:
            p.suspend()
            console.print(f"[yellow]Suspended {p.name()} (PID {p.pid})[/]")
            log_action("suspend", f"PID {p.pid}")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            console.print(f"[red]Error: {e}[/]")


def resume_process(target: str):
    matches = _find_process(target)
    for p in matches:
        try:
            p.resume()
            console.print(f"[green]Resumed {p.name()} (PID {p.pid})[/]")
            log_action("resume", f"PID {p.pid}")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            console.print(f"[red]Error: {e}[/]")


def renice_process(target: str, value: int):
    if value < -20 or value > 19:
        console.print("[red]Nice value must be between -20 and 19[/]")
        return
    matches = _find_process(target)
    for p in matches:
        try:
            p.nice(value)
            console.print(f"[green]Set nice={value} for {p.name()} (PID {p.pid})[/]")
            log_action("renice", f"PID {p.pid} nice={value}")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            console.print(f"[red]Error: {e}[/]")


def quit_app(app_name: str):
    safe_name = app_name.replace("\\", "\\\\").replace('"', '\\"')
    out, err, rc = run_cmd([
        "osascript", "-e", f'tell application "{safe_name}" to quit'
    ])
    if rc == 0:
        console.print(f"[green]Quit {app_name}[/]")
        log_action("quit_app", app_name)
    else:
        console.print(f"[red]Failed to quit {app_name}: {err}[/]")


def restart_app(app_name: str):
    quit_app(app_name)
    time.sleep(2)
    out, err, rc = run_cmd(["open", "-a", app_name])
    if rc == 0:
        console.print(f"[green]Reopened {app_name}[/]")
        log_action("restart_app", app_name)
    else:
        console.print(f"[red]Failed to reopen {app_name}: {err}[/]")


# ── Purge RAM ────────────────────────────────────────────────────────────

def purge_ram():
    mem_before = psutil.virtual_memory()
    console.print(f"[cyan]RAM before: {format_size(mem_before.used)} used, {format_size(mem_before.available)} available[/]")
    console.print("[yellow]Running sudo purge...[/]")
    # sudo -n fails fast instead of hanging on a password prompt run_cmd cannot show
    _, err, rc = run_cmd(["sudo", "-n", "purge"], timeout=30)
    if rc != 0:
        console.print(f"[red]Purge failed: {err.strip() or 'sudo needs a password'}[/]")
        console.print("[yellow]Run 'sudo -v' first to cache credentials, or configure NOPASSWD for /usr/sbin/purge.[/]")
        return
    time.sleep(1)
    mem_after = psutil.virtual_memory()
    freed = mem_before.used - mem_after.used
    console.print(f"[green]RAM after: {format_size(mem_after.used)} used, {format_size(mem_after.available)} available[/]")
    if freed > 0:
        console.print(f"[green bold]Freed: {format_size(freed)}[/]")
    else:
        console.print("[dim]No significant RAM freed (already optimal)[/]")
    log_action("purge", f"freed {format_size(max(0, freed))}")


# ── Sweep: Zombie/Orphan/Port/Lock killer ────────────────────────────────

def run_sweep(zombies_only: bool = False, orphans_only: bool = False, force_yes: bool = False):
    console.print(Panel("[bold]macmon sweep[/] -- Dead Process Hunter", border_style="red"))

    results = {"zombies": 0, "orphans": 0, "ports": 0, "locks": 0}

    if not orphans_only:
        results["zombies"] = _kill_zombies(force_yes)

    if not zombies_only:
        results["orphans"] = _kill_orphans(force_yes)

    if not zombies_only and not orphans_only:
        results["ports"] = _clean_dead_ports(force_yes)
        results["locks"] = _clean_stale_locks(force_yes)

    table = Table(title="Sweep Summary", border_style="green")
    table.add_column("Category", style="cyan")
    table.add_column("Found & Cleaned", justify="right")
    table.add_row("Zombie processes", str(results["zombies"]))
    table.add_row("Orphan processes", str(results["orphans"]))
    table.add_row("Dead port holders", str(results["ports"]))
    table.add_row("Stale lock files", str(results["locks"]))
    console.print(table)


def _kill_zombies(force_yes: bool = False) -> int:
    zombies = []
    for p in psutil.process_iter(["pid", "ppid", "name", "status", "create_time"]):
        try:
            if p.info["status"] == psutil.STATUS_ZOMBIE:
                zombies.append(p.info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not zombies:
        console.print("[green]No zombie processes found.[/]")
        return 0

    table = Table(title=f"Zombie Processes ({len(zombies)})", border_style="red")
    table.add_column("PID", width=7)
    table.add_column("PPID", width=7)
    table.add_column("Name", width=24)
    table.add_column("Dead Since", width=15)
    for z in zombies:
        created = format_duration(time.time() - z["create_time"]) if z["create_time"] else "?"
        table.add_row(str(z["pid"]), str(z["ppid"]), z["name"] or "?", created)
    console.print(table)

    if confirm_action(f"Try to reap {len(zombies)} zombie processes?", force_yes=force_yes):
        # Nudge each parent with SIGCHLD so it reaps its child; never terminate
        # the parent, and never signal pid <= 1 (zombies themselves ignore signals)
        for z in zombies:
            ppid = z["ppid"] or 0
            if ppid > 1:
                try:
                    os.kill(ppid, signal.SIGCHLD)
                except (ProcessLookupError, PermissionError):
                    pass
        time.sleep(1)
        remaining = []
        for z in zombies:
            try:
                if psutil.Process(z["pid"]).status() == psutil.STATUS_ZOMBIE:
                    remaining.append(z)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        reaped = len(zombies) - len(remaining)
        console.print(f"[green]Reaped {reaped} zombie processes.[/]")
        if remaining:
            console.print(f"[yellow]{len(remaining)} zombies remain (their parent did not reap them; they use no resources).[/]")
        log_action("sweep_zombies", f"reaped {reaped}, {len(remaining)} remain")
        return reaped
    return 0


def _kill_orphans(force_yes: bool = False) -> int:
    orphans = []
    dev_categories = {"llm", "ide", "node", "python", "build"}
    for p in psutil.process_iter(["pid", "ppid", "name", "cpu_percent", "memory_info", "status", "create_time"]):
        try:
            info = p.info
            if info["ppid"] != 1 or info["pid"] == os.getpid():
                continue
            name = info["name"] or ""
            cat = categorize_process(name)
            if cat not in dev_categories:
                continue
            # Skip system-essential processes
            if name in ("launchd", "kernel_task", "WindowServer"):
                continue
            # ppid==1 is not enough on macOS: launchd parents all GUI apps and
            # LaunchAgents. Require no controlling terminal and an executable
            # outside /Applications and /System before calling it an orphan.
            if p.terminal() is not None:
                continue
            exe = p.exe()
            if not exe or exe.startswith(("/Applications/", "/System/")):
                continue
            orphans.append({
                **info,
                "name": name,
                "ram": info["memory_info"].rss if info["memory_info"] else 0,
                "category": cat,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if not orphans:
        console.print("[green]No orphan dev processes found.[/]")
        return 0

    table = Table(title=f"Orphan Dev Processes ({len(orphans)})", border_style="yellow")
    table.add_column("PID", width=7)
    table.add_column("Name", width=24)
    table.add_column("CPU%", width=7, justify="right")
    table.add_column("RAM", width=10, justify="right")
    table.add_column("Category", width=10)
    table.add_column("Runtime", width=10)
    for o in orphans:
        runtime = format_duration(time.time() - o["create_time"]) if o["create_time"] else "?"
        table.add_row(
            str(o["pid"]), o["name"][:24],
            f"{o['cpu_percent'] or 0:.1f}", format_size(o["ram"]),
            o["category"], runtime,
        )
    console.print(table)

    total_ram = sum(o["ram"] for o in orphans)
    console.print(f"[yellow]Total orphan RAM: {format_size(total_ram)}[/]")

    if confirm_action(f"Kill {len(orphans)} orphan processes?", force_yes=force_yes):
        killed = 0
        for o in orphans:
            try:
                proc = psutil.Process(o["pid"])
                proc.terminate()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        console.print(f"[green]Killed {killed} orphan processes.[/]")
        log_action("sweep_orphans", f"killed {killed}, freed ~{format_size(total_ram)}")
        return killed
    return 0


def _clean_dead_ports(force_yes: bool = False) -> int:
    cfg = load_config()
    watch_ports = cfg.get("dev_ports", {}).get("watch", [3000, 3001, 4000, 5000, 5173, 8000, 8080, 8888, 9000, 9229])
    dead = _find_dead_port_holders(watch_ports)
    if not dead:
        console.print("[green]No dead port holders found.[/]")
        return 0

    table = Table(title=f"Dead Port Holders ({len(dead)})", border_style="red")
    table.add_column("Port", width=7)
    table.add_column("PID", width=7)
    table.add_column("Name", width=24)
    table.add_column("Status", width=10)
    for d in dead:
        table.add_row(str(d["port"]), str(d["pid"]), d["name"], d["status"])
    console.print(table)

    if confirm_action(f"Kill {len(dead)} dead port holders?", force_yes=force_yes):
        killed = 0
        for d in dead:
            try:
                os.kill(d["pid"], signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
        log_action("sweep_ports", f"killed {killed}")
        return killed
    return 0


def _find_dead_port_holders(ports: list[int]) -> list[dict]:
    dead = []
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        console.print("[yellow]Net connections require root. Using lsof fallback...[/]")
        return _find_dead_port_holders_lsof(ports)
    for conn in connections:
        if conn.laddr and conn.laddr.port in ports and conn.pid:
            try:
                p = psutil.Process(conn.pid)
                status = p.status()
                if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    dead.append({
                        "port": conn.laddr.port,
                        "pid": conn.pid,
                        "name": p.name(),
                        "status": status,
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                dead.append({
                    "port": conn.laddr.port,
                    "pid": conn.pid,
                    "name": "?",
                    "status": "dead",
                })
    return dead


def _find_dead_port_holders_lsof(ports: list[int]) -> list[dict]:
    """Fallback using lsof when psutil.net_connections needs root."""
    dead = []
    for port in ports:
        out, _, rc = run_cmd(["lsof", "-ti", f"tcp:{port}"], timeout=5)
        if rc == 0 and out.strip():
            for pid_str in out.strip().splitlines():
                try:
                    pid = int(pid_str.strip())
                    try:
                        p = psutil.Process(pid)
                        if p.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                            dead.append({"port": port, "pid": pid, "name": p.name(), "status": p.status()})
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        dead.append({"port": port, "pid": pid, "name": "?", "status": "dead"})
                except ValueError:
                    pass
    return dead


def _lock_is_stale(f: Path) -> bool:
    """A .lock file is stale only if its embedded PID is dead or nobody holds
    an flock on it. mtime age alone is not evidence."""
    try:
        content = f.read_text(errors="ignore").strip()
    except OSError:
        return False
    if content.isdigit():
        return not psutil.pid_exists(int(content))
    try:
        with open(f, "rb") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh, fcntl.LOCK_UN)
        return True
    except OSError:
        return False


def _clean_stale_locks(force_yes: bool = False) -> int:
    lock_patterns = ["*.lock", "*.pid"]
    search_dirs = [Path.home(), Path("/tmp")]
    stale = []

    for base in search_dirs:
        for pattern in lock_patterns:
            try:
                for f in base.glob(pattern):
                    if not f.is_file():
                        continue
                    # Check if PID file references a dead process
                    if f.suffix == ".pid":
                        try:
                            pid = int(f.read_text().strip())
                            if not psutil.pid_exists(pid):
                                stale.append(f)
                        except (ValueError, OSError):
                            pass
                    elif f.suffix == ".lock":
                        # Age > 1 hour AND (dead embedded PID or flock acquirable)
                        try:
                            age = time.time() - f.stat().st_mtime
                            if age > 3600 and _lock_is_stale(f):
                                stale.append(f)
                        except OSError:
                            pass
            except (OSError, PermissionError):
                continue

    if not stale:
        console.print("[green]No stale lock/PID files found.[/]")
        return 0

    console.print(f"[yellow]Found {len(stale)} stale lock/PID files[/]")
    for f in stale[:20]:
        console.print(f"  [dim]{f}[/]")

    if confirm_action(f"Delete {len(stale)} stale lock files?", force_yes=force_yes):
        deleted = 0
        for f in stale:
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
        console.print(f"[green]Deleted {deleted} stale files.[/]")
        log_action("sweep_locks", f"deleted {deleted}")
        return deleted
    return 0


# ── Port Manager ─────────────────────────────────────────────────────────

def manage_ports(free_port: int = None, free_all: bool = False, force_yes: bool = False):
    cfg = load_config()
    watch_ports = cfg.get("dev_ports", {}).get("watch", [3000, 3001, 4000, 5000, 5173, 8000, 8080, 8888, 9000, 9229])

    if free_port:
        _free_port(free_port, force_yes)
        return

    if free_all:
        for port in watch_ports:
            _free_port(port, force_yes)
        return

    # Show port table
    table = Table(title="Port Usage", border_style="cyan")
    table.add_column("Port", width=7)
    table.add_column("PID", width=7)
    table.add_column("Process", width=24)
    table.add_column("RAM", width=10, justify="right")
    table.add_column("Status", width=12)

    # Use lsof fallback since psutil.net_connections needs root on macOS
    for port in watch_ports:
        out, _, rc = run_cmd(["lsof", "-ti", f"tcp:{port}"], timeout=3)
        if rc == 0 and out.strip():
            for pid_str in out.strip().splitlines():
                try:
                    pid = int(pid_str.strip())
                    try:
                        p = psutil.Process(pid)
                        status_color = "green" if p.status() == psutil.STATUS_RUNNING else "yellow"
                        table.add_row(
                            str(port), str(pid), p.name()[:24],
                            format_size(p.memory_info().rss),
                            f"[{status_color}]{p.status()}[/]",
                        )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        table.add_row(str(port), str(pid), "?", "?", "[red]dead[/]")
                except ValueError:
                    pass

    console.print(table)


def _free_port(port: int, force_yes: bool = False):
    out, _, rc = run_cmd(["lsof", "-ti", f"tcp:{port}"], timeout=5)
    if not (rc == 0 and out.strip()):
        console.print(f"[dim]Port {port} is not in use.[/]")
        return
    killed = 0
    for pid_str in out.strip().splitlines():
        try:
            pid = int(pid_str.strip())
            p = psutil.Process(pid)
            if confirm_action(f"Kill {p.name()} (PID {pid}) on port {port}?", force_yes=force_yes):
                p.terminate()
                killed += 1
                log_action("free_port", f"port {port} pid {pid}")
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied) as e:
            console.print(f"[red]Error freeing port {port}: {e}[/]")
            continue
    if killed:
        console.print(f"[green]Freed port {port} (killed {killed} process{'es' if killed != 1 else ''})[/]")
