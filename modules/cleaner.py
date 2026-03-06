"""CCleaner-equivalent system cleaner for macmon."""

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .config import load_config
from .utils import (
    confirm_action,
    console,
    dir_size,
    format_size,
    get_db,
    log_action,
    run_cmd,
    safe_stat,
)

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


def _trash_or_rm(path: Path, permanent: bool = False):
    if permanent:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    elif send2trash:
        try:
            send2trash(str(path))
        except Exception:
            path.unlink(missing_ok=True) if path.is_file() else shutil.rmtree(path, ignore_errors=True)
    else:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


# ── Browser paths ────────────────────────────────────────────────────────

BROWSER_PATHS = {
    "chrome": Path.home() / "Library/Application Support/Google/Chrome",
    "chromium": Path.home() / "Library/Application Support/Chromium",
    "safari": Path.home() / "Library/Safari",
    "firefox": Path.home() / "Library/Application Support/Firefox/Profiles",
    "arc": Path.home() / "Library/Application Support/Arc",
    "brave": Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser",
    "opera": Path.home() / "Library/Application Support/com.operasoftware.Opera",
    "edge": Path.home() / "Library/Application Support/Microsoft Edge",
}

BROWSER_CLEAN_PATTERNS = {
    "cache": ["Cache", "Code Cache", "GPUCache", "Media Cache", "ShaderCache", "GrShaderCache"],
    "cookies": ["Cookies", "Cookies.bak"],
    "history": ["History", "Archived History"],
    "sessions": ["Sessions", "Last Session", "Last Tabs"],
    "storage": ["Local Storage", "Session Storage", "IndexedDB", "Service Worker"],
    "crash": ["Crashpad/reports"],
    "misc": ["Top Sites", "Thumbnails", "Favicons", "Web Data"],
}

SAFARI_EXTRA = {
    "cache": [
        Path.home() / "Library/Caches/com.apple.Safari",
        Path.home() / "Library/WebKit/com.apple.Safari",
    ],
}

# ── App-specific cleaners ────────────────────────────────────────────────

APP_CLEANERS = {
    "xcode_derived": {
        "name": "Xcode DerivedData",
        "path": Path.home() / "Library/Developer/Xcode/DerivedData",
    },
    "xcode_archives": {
        "name": "Xcode Archives",
        "path": Path.home() / "Library/Developer/Xcode/Archives",
    },
    "xcode_device_support": {
        "name": "Xcode Device Support",
        "path": Path.home() / "Library/Developer/Xcode/iOS DeviceSupport",
    },
    "sim_caches": {
        "name": "Simulator Caches",
        "path": Path.home() / "Library/Developer/CoreSimulator/Caches",
    },
    "vscode_vsix": {
        "name": "VSCode Cached VSIX",
        "path": Path.home() / "Library/Application Support/Code/CachedExtensionVSIXs",
    },
    "vscode_logs": {
        "name": "VSCode Logs",
        "path": Path.home() / "Library/Application Support/Code/logs",
    },
    "vscode_cached": {
        "name": "VSCode CachedData",
        "path": Path.home() / "Library/Application Support/Code/CachedData",
    },
    "vscode_obsolete": {
        "name": "VSCode Obsolete Extensions",
        "path": Path.home() / ".vscode/extensions/.obsolete",
    },
    "cursor_obsolete": {
        "name": "Cursor Obsolete",
        "path": Path.home() / ".cursor/extensions/.obsolete",
    },
    "slack_cache": {
        "name": "Slack Cache",
        "path": Path.home() / "Library/Application Support/Slack/Cache",
    },
    "spotify_cache": {
        "name": "Spotify Cache",
        "path": Path.home() / "Library/Application Support/Spotify/PersistentCache",
    },
    "jetbrains_cache": {
        "name": "JetBrains Caches",
        "path": Path.home() / "Library/Caches/JetBrains",
    },
    "gradle_cache": {
        "name": "Gradle Caches",
        "path": Path.home() / ".gradle/caches",
    },
    "maven_repo": {
        "name": "Maven Repository",
        "path": Path.home() / ".m2/repository",
    },
    "cocoapods_cache": {
        "name": "CocoaPods Cache",
        "path": Path.home() / "Library/Caches/CocoaPods",
    },
    "cargo_cache": {
        "name": "Cargo Cache",
        "path": Path.home() / ".cargo/registry/cache",
    },
    "gem_cache": {
        "name": "Ruby Gem Cache",
        "path": Path.home() / ".gem",
    },
    "zoom_cache": {
        "name": "Zoom Cache",
        "path": Path.home() / "Library/Application Support/zoom.us/data",
    },
}


# ── Main cleaner entry point ─────────────────────────────────────────────

def run_cleaner(
    scan: bool = False,
    run: bool = False,
    all_clean: bool = False,
    module: str = None,
    browsers: bool = False,
    all_browsers: bool = False,
    browser: str = None,
    cookies: bool = False,
    cache: bool = False,
    clipboard: bool = False,
    recent: bool = False,
    schedule: bool = False,
    permanent: bool = False,
    force_yes: bool = False,
    json_out: bool = False,
):
    if permanent and not force_yes:
        console.print("[red bold]WARNING: --permanent will PERMANENTLY DELETE files (no Trash)![/]")
        if not confirm_action("Are you sure you want permanent deletion?"):
            return

    if clipboard:
        _clean_clipboard()
        return

    if recent:
        _clean_recent_items(force_yes=force_yes)
        return

    if schedule:
        _setup_schedule()
        return

    if browsers or all_browsers or browser:
        _clean_browsers(
            scan_only=scan, all_browsers=all_browsers, browser_name=browser,
            cookies=cookies, cache=cache, permanent=permanent,
            force_yes=force_yes, json_out=json_out,
        )
        return

    if module:
        _clean_module(module, scan_only=scan, permanent=permanent, force_yes=force_yes)
        return

    # Full system clean
    results = []

    console.print(Panel("[bold]macmon clean[/] -- System Cleaner", border_style="cyan"))

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scanning system junk...", total=None)
        junk = _scan_system_junk()
        results.extend(junk)
        progress.update(task, description="Scanning browser caches...")
        browser_junk = _scan_all_browsers()
        results.extend(browser_junk)
        progress.update(task, description="Scanning app caches...")
        app_junk = _scan_app_caches()
        results.extend(app_junk)
        progress.update(task, description="Scanning user caches...")
        user_cache = _scan_user_caches()
        results.extend(user_cache)
        progress.remove_task(task)

    # Display results
    total_size = sum(r["size"] for r in results)
    total_files = sum(r["count"] for r in results)

    table = Table(title="Cleanup Summary", border_style="cyan")
    table.add_column("Category", style="cyan", width=30)
    table.add_column("Items", justify="right", width=8)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Action", width=10)

    for r in sorted(results, key=lambda x: x["size"], reverse=True):
        if r["size"] == 0:
            continue
        table.add_row(r["name"], str(r["count"]), format_size(r["size"]), "[dim]pending[/]")

    table.add_row("[bold]TOTAL[/]", f"[bold]{total_files}[/]", f"[bold]{format_size(total_size)}[/]", "")
    console.print(table)

    if json_out:
        console.print_json(json.dumps(results, default=str))
        return

    if scan:
        console.print("\n[dim]Preview only. Use --run or --all to clean.[/]")
        return

    if total_size == 0:
        console.print("[green]System is already clean![/]")
        return

    if all_clean:
        if confirm_action(f"Clean {format_size(total_size)} across {total_files} items?", force_yes=force_yes):
            _execute_clean(results, permanent)
    elif run:
        _interactive_clean(results, permanent)


def _execute_clean(results: list[dict], permanent: bool = False):
    total_freed = 0
    for r in results:
        for path in r.get("paths", []):
            try:
                p = Path(path)
                if p.exists():
                    _trash_or_rm(p, permanent)
                    total_freed += r["size"]
            except (OSError, PermissionError) as e:
                console.print(f"[red]  Error: {e}[/]")

    console.print(f"\n[green bold]Cleaned ~{format_size(total_freed)}[/]")
    log_action("clean", f"freed ~{format_size(total_freed)}")

    db = get_db()
    db.execute(
        "INSERT INTO scan_history (scan_type, total_size, freed_size) VALUES (?, ?, ?)",
        ("clean", sum(r["size"] for r in results), total_freed),
    )
    db.commit()
    db.close()


def _interactive_clean(results: list[dict], permanent: bool = False):
    total_freed = 0
    for r in sorted(results, key=lambda x: x["size"], reverse=True):
        if r["size"] == 0:
            continue
        if confirm_action(f"  Clean {r['name']} ({format_size(r['size'])})?"):
            for path in r.get("paths", []):
                try:
                    p = Path(path)
                    if p.exists():
                        _trash_or_rm(p, permanent)
                except (OSError, PermissionError):
                    pass
            total_freed += r["size"]
            console.print(f"  [green]Cleaned {r['name']}[/]")

    console.print(f"\n[green bold]Total freed: ~{format_size(total_freed)}[/]")
    log_action("clean_interactive", f"freed ~{format_size(total_freed)}")


# ── System Junk Scanner ──────────────────────────────────────────────────

def _scan_system_junk() -> list[dict]:
    results = []
    cfg = load_config()
    max_age_days = cfg.get("cleaner", {}).get("log_max_age_days", 7)
    cutoff = time.time() - (max_age_days * 86400)

    # User logs
    user_logs = Path.home() / "Library/Logs"
    if user_logs.exists():
        size, count, paths = _scan_old_files(user_logs, cutoff)
        results.append({"name": "User Logs (old)", "size": size, "count": count, "paths": paths})

    # Crash reports
    for crash_dir in [
        Path.home() / "Library/Logs/DiagnosticReports",
        Path("/Library/Logs/DiagnosticReports"),
    ]:
        if crash_dir.exists():
            size, count, paths = _scan_dir_all(crash_dir)
            results.append({"name": f"Crash Reports ({crash_dir.name})", "size": size, "count": count, "paths": paths})

    # User temp
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    tmppath = Path(tmpdir)
    if tmppath.exists():
        one_hour_ago = time.time() - 3600
        size, count, paths = _scan_old_files(tmppath, one_hour_ago)
        results.append({"name": "User Temp Files", "size": size, "count": count, "paths": paths})

    # /private/tmp old files
    private_tmp = Path("/private/tmp")
    if private_tmp.exists():
        day_ago = time.time() - 86400
        size, count, paths = _scan_old_files(private_tmp, day_ago)
        results.append({"name": "System Temp (old)", "size": size, "count": count, "paths": paths})

    return results


def _scan_old_files(directory: Path, cutoff: float) -> tuple[int, int, list[str]]:
    total_size = 0
    count = 0
    paths = []
    try:
        for f in directory.rglob("*"):
            if f.is_file() and not f.is_symlink():
                try:
                    st = f.stat()
                    if st.st_mtime < cutoff:
                        total_size += st.st_size
                        count += 1
                        paths.append(str(f))
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total_size, count, paths


def _scan_dir_all(directory: Path) -> tuple[int, int, list[str]]:
    total_size = 0
    count = 0
    paths = [str(directory)]
    try:
        for f in directory.rglob("*"):
            if f.is_file():
                try:
                    total_size += f.stat().st_size
                    count += 1
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total_size, count, paths


# ── Browser Scanner ──────────────────────────────────────────────────────

def _scan_all_browsers() -> list[dict]:
    results = []
    for name, base_path in BROWSER_PATHS.items():
        if not base_path.exists():
            continue
        size = dir_size(base_path)
        cache_size = 0
        cache_paths = []
        for pattern in BROWSER_CLEAN_PATTERNS.get("cache", []):
            for match in base_path.rglob(pattern):
                if match.is_dir():
                    s = dir_size(match)
                    cache_size += s
                    cache_paths.append(str(match))
        if cache_size > 0:
            results.append({
                "name": f"{name.title()} Cache",
                "size": cache_size,
                "count": len(cache_paths),
                "paths": cache_paths,
            })

        # Crashpad
        for match in base_path.rglob("Crashpad/reports"):
            if match.is_dir():
                s = dir_size(match)
                if s > 0:
                    results.append({
                        "name": f"{name.title()} Crash Reports",
                        "size": s,
                        "count": 1,
                        "paths": [str(match)],
                    })

    # Safari extra
    for cache_path in SAFARI_EXTRA.get("cache", []):
        if cache_path.exists():
            s = dir_size(cache_path)
            if s > 0:
                results.append({
                    "name": "Safari Cache/WebKit",
                    "size": s,
                    "count": 1,
                    "paths": [str(cache_path)],
                })

    return results


def _clean_browsers(
    scan_only: bool = False,
    all_browsers: bool = False,
    browser_name: str = None,
    cookies: bool = False,
    cache: bool = False,
    permanent: bool = False,
    force_yes: bool = False,
    json_out: bool = False,
):
    targets = {}
    if browser_name:
        if browser_name.lower() in BROWSER_PATHS:
            targets = {browser_name.lower(): BROWSER_PATHS[browser_name.lower()]}
        else:
            console.print(f"[red]Unknown browser: {browser_name}[/]")
            console.print(f"[dim]Available: {', '.join(BROWSER_PATHS.keys())}[/]")
            return
    elif all_browsers:
        targets = {k: v for k, v in BROWSER_PATHS.items() if v.exists()}
    else:
        # Interactive selection
        available = {k: v for k, v in BROWSER_PATHS.items() if v.exists()}
        if not available:
            console.print("[yellow]No browsers detected.[/]")
            return
        console.print("[bold]Detected browsers:[/]")
        for name, path in available.items():
            s = dir_size(path)
            console.print(f"  {name.title()}: {format_size(s)}")
        targets = available

    # Determine what to clean
    clean_categories = []
    if cache:
        clean_categories.append("cache")
    if cookies:
        clean_categories.append("cookies")
    if not clean_categories:
        clean_categories = list(BROWSER_CLEAN_PATTERNS.keys())

    results = []
    for name, base_path in targets.items():
        if not base_path.exists():
            continue
        for cat in clean_categories:
            patterns = BROWSER_CLEAN_PATTERNS.get(cat, [])
            for pattern in patterns:
                for match in base_path.rglob(pattern):
                    if match.exists():
                        s = dir_size(match) if match.is_dir() else match.stat().st_size
                        if s > 0:
                            results.append({
                                "name": f"{name.title()} - {cat}/{pattern}",
                                "size": s,
                                "count": 1,
                                "paths": [str(match)],
                            })

    total = sum(r["size"] for r in results)
    table = Table(title="Browser Cleanup", border_style="blue")
    table.add_column("Item", width=40)
    table.add_column("Size", justify="right", width=12)
    for r in sorted(results, key=lambda x: x["size"], reverse=True):
        table.add_row(r["name"], format_size(r["size"]))
    table.add_row("[bold]TOTAL[/]", f"[bold]{format_size(total)}[/]")
    console.print(table)

    if scan_only or total == 0:
        return

    if confirm_action(f"Clean {format_size(total)} of browser data?", force_yes=force_yes):
        _execute_clean(results, permanent)


# ── App Cache Scanner ────────────────────────────────────────────────────

def _scan_app_caches() -> list[dict]:
    results = []
    for key, info in APP_CLEANERS.items():
        path = info["path"]
        if path.exists():
            s = dir_size(path) if path.is_dir() else (path.stat().st_size if path.is_file() else 0)
            if s > 0:
                results.append({
                    "name": info["name"],
                    "size": s,
                    "count": 1,
                    "paths": [str(path)],
                })
    return results


# ── User Cache Scanner ───────────────────────────────────────────────────

def _scan_user_caches() -> list[dict]:
    results = []
    user_caches = Path.home() / "Library/Caches"
    if not user_caches.exists():
        return results

    entries = []
    try:
        for d in user_caches.iterdir():
            if d.is_dir():
                try:
                    s = dir_size(d)
                    entries.append((d, s))
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass

    entries.sort(key=lambda x: x[1], reverse=True)
    top = entries[:20]
    total_size = sum(s for _, s in top)
    paths = [str(d) for d, _ in top]

    if total_size > 0:
        results.append({
            "name": "User Caches (top 20)",
            "size": total_size,
            "count": len(top),
            "paths": paths,
        })

    return results


# ── Module runner ────────────────────────────────────────────────────────

def _clean_module(module_name: str, scan_only: bool = False, permanent: bool = False, force_yes: bool = False):
    if module_name in APP_CLEANERS:
        info = APP_CLEANERS[module_name]
        path = info["path"]
        if not path.exists():
            console.print(f"[yellow]{info['name']} not found at {path}[/]")
            return
        s = dir_size(path)
        console.print(f"[cyan]{info['name']}:[/] {format_size(s)}")
        if scan_only or s == 0:
            return
        if confirm_action(f"Clean {format_size(s)}?", force_yes=force_yes):
            _trash_or_rm(path, permanent)
            console.print(f"[green]Cleaned {info['name']} ({format_size(s)})[/]")
            log_action("clean_module", f"{module_name} freed {format_size(s)}")
    elif module_name == "xcode":
        for key in ["xcode_derived", "xcode_archives", "xcode_device_support", "sim_caches"]:
            _clean_module(key, scan_only, permanent, force_yes)
    elif module_name == "vscode":
        for key in ["vscode_vsix", "vscode_logs", "vscode_cached", "vscode_obsolete"]:
            _clean_module(key, scan_only, permanent, force_yes)
    else:
        console.print(f"[yellow]Unknown module: {module_name}[/]")
        console.print(f"[dim]Available: {', '.join(APP_CLEANERS.keys())}, xcode, vscode[/]")


# ── Special cleaners ────────────────────────────────────────────────────

def _clean_clipboard():
    run_cmd(["pbcopy"], timeout=2)  # pipe empty to clipboard
    os.popen("echo -n '' | pbcopy")
    console.print("[green]Clipboard cleared.[/]")
    log_action("clean_clipboard")


def _clean_recent_items(force_yes: bool = False):
    console.print("[cyan]Clearing recent items...[/]")
    commands = [
        (["defaults", "delete", "com.apple.recentitems"], "Recent Items"),
        (["defaults", "delete", "com.apple.finder", "FXRecentFolders"], "Finder Recent Folders"),
    ]
    for cmd, name in commands:
        out, err, rc = run_cmd(cmd)
        if rc == 0:
            console.print(f"  [green]Cleared {name}[/]")
        else:
            console.print(f"  [dim]{name}: nothing to clear[/]")
    log_action("clean_recent")


def _setup_schedule():
    plist_path = Path.home() / "Library/LaunchAgents/com.macmon.autoclean.plist"
    macmon_path = Path(__file__).parent.parent / "macmon.py"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.macmon.autoclean</string>
    <key>ProgramArguments</key>
    <array>
        <string>{Path.home()}/.macmon/venv/bin/python</string>
        <string>{macmon_path}</string>
        <string>clean</string>
        <string>--all</string>
        <string>-y</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
        <key>Weekday</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{Path.home()}/.macmon/autoclean.log</string>
    <key>StandardErrorPath</key>
    <string>{Path.home()}/.macmon/autoclean.log</string>
</dict>
</plist>
"""
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)
    run_cmd(["launchctl", "load", str(plist_path)])
    console.print(f"[green]Auto-clean scheduled (weekly Sunday 3AM)[/]")
    console.print(f"[dim]Plist: {plist_path}[/]")
    log_action("schedule_clean", str(plist_path))
