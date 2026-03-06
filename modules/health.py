"""System health check and reporting for macmon."""

import json
import time
from datetime import datetime
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .utils import (
    REPORTS_DIR,
    console,
    dir_size,
    format_size,
    get_db,
    log_action,
    run_cmd,
)


def run_health(fix: bool = False, report: bool = False, json_out: bool = False):
    console.print(Panel("[bold]macmon health[/] -- System Health Check", border_style="green"))

    checks = _run_all_checks()
    score = _calculate_score(checks)

    # Display
    table = Table(title=f"Health Score: {score}/100", border_style="green" if score >= 80 else "yellow" if score >= 50 else "red")
    table.add_column("Check", style="cyan", width=35)
    table.add_column("Status", width=8)
    table.add_column("Details", width=40)

    for c in checks:
        status_emoji = {"pass": "[green]PASS[/]", "warn": "[yellow]WARN[/]", "fail": "[red]FAIL[/]"}.get(c["status"], "[dim]?[/]")
        table.add_row(c["name"], status_emoji, c["detail"])

    console.print(table)
    console.print(f"\n[bold]Overall Score: [{'green' if score >= 80 else 'yellow' if score >= 50 else 'red'}]{score}/100[/][/]")

    # Prioritized actions
    actions = [c for c in checks if c["status"] in ("warn", "fail")]
    if actions:
        console.print("\n[bold]Recommended Actions:[/]")
        for i, a in enumerate(actions[:10], 1):
            color = "red" if a["status"] == "fail" else "yellow"
            console.print(f"  [{color}]{i}. {a.get('fix_hint', a['name'])}[/]")

    if fix:
        _auto_fix(checks)

    if json_out:
        console.print_json(json.dumps({"score": score, "checks": checks}, default=str))

    if report:
        _save_report(score, checks)

    log_action("health", f"score={score}")


def _run_all_checks() -> list[dict]:
    checks = []

    # Zombie processes
    zombies = sum(1 for p in psutil.process_iter(["status"]) if p.info.get("status") == psutil.STATUS_ZOMBIE)
    checks.append({
        "name": "Zombie Processes",
        "status": "fail" if zombies > 5 else "warn" if zombies > 0 else "pass",
        "detail": f"{zombies} zombie processes",
        "fix_hint": f"Run `macmon sweep --zombies` to clean {zombies} zombies",
        "value": zombies,
    })

    # Memory pressure
    mem = psutil.virtual_memory()
    checks.append({
        "name": "RAM Usage",
        "status": "fail" if mem.percent > 88 else "warn" if mem.percent > 70 else "pass",
        "detail": f"{mem.percent:.1f}% used ({format_size(mem.used)}/{format_size(mem.total)})",
        "fix_hint": "Run `macmon purge` or close heavy apps",
        "value": mem.percent,
    })

    # Swap
    swap = psutil.swap_memory()
    swap_pct = swap.percent if swap.total > 0 else 0
    checks.append({
        "name": "Swap Usage",
        "status": "fail" if swap_pct > 80 else "warn" if swap_pct > 50 else "pass",
        "detail": f"{swap_pct:.1f}% ({format_size(swap.used)})",
        "value": swap_pct,
    })

    # Disk space
    disk = psutil.disk_usage("/")
    disk_free_gb = disk.free / (1024**3)
    checks.append({
        "name": "Disk Free Space",
        "status": "fail" if disk_free_gb < 5 else "warn" if disk_free_gb < 15 else "pass",
        "detail": f"{format_size(disk.free)} free ({disk.percent}% used)",
        "fix_hint": "Run `macmon clean --all` or `macmon gc --all`",
        "value": disk_free_gb,
    })

    # CPU load
    load1, load5, load15 = psutil.getloadavg()
    cpu_count = psutil.cpu_count()
    load_ratio = load5 / cpu_count if cpu_count else load5
    checks.append({
        "name": "CPU Load (5min avg)",
        "status": "fail" if load_ratio > 2.0 else "warn" if load_ratio > 1.0 else "pass",
        "detail": f"Load: {load5:.2f} ({load_ratio:.2f}x cores)",
        "value": load_ratio,
    })

    # Broken startup items
    broken_startup = _check_broken_startups()
    checks.append({
        "name": "Broken Startup Items",
        "status": "fail" if broken_startup > 3 else "warn" if broken_startup > 0 else "pass",
        "detail": f"{broken_startup} broken items",
        "fix_hint": "Run `macmon startup --broken` to review",
        "value": broken_startup,
    })

    # Launch Services DB
    ls_db = Path.home() / "Library/Preferences/com.apple.LaunchServices"
    ls_size = 0
    if ls_db.exists():
        ls_size = dir_size(ls_db) if ls_db.is_dir() else ls_db.stat().st_size
    checks.append({
        "name": "Launch Services DB",
        "status": "warn" if ls_size > 50 * 1024 * 1024 else "pass",
        "detail": format_size(ls_size),
        "fix_hint": "Run `lsregister -kill -r` to rebuild",
        "value": ls_size,
    })

    # Homebrew outdated
    out, _, rc = run_cmd(["brew", "outdated", "--quiet"], timeout=15)
    outdated = len(out.strip().splitlines()) if rc == 0 and out.strip() else 0
    checks.append({
        "name": "Homebrew Outdated",
        "status": "warn" if outdated > 20 else "pass",
        "detail": f"{outdated} outdated packages",
        "fix_hint": "Run `brew upgrade` to update",
        "value": outdated,
    })

    # node_modules count estimate
    nm_count = _estimate_node_modules()
    checks.append({
        "name": "node_modules Count",
        "status": "warn" if nm_count > 20 else "pass",
        "detail": f"~{nm_count} directories found",
        "fix_hint": "Run `macmon gc --scan` to review",
        "value": nm_count,
    })

    # Docker disk usage
    docker_info = _check_docker_usage()
    if docker_info:
        checks.append(docker_info)

    # Quarantine DB
    qdb = Path.home() / "Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
    qsize = qdb.stat().st_size if qdb.exists() else 0
    checks.append({
        "name": "Quarantine DB",
        "status": "warn" if qsize > 10 * 1024 * 1024 else "pass",
        "detail": format_size(qsize),
        "fix_hint": "Run `macmon privacy --clean`",
        "value": qsize,
    })

    # Battery health
    bat_check = _check_battery()
    if bat_check:
        checks.append(bat_check)

    # Time since last clean
    last_clean = _get_last_clean_time()
    if last_clean:
        hours_since = (time.time() - last_clean) / 3600
        checks.append({
            "name": "Last Full Clean",
            "status": "warn" if hours_since > 168 else "pass",
            "detail": f"{hours_since:.0f} hours ago",
            "fix_hint": "Run `macmon clean --all` for maintenance",
            "value": hours_since,
        })
    else:
        checks.append({
            "name": "Last Full Clean",
            "status": "warn",
            "detail": "Never run",
            "fix_hint": "Run `macmon clean --all` for first cleanup",
            "value": 999,
        })

    # macOS updates
    updates = _check_macos_updates()
    if updates is not None:
        checks.append({
            "name": "macOS Updates",
            "status": "warn" if updates > 0 else "pass",
            "detail": f"{updates} updates pending" if updates > 0 else "Up to date",
            "value": updates,
        })

    return checks


def _calculate_score(checks: list[dict]) -> int:
    total = len(checks)
    if total == 0:
        return 100
    points = 0
    for c in checks:
        if c["status"] == "pass":
            points += 100
        elif c["status"] == "warn":
            points += 50
        # fail = 0
    return int(points / total)


def _check_broken_startups() -> int:
    count = 0
    agent_dirs = [
        Path.home() / "Library/LaunchAgents",
        Path("/Library/LaunchAgents"),
    ]
    for d in agent_dirs:
        if not d.exists():
            continue
        for plist in d.glob("*.plist"):
            try:
                import plistlib
                with open(plist, "rb") as f:
                    data = plistlib.load(f)
                program = data.get("Program", "")
                if not program and "ProgramArguments" in data:
                    args = data["ProgramArguments"]
                    if args and isinstance(args, list):
                        program = args[0]
                if program and not Path(program).exists():
                    count += 1
            except Exception:
                pass
    return count


def _estimate_node_modules() -> int:
    count = 0
    search_dirs = [
        Path.home() / "Projects", Path.home() / "Documents",
        Path.home() / "Developer", Path.home() / "dev",
    ]
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for d in base.rglob("node_modules"):
                if d.is_dir():
                    count += 1
                if count >= 100:
                    return count
        except (OSError, PermissionError):
            continue
    return count


def _check_docker_usage():
    out, _, rc = run_cmd(["docker", "system", "df", "--format", "table {{.Type}}\t{{.Size}}\t{{.Reclaimable}}"], timeout=10)
    if rc != 0:
        return None
    return {
        "name": "Docker Disk Usage",
        "status": "pass",
        "detail": out.strip()[:60] if out.strip() else "Docker running",
        "value": 0,
    }


def _check_battery():
    bat = psutil.sensors_battery()
    if not bat:
        return None
    out, _, rc = run_cmd(["system_profiler", "SPPowerDataType"], timeout=5)
    cycles = 0
    health = 100
    if rc == 0:
        for line in out.splitlines():
            line = line.strip()
            if "Cycle Count" in line:
                try:
                    cycles = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
            if "Maximum Capacity" in line:
                try:
                    health = int(line.split(":")[-1].strip().replace("%", ""))
                except ValueError:
                    pass
    status = "fail" if health < 70 else "warn" if health < 85 or cycles > 800 else "pass"
    return {
        "name": "Battery Health",
        "status": status,
        "detail": f"Health: {health}% | Cycles: {cycles}",
        "value": health,
    }


def _get_last_clean_time():
    try:
        db = get_db()
        row = db.execute(
            "SELECT timestamp FROM scan_history WHERE scan_type='clean' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        db.close()
        if row:
            from datetime import datetime
            dt = datetime.fromisoformat(row["timestamp"])
            return dt.timestamp()
    except Exception:
        pass
    return None


def _check_macos_updates():
    out, _, rc = run_cmd(["softwareupdate", "-l"], timeout=30)
    if rc != 0:
        return None
    count = sum(1 for line in out.splitlines() if "Label:" in line or "* " in line)
    return count


def _auto_fix(checks: list[dict]):
    console.print("\n[bold yellow]Auto-fixing safe issues...[/]")
    for c in checks:
        if c["status"] != "pass":
            name = c["name"]
            if name == "Zombie Processes" and c["value"] > 0:
                from .processes import _kill_zombies
                _kill_zombies(force_yes=True)
            elif name == "Quarantine DB" and c["value"] > 10 * 1024 * 1024:
                from .privacy import _execute_wipe
                _execute_wipe("clear_quarantine", {}, 0)
                console.print("  [green]Cleared quarantine DB[/]")
    console.print("[green]Auto-fix complete.[/]")


# ── Report ───────────────────────────────────────────────────────────────

def _save_report(score: int, checks: list[dict]):
    from .utils import REPORTS_DIR, ensure_dirs
    ensure_dirs()
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = REPORTS_DIR / f"{ts}_health.txt"

    lines = [
        f"macmon Health Report - {ts}",
        f"Score: {score}/100",
        "=" * 60,
    ]
    for c in checks:
        lines.append(f"[{c['status'].upper()}] {c['name']}: {c['detail']}")

    path.write_text("\n".join(lines))
    console.print(f"[green]Report saved to {path}[/]")


def run_report(full: bool = False, tail: bool = False, save: bool = False):
    if tail:
        from .utils import LOG_PATH
        if LOG_PATH.exists():
            lines = LOG_PATH.read_text().splitlines()
            console.print(Panel("\n".join(lines[-20:]), title="Last 20 Log Events", border_style="dim"))
        else:
            console.print("[dim]No log file found.[/]")
        return

    if full:
        run_health(report=save)
        return

    # Session summary
    console.print(Panel("[bold]Session Report[/]", border_style="cyan"))
    cpu_pct = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    table = Table(box=None)
    table.add_column("Metric", style="cyan")
    table.add_column("Value")
    table.add_row("CPU", f"{cpu_pct:.1f}%")
    table.add_row("RAM", f"{mem.percent:.1f}% ({format_size(mem.used)})")
    table.add_row("Disk", f"{format_size(disk.free)} free")
    table.add_row("Uptime", f"{(time.time() - psutil.boot_time()) / 3600:.1f} hours")
    console.print(table)

    # Recent actions from DB
    try:
        db = get_db()
        rows = db.execute(
            "SELECT scan_type, timestamp, freed_size FROM scan_history ORDER BY id DESC LIMIT 5"
        ).fetchall()
        db.close()
        if rows:
            console.print("\n[bold]Recent Actions:[/]")
            for r in rows:
                console.print(f"  {r['timestamp']} | {r['scan_type']} | freed {format_size(r['freed_size'])}")
    except Exception:
        pass

    if save:
        _save_report(0, [])
