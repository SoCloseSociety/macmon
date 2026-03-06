#!/usr/bin/env python3
"""macmon - Mac Developer Monitor + System Cleaner CLI."""

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

# Ensure modules are importable
sys.path.insert(0, str(Path(__file__).parent))

app = typer.Typer(
    name="macmon",
    help="Mac Developer Monitor + System Cleaner -- CCleaner Pro level, 100% local.",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console()


# ── Dashboard (default command) ──────────────────────────────────────────

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Launch the live dashboard if no subcommand given."""
    if ctx.invoked_subcommand is None:
        from modules.dashboard import run_dashboard
        run_dashboard()


@app.command()
def dashboard(
    refresh: int = typer.Option(2, "--refresh", "-r", help="Refresh interval in seconds"),
):
    """Live system monitoring dashboard."""
    from modules.dashboard import run_dashboard
    run_dashboard(refresh=refresh)


# ── Process Manager ──────────────────────────────────────────────────────

@app.command()
def ps(
    filter: Optional[str] = typer.Option(None, "--filter", "-f", help="Filter by category"),
    sort: str = typer.Option("cpu", "--sort", "-s", help="Sort by: cpu, ram, name, runtime"),
    tree: bool = typer.Option(False, "--tree", help="Show process hierarchy"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """List dev-relevant processes."""
    from modules.processes import list_processes
    list_processes(filter_cat=filter, sort_by=sort, tree=tree, json_out=json_out)


@app.command()
def kill(
    target: str = typer.Argument(..., help="Process name or PID"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Kill entire category"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Kill a process by name or PID."""
    from modules.processes import kill_process
    kill_process(target=target, category=category, force_yes=yes)


@app.command()
def suspend(target: str = typer.Argument(..., help="Process name or PID")):
    """Suspend (SIGSTOP) a process."""
    from modules.processes import suspend_process
    suspend_process(target)


@app.command()
def resume(target: str = typer.Argument(..., help="Process name or PID")):
    """Resume (SIGCONT) a suspended process."""
    from modules.processes import resume_process
    resume_process(target)


@app.command()
def nice(
    target: str = typer.Argument(..., help="Process name or PID"),
    value: int = typer.Argument(..., help="Nice value (-20 to 19)"),
):
    """Renice a process."""
    from modules.processes import renice_process
    renice_process(target, value)


@app.command()
def quit(app_name: str = typer.Argument(..., help="Application name")):
    """Gracefully quit a macOS app via osascript."""
    from modules.processes import quit_app
    quit_app(app_name)


@app.command()
def restart(app_name: str = typer.Argument(..., help="Application name")):
    """Quit and reopen a macOS app."""
    from modules.processes import restart_app
    restart_app(app_name)


# ── Sweep (zombie/orphan/port killer) ───────────────────────────────────

@app.command()
def sweep(
    zombies: bool = typer.Option(False, "--zombies", help="Kill zombies only"),
    orphans: bool = typer.Option(False, "--orphans", help="Kill orphans only"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Hunt and kill zombie/orphan processes + stale locks."""
    from modules.processes import run_sweep
    run_sweep(zombies_only=zombies, orphans_only=orphans, force_yes=yes)


@app.command()
def ports(
    free: Optional[int] = typer.Option(None, "--free", help="Free a specific port"),
    free_all_dev: bool = typer.Option(False, "--free-all-dev", help="Free all common dev ports"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Show port usage and manage dev ports."""
    from modules.processes import manage_ports
    manage_ports(free_port=free, free_all=free_all_dev, force_yes=yes)


# ── System Cleaner ──────────────────────────────────────────────────────

@app.command()
def clean(
    scan: bool = typer.Option(False, "--scan", help="Preview only, no deletion"),
    run: bool = typer.Option(False, "--run", help="Interactive clean"),
    all_clean: bool = typer.Option(False, "--all", help="Clean all safe categories"),
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Run specific module"),
    browsers: bool = typer.Option(False, "--browsers", help="Browser cleaner"),
    all_browsers: bool = typer.Option(False, "--all-browsers", help="All browsers"),
    browser: Optional[str] = typer.Option(None, "--browser", help="Specific browser"),
    cookies: bool = typer.Option(False, "--cookies", help="Clean cookies"),
    cache: bool = typer.Option(False, "--cache", help="Clean cache"),
    clipboard: bool = typer.Option(False, "--clipboard", help="Clear clipboard"),
    recent: bool = typer.Option(False, "--recent", help="Clear recent items"),
    schedule: bool = typer.Option(False, "--schedule", help="Set auto-clean schedule"),
    permanent: bool = typer.Option(False, "--permanent", help="Use rm -rf instead of Trash"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """CCleaner-equivalent system cleaner."""
    from modules.cleaner import run_cleaner
    run_cleaner(
        scan=scan, run=run, all_clean=all_clean, module=module,
        browsers=browsers, all_browsers=all_browsers, browser=browser,
        cookies=cookies, cache=cache, clipboard=clipboard, recent=recent,
        schedule=schedule, permanent=permanent, force_yes=yes, json_out=json_out,
    )


# ── Dev Garbage Collector ────────────────────────────────────────────────

@app.command()
def gc(
    scan: bool = typer.Option(False, "--scan", help="Preview only"),
    clean_gc: bool = typer.Option(False, "--clean", help="Interactive clean"),
    all_gc: bool = typer.Option(False, "--all", help="Full auto nuke"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    permanent: bool = typer.Option(False, "--permanent", help="Use rm -rf"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Dev garbage collector: node_modules, venvs, docker, caches."""
    from modules.gc import run_gc
    run_gc(scan=scan, clean=clean_gc, all_gc=all_gc, force_yes=yes, permanent=permanent, json_out=json_out)


# ── Privacy Cleaner ─────────────────────────────────────────────────────

@app.command()
def privacy(
    scan: bool = typer.Option(False, "--scan", help="List traces"),
    clean_priv: bool = typer.Option(False, "--clean", help="Interactive clean"),
    full: bool = typer.Option(False, "--full", help="Wipe all traces"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Privacy traces wiper."""
    from modules.privacy import run_privacy
    run_privacy(scan=scan, clean=clean_priv, full=full, force_yes=yes)


# ── Health Check ─────────────────────────────────────────────────────────

@app.command()
def health(
    fix: bool = typer.Option(False, "--fix", help="Auto-fix safe issues"),
    report: bool = typer.Option(False, "--report", help="Save report"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Full system health check with /100 score."""
    from modules.health import run_health
    run_health(fix=fix, report=report, json_out=json_out)


# ── Startup Manager ─────────────────────────────────────────────────────

@app.command()
def startup(
    list_items: bool = typer.Option(False, "--list", "-l", help="List all startup items"),
    disable: Optional[str] = typer.Option(None, "--disable", help="Disable an item"),
    enable: Optional[str] = typer.Option(None, "--enable", help="Enable an item"),
    delete: Optional[str] = typer.Option(None, "--delete", help="Delete an item"),
    broken: bool = typer.Option(False, "--broken", help="Show broken items"),
    audit: bool = typer.Option(False, "--audit", help="Flag suspicious items"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Manage startup/login items."""
    from modules.startup import run_startup
    run_startup(
        list_items=list_items, disable=disable, enable=enable,
        delete=delete, broken=broken, audit=audit, force_yes=yes,
    )


# ── App Uninstaller ─────────────────────────────────────────────────────

@app.command()
def uninstall(
    app_name: Optional[str] = typer.Argument(None, help="Application name"),
    scan_only: bool = typer.Option(False, "--scan", help="Show leftovers only"),
    list_apps: bool = typer.Option(False, "--list", "-l", help="List all apps"),
    permanent: bool = typer.Option(False, "--permanent", help="Use rm -rf"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Full app uninstaller with leftover detection."""
    from modules.uninstaller import run_uninstaller
    run_uninstaller(
        app_name=app_name, scan_only=scan_only, list_apps=list_apps,
        permanent=permanent, force_yes=yes,
    )


# ── Duplicate Finder ────────────────────────────────────────────────────

@app.command()
def dupes(
    paths: Optional[list[str]] = typer.Argument(None, help="Directories to scan"),
    scan: bool = typer.Option(False, "--scan", help="Preview only"),
    auto_keep_newest: bool = typer.Option(False, "--auto-keep-newest"),
    auto_keep_oldest: bool = typer.Option(False, "--auto-keep-oldest"),
    keep_in: Optional[str] = typer.Option(None, "--keep-in", help="Keep files in this path"),
    empty_dirs: bool = typer.Option(False, "--empty-dirs", help="Find empty directories"),
    broken_symlinks: bool = typer.Option(False, "--broken-symlinks", help="Find broken symlinks"),
    permanent: bool = typer.Option(False, "--permanent", help="Use rm -rf"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Duplicate file finder."""
    from modules.duplicates import run_dupes
    target_paths = paths or [str(Path.home())]
    run_dupes(
        paths=target_paths, scan=scan, auto_keep_newest=auto_keep_newest,
        auto_keep_oldest=auto_keep_oldest, keep_in=keep_in,
        empty_dirs=empty_dirs, broken_symlinks=broken_symlinks,
        permanent=permanent, force_yes=yes,
    )


# ── Large File Finder ───────────────────────────────────────────────────

@app.command()
def bigfiles(
    path: str = typer.Argument(str(Path.home()), help="Directory to scan"),
    min_size: str = typer.Option("50MB", "--min", help="Minimum file size"),
    file_type: Optional[str] = typer.Option(None, "--type", help="Filter by extension"),
    older: Optional[int] = typer.Option(None, "--older", help="Days since last access"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Find large files."""
    from modules.disk import find_big_files
    find_big_files(path=path, min_size=min_size, file_type=file_type, older=older, json_out=json_out)


# ── Disk Analyzer ───────────────────────────────────────────────────────

@app.command()
def disk(
    path: str = typer.Argument(str(Path.home()), help="Directory to analyze"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Disk usage analyzer."""
    from modules.disk import analyze_disk
    analyze_disk(path=path, json_out=json_out)


# ── Network Monitor ─────────────────────────────────────────────────────

@app.command()
def network(
    listening: bool = typer.Option(False, "--listening", help="Only listening ports"),
    established: bool = typer.Option(False, "--established", help="Only active connections"),
    process: Optional[str] = typer.Option(None, "--process", help="Filter by process"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Network connections monitor."""
    from modules.network import run_network
    run_network(listening=listening, established=established, process=process, json_out=json_out)


@app.command(name="flush-dns")
def flush_dns():
    """Flush DNS cache."""
    from modules.network import flush_dns_cache
    flush_dns_cache()


# ── Autopilot Daemon ────────────────────────────────────────────────────

@app.command()
def auto(
    start: bool = typer.Option(False, "--start", help="Start daemon"),
    stop: bool = typer.Option(False, "--stop", help="Stop daemon"),
    status: bool = typer.Option(False, "--status", help="Show status"),
    log: bool = typer.Option(False, "--log", help="Tail log"),
):
    """Background autopilot daemon."""
    from modules.autopilot import run_autopilot
    run_autopilot(start=start, stop=stop, status=status, log=log)


# ── Focus Mode ──────────────────────────────────────────────────────────

@app.command()
def focus():
    """Enter focus mode: quit non-essentials, purge RAM."""
    from modules.autopilot import enter_focus
    enter_focus()


@app.command()
def restore():
    """Restore apps killed by focus mode."""
    from modules.autopilot import restore_focus
    restore_focus()


# ── Purge RAM ────────────────────────────────────────────────────────────

@app.command()
def purge():
    """Purge inactive RAM (sudo purge)."""
    from modules.processes import purge_ram
    purge_ram()


# ── Report ───────────────────────────────────────────────────────────────

@app.command()
def report(
    full: bool = typer.Option(False, "--full", help="Full report"),
    tail: bool = typer.Option(False, "--tail", help="Last 20 log events"),
    save: bool = typer.Option(False, "--save", help="Save to file"),
):
    """Session report."""
    from modules.health import run_report
    run_report(full=full, tail=tail, save=save)


# ── Config ───────────────────────────────────────────────────────────────

@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Print full config"),
    init: bool = typer.Option(False, "--init", help="Create default config"),
    set_val: Optional[str] = typer.Option(None, "--set", help="Set key=value"),
    edit: bool = typer.Option(False, "--edit", help="Open in $EDITOR"),
):
    """Manage configuration."""
    from modules.config import show_config, init_config, set_config, edit_config
    if init:
        init_config()
    elif show:
        show_config()
    elif set_val:
        if "=" in set_val:
            k, v = set_val.split("=", 1)
            set_config(k.strip(), v.strip())
        else:
            console.print("[red]Use format: --set key=value[/red]")
    elif edit:
        edit_config()
    else:
        show_config()


# ── Security ─────────────────────────────────────────────────────────────

@app.command()
def security(
    scan: bool = typer.Option(False, "--scan", help="Full security audit with score"),
    connections: bool = typer.Option(False, "--connections", help="Scan active connections"),
    firewall: bool = typer.Option(False, "--firewall", help="Firewall status"),
    malware: bool = typer.Option(False, "--malware", help="Malware indicator scan"),
    remote: bool = typer.Option(False, "--remote", help="Remote access tool detection"),
    rules: bool = typer.Option(False, "--rules", help="Show active security rules"),
    block_ip: Optional[str] = typer.Option(None, "--block-ip", help="Block an IP address"),
    unblock_ip: Optional[str] = typer.Option(None, "--unblock-ip", help="Unblock an IP address"),
    quarantine: Optional[str] = typer.Option(None, "--quarantine", help="Kill + block a process"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Network security, malware detection & remote access monitor."""
    from modules.security import run_security
    run_security(
        scan=scan, connections=connections, firewall=firewall,
        malware=malware, remote=remote, rules=rules,
        block_ip=block_ip, unblock_ip=unblock_ip,
        quarantine=quarantine, json_out=json_out,
    )


# ── Docker Management ───────────────────────────────────────────────────

@app.command()
def docker(
    status: bool = typer.Option(False, "--status", help="Docker overview"),
    containers: bool = typer.Option(False, "--containers", help="List all containers"),
    images: bool = typer.Option(False, "--images", help="List images"),
    volumes: bool = typer.Option(False, "--volumes", help="List volumes"),
    networks: bool = typer.Option(False, "--networks", help="List networks"),
    prune: bool = typer.Option(False, "--prune", help="Full Docker cleanup"),
    stop_all: bool = typer.Option(False, "--stop-all", help="Stop all containers"),
    restart_ctr: Optional[str] = typer.Option(None, "--restart", help="Restart a container"),
    logs: Optional[str] = typer.Option(None, "--logs", help="Tail container logs"),
    stats: bool = typer.Option(False, "--stats", help="Live container stats"),
    compose: bool = typer.Option(False, "--compose", help="List Compose projects"),
    scan: bool = typer.Option(False, "--scan", help="Docker security audit"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_out: bool = typer.Option(False, "--json", help="JSON output"),
):
    """Docker container, image & volume management."""
    from modules.docker_mgr import run_docker
    run_docker(
        status=status, containers=containers, images=images,
        volumes=volumes, networks=networks, prune=prune,
        stop_all=stop_all, restart=restart_ctr, logs=logs,
        stats=stats, compose=compose, scan=scan,
        yes=yes, json_out=json_out,
    )


if __name__ == "__main__":
    app()
