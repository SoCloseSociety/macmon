"""Full app uninstaller with leftover detection for macmon."""

import json
import os
import re
import shutil
import signal
import time
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .utils import confirm_action, console, dir_size, format_size, log_action, run_cmd
from .platform_compat import require_os

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


def _trash_or_rm(path: Path, permanent: bool = False) -> bool:
    """Move to Trash (or delete permanently). Returns True if removed."""
    if permanent:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return True
    if send2trash:
        try:
            send2trash(str(path))
            return True
        except Exception:
            console.print(f"  [yellow]Skipped (Trash unavailable): {path}[/]")
            return False
    console.print(f"  [yellow]Skipped (Trash unavailable): {path}[/]")
    return False


def run_uninstaller(
    app_name: str = None,
    scan_only: bool = False,
    list_apps: bool = False,
    permanent: bool = False,
    force_yes: bool = False,
):
    msg = require_os("macOS")
    if msg:
        console.print(f"[yellow]{msg}[/]")
        return
    if list_apps:
        _list_all_apps()
        return

    if not app_name:
        console.print("[yellow]Usage: macmon uninstall <AppName> or macmon uninstall --list[/]")
        return

    console.print(Panel(f"[bold]macmon uninstall[/] -- {app_name}", border_style="red"))

    leftovers = _find_leftovers(app_name)

    if not leftovers:
        console.print(f"[yellow]No files found for '{app_name}'[/]")
        return

    total_size = sum(l["size"] for l in leftovers)

    table = Table(title=f"Leftovers for {app_name}", border_style="red")
    table.add_column("Type", style="cyan", width=20)
    table.add_column("Path", width=50)
    table.add_column("Size", justify="right", width=12)

    for l in leftovers:
        table.add_row(l["type"], str(l["path"]), format_size(l["size"]))

    table.add_row("[bold]TOTAL[/]", f"[bold]{len(leftovers)} items[/]", f"[bold]{format_size(total_size)}[/]")
    console.print(table)

    if scan_only:
        console.print("\n[dim]Preview only. Remove --scan to uninstall.[/]")
        return

    if permanent and not force_yes:
        console.print("[red bold]WARNING: --permanent will PERMANENTLY DELETE (no Trash)![/]")

    if confirm_action(f"Uninstall {app_name} ({format_size(total_size)})?", force_yes=force_yes):
        # Kill running processes first
        _kill_app_processes(app_name)

        # Unload launch agents/daemons
        for l in leftovers:
            if l["type"] in ("LaunchAgent", "LaunchDaemon"):
                run_cmd(["launchctl", "unload", str(l["path"])], timeout=5)

        # Delete everything
        deleted = 0
        freed = 0
        for l in leftovers:
            try:
                if _trash_or_rm(l["path"], permanent):
                    deleted += 1
                    freed += l["size"]
            except (OSError, PermissionError) as e:
                console.print(f"  [red]Failed: {l['path']}: {e}[/]")

        console.print(f"\n[green bold]Uninstalled {app_name}: {deleted} items removed, {format_size(freed)} freed[/]")
        log_action("uninstall", f"{app_name}: {deleted} items, {format_size(freed)}")


def _matches_app(entry_name: str, name_variants: list[str]) -> bool:
    """Strict match: exact name, name + separator prefix, or full bundle-id.

    Prefix matching requires the variant to be >= 5 chars to avoid
    matching unrelated apps' data. Case-insensitive.
    """
    entry_lower = entry_name.lower()
    if entry_lower.endswith(".plist"):
        entry_lower = entry_lower[:-6]
    for v in name_variants:
        v_lower = v.lower()
        if entry_lower == v_lower:
            return True
        if len(v_lower) >= 5 and entry_lower.startswith((v_lower + ".", v_lower + " ", v_lower + "-")):
            return True
    return False


def _find_leftovers(app_name: str) -> list[dict]:
    leftovers = []
    home = Path.home()

    # Derive possible identifiers
    name_lower = app_name.lower().replace(" ", "").replace(".app", "")
    name_variants = [
        app_name,
        app_name.replace(" ", ""),
        name_lower,
    ]

    # Try to find bundle identifier from the .app
    bundle_id = _get_bundle_id(app_name)
    if bundle_id:
        name_variants.append(bundle_id)

    # Main .app bundle
    for app_dir in [Path("/Applications"), home / "Applications"]:
        for variant in [app_name, app_name + ".app"]:
            app_path = app_dir / variant
            if not app_path.exists() and not variant.endswith(".app"):
                app_path = app_dir / (variant + ".app")
            if app_path.exists():
                leftovers.append({
                    "type": "Application",
                    "path": app_path,
                    "size": dir_size(app_path),
                })

    # Library locations to search
    lib_searches = [
        ("App Support", home / "Library/Application Support"),
        ("Preferences", home / "Library/Preferences"),
        ("Caches", home / "Library/Caches"),
        ("Logs", home / "Library/Logs"),
        ("Saved State", home / "Library/Saved Application State"),
        ("Containers", home / "Library/Containers"),
        ("Group Containers", home / "Library/Group Containers"),
        ("HTTPStorages", home / "Library/HTTPStorages"),
        ("WebKit", home / "Library/WebKit"),
        ("App Scripts", home / "Library/Application Scripts"),
        ("Cookies", home / "Library/Cookies"),
    ]

    for type_name, base_dir in lib_searches:
        if not base_dir.exists():
            continue
        try:
            for entry in base_dir.iterdir():
                if _matches_app(entry.name, name_variants):
                    s = dir_size(entry) if entry.is_dir() else (entry.stat().st_size if entry.is_file() else 0)
                    leftovers.append({"type": type_name, "path": entry, "size": s})
        except (OSError, PermissionError):
            continue

    # LaunchAgents/Daemons
    for type_name, la_dir in [
        ("LaunchAgent", home / "Library/LaunchAgents"),
        ("LaunchAgent", Path("/Library/LaunchAgents")),
        ("LaunchDaemon", Path("/Library/LaunchDaemons")),
    ]:
        if not la_dir.exists():
            continue
        try:
            for plist in la_dir.glob("*.plist"):
                if _matches_app(plist.name, name_variants):
                    leftovers.append({
                        "type": type_name,
                        "path": plist,
                        "size": plist.stat().st_size,
                    })
        except (OSError, PermissionError):
            continue

    # Config dirs
    for config_base in [home / ".config", home / ".local/share"]:
        if config_base.exists():
            try:
                for entry in config_base.iterdir():
                    if _matches_app(entry.name, name_variants):
                        s = dir_size(entry) if entry.is_dir() else entry.stat().st_size
                        leftovers.append({"type": "Config", "path": entry, "size": s})
            except (OSError, PermissionError):
                continue

    # Deduplicate by resolved path
    seen = set()
    unique = []
    for l in leftovers:
        try:
            key = str(Path(l["path"]).resolve())
        except OSError:
            key = str(l["path"])
        if key not in seen:
            seen.add(key)
            unique.append(l)

    return unique


def _get_bundle_id(app_name: str) -> str:
    for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
        app_path = app_dir / f"{app_name}.app"
        if not app_path.exists():
            app_path = app_dir / app_name
        if app_path.exists():
            plist = app_path / "Contents/Info.plist"
            if plist.exists():
                out, _, rc = run_cmd(
                    ["defaults", "read", str(plist), "CFBundleIdentifier"],
                    timeout=5,
                )
                if rc == 0 and out.strip():
                    return out.strip()
    return ""


def _get_bundle_executable(app_name: str) -> str:
    for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
        app_path = app_dir / f"{app_name}.app"
        if not app_path.exists():
            app_path = app_dir / app_name
        plist = app_path / "Contents/Info.plist"
        if plist.exists():
            out, _, rc = run_cmd(
                ["defaults", "read", str(plist), "CFBundleExecutable"],
                timeout=5,
            )
            if rc == 0 and out.strip():
                return out.strip()
    return ""


def _kill_app_processes(app_name: str):
    # Exact process names only (case-insensitive): app name and its
    # CFBundleExecutable. Never kill by substring, never kill ourselves.
    my_pid = os.getpid()
    targets = {app_name.lower(), app_name.lower().replace(".app", "")}
    exe = _get_bundle_executable(app_name)
    if exe:
        targets.add(exe.lower())

    killed = 0
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if p.info["pid"] == my_pid:
                continue
            if (p.info["name"] or "").lower() in targets:
                p.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if killed > 0:
        console.print(f"[yellow]Terminated {killed} running processes[/]")
        time.sleep(2)  # Wait for graceful shutdown

        # SIGKILL stragglers
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if p.info["pid"] == my_pid:
                    continue
                if (p.info["name"] or "").lower() in targets:
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue


def _list_all_apps():
    console.print(Panel("[bold]Installed Applications[/]", border_style="cyan"))

    apps = []
    for app_dir in [Path("/Applications"), Path.home() / "Applications"]:
        if not app_dir.exists():
            continue
        for app in sorted(app_dir.glob("*.app")):
            try:
                size = dir_size(app)
                apps.append({"name": app.stem, "path": str(app), "size": size})
            except (OSError, PermissionError):
                apps.append({"name": app.stem, "path": str(app), "size": 0})

    apps.sort(key=lambda x: x["size"], reverse=True)

    table = Table(title="Applications by Size", border_style="cyan")
    table.add_column("Application", width=30)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Path", style="dim", width=40)

    for app in apps:
        table.add_row(app["name"], format_size(app["size"]), app["path"])

    console.print(table)
    console.print(f"\n[dim]{len(apps)} applications found[/]")
