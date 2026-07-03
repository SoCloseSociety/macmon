"""CCleaner-equivalent system cleaner for macmon."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

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


def _trash_or_rm(path: Path, permanent: bool = False) -> bool:
    """Delete a path. Returns True only if the path is actually gone.

    Without --permanent, files go to Trash; if Trash fails the path is
    SKIPPED (never silently escalated to permanent deletion).
    """
    try:
        if permanent:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            return True
        if send2trash:
            try:
                send2trash(str(path))
                return True
            except Exception as e:
                console.print(f"[yellow]  Skipped (Trash unavailable): {path} -- {e}[/]")
                console.print("[dim]  Use --permanent to force deletion without Trash.[/]")
                return False
        console.print(f"[yellow]  Skipped (send2trash not installed): {path}[/]")
        return False
    except (OSError, PermissionError) as e:
        console.print(f"[yellow]  Skipped ({e.__class__.__name__}): {path}[/]")
        return False


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
    "misc": ["Top Sites", "Thumbnails", "Favicons"],
}

# Categories safe to clean by default (no logins, autofill, or history lost)
DEFAULT_BROWSER_CATEGORIES = ["cache", "crash"]

# Main HTTP disk caches live under ~/Library/Caches/<vendor>, not App Support
BROWSER_CACHE_DIRS = {
    "chrome": Path.home() / "Library/Caches/Google/Chrome",
    "chromium": Path.home() / "Library/Caches/Chromium",
    "firefox": Path.home() / "Library/Caches/Firefox/Profiles",
    "arc": Path.home() / "Library/Caches/Arc",
    "brave": Path.home() / "Library/Caches/BraveSoftware/Brave-Browser",
    "opera": Path.home() / "Library/Caches/com.operasoftware.Opera",
    "edge": Path.home() / "Library/Caches/Microsoft Edge",
}

# Process names used to refuse cleaning a running browser's databases
BROWSER_PROCESS_NAMES = {
    "chrome": ["Google Chrome"],
    "chromium": ["Chromium"],
    "safari": ["Safari"],
    "firefox": ["firefox"],
    "arc": ["Arc"],
    "brave": ["Brave Browser"],
    "opera": ["Opera"],
    "edge": ["Microsoft Edge"],
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
        "risky": True,  # release dSYMs -- irreplaceable, opt-in only
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
        "risky": True,  # full local repo, expensive to re-download -- opt-in only
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
        # Only the downloaded .gem archives -- NOT ~/.gem itself, which holds
        # installed gems and ~/.gem/credentials (RubyGems API key)
        "path": Path.home() / ".gem/ruby",
        "risky": True,
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
        if scan:
            console.print("[dim]Preview: would clear the clipboard. Run without --scan to apply.[/]")
            return
        _clean_clipboard()
        return

    if recent:
        if scan:
            console.print("[dim]Preview: would clear macOS recent items lists. Run without --scan to apply.[/]")
            return
        _clean_recent_items(force_yes=force_yes)
        return

    if schedule:
        if scan:
            console.print("[dim]Preview: would install the weekly auto-clean LaunchAgent. Run without --scan to apply.[/]")
            return
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
        _interactive_clean(results, permanent, force_yes=force_yes)


def _clean_paths(paths: list[str], permanent: bool = False) -> int:
    """Delete each path, returning the number of bytes actually freed."""
    freed = 0
    for path in paths:
        try:
            p = Path(path)
            if not p.exists():
                continue
            size = dir_size(p) if p.is_dir() and not p.is_symlink() else (safe_stat(p).st_size if safe_stat(p) else 0)
            if _trash_or_rm(p, permanent):
                freed += size
        except (OSError, PermissionError) as e:
            console.print(f"[red]  Error: {e}[/]")
    return freed


def _execute_clean(results: list[dict], permanent: bool = False):
    total_freed = 0
    for r in results:
        total_freed += _clean_paths(r.get("paths", []), permanent)

    console.print(f"\n[green bold]Cleaned ~{format_size(total_freed)}[/]")
    log_action("clean", f"freed ~{format_size(total_freed)}")

    db = get_db()
    db.execute(
        "INSERT INTO scan_history (scan_type, total_size, freed_size) VALUES (?, ?, ?)",
        ("clean", sum(r["size"] for r in results), total_freed),
    )
    db.commit()
    db.close()


def _interactive_clean(results: list[dict], permanent: bool = False, force_yes: bool = False):
    total_freed = 0
    for r in sorted(results, key=lambda x: x["size"], reverse=True):
        if r["size"] == 0:
            continue
        if confirm_action(f"  Clean {r['name']} ({format_size(r['size'])})?", force_yes=force_yes):
            freed = _clean_paths(r.get("paths", []), permanent)
            total_freed += freed
            console.print(f"  [green]Cleaned {r['name']} ({format_size(freed)})[/]")

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

    # Crash reports (individual files only; skip root-owned system dir when not writable)
    for crash_dir in [
        Path.home() / "Library/Logs/DiagnosticReports",
        Path("/Library/Logs/DiagnosticReports"),
    ]:
        if crash_dir.exists() and os.access(crash_dir, os.W_OK):
            size, count, paths = _scan_dir_all(crash_dir)
            results.append({"name": f"Crash Reports ({crash_dir.name})", "size": size, "count": count, "paths": paths})

    # User temp (3+ days old -- fresher files may be in active use)
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    tmppath = Path(tmpdir)
    if tmppath.exists():
        three_days_ago = time.time() - 3 * 86400
        size, count, paths = _scan_old_files(tmppath, three_days_ago)
        results.append({"name": "User Temp Files", "size": size, "count": count, "paths": paths})

    # /private/tmp old files
    private_tmp = Path("/private/tmp")
    if private_tmp.exists():
        three_days_ago = time.time() - 3 * 86400
        size, count, paths = _scan_old_files(private_tmp, three_days_ago)
        results.append({"name": "System Temp (old)", "size": size, "count": count, "paths": paths})

    return results


# Files that act as live markers for running processes -- never clean them
_TEMP_SKIP_SUFFIXES = (".lock", ".pid", ".sock")


def _is_protected_temp_file(f: Path) -> bool:
    name = f.name
    return name.startswith(".") or name.lower().endswith(_TEMP_SKIP_SUFFIXES)


def _scan_old_files(directory: Path, cutoff: float) -> tuple[int, int, list[str]]:
    total_size = 0
    count = 0
    paths = []
    try:
        for f in directory.rglob("*"):
            if f.is_file() and not f.is_symlink() and not _is_protected_temp_file(f):
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
    """Collect individual files inside a directory (never the directory itself)."""
    total_size = 0
    count = 0
    paths = []
    try:
        for f in directory.rglob("*"):
            if f.is_file() and not f.is_symlink():
                try:
                    total_size += f.stat().st_size
                    count += 1
                    paths.append(str(f))
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total_size, count, paths


# ── Browser Scanner ──────────────────────────────────────────────────────

def _check_full_disk_access() -> bool:
    """Probe a TCC-protected path; False means Full Disk Access is missing."""
    safari_dir = Path.home() / "Library/Safari"
    if not safari_dir.exists():
        return True
    try:
        next(safari_dir.iterdir(), None)
        return True
    except PermissionError:
        return False


def _scan_all_browsers() -> list[dict]:
    results = []
    if not _check_full_disk_access():
        console.print("[yellow]Note: Safari data is TCC-protected -- grant your terminal Full Disk Access to include it.[/]")
    for name, base_path in BROWSER_PATHS.items():
        if not base_path.exists():
            continue
        cache_size = 0
        cache_paths = []
        for pattern in BROWSER_CLEAN_PATTERNS.get("cache", []):
            for match in base_path.rglob(pattern):
                if match.is_dir():
                    s = dir_size(match)
                    cache_size += s
                    cache_paths.append(str(match))
        # Main HTTP disk cache lives under ~/Library/Caches/<vendor>
        vendor_cache = BROWSER_CACHE_DIRS.get(name)
        if vendor_cache and vendor_cache.exists():
            s = dir_size(vendor_cache)
            if s > 0:
                cache_size += s
                cache_paths.append(str(vendor_cache))
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


def _running_browsers(names) -> set[str]:
    """Return the subset of browser keys whose process is currently running."""
    wanted = {}
    for key in names:
        for proc_name in BROWSER_PROCESS_NAMES.get(key, []):
            wanted[proc_name.lower()] = key
    running = set()
    for p in psutil.process_iter(["name"]):
        name = (p.info["name"] or "").lower()
        if name in wanted:
            running.add(wanted[name])
    return running


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

    # Determine what to clean. Default is cache + crash reports only:
    # cookies/history/storage hold logins and user data and are opt-in.
    clean_categories = []
    if cache:
        clean_categories.append("cache")
    if cookies:
        clean_categories.append("cookies")
    if not clean_categories:
        clean_categories = list(DEFAULT_BROWSER_CATEGORIES)

    # Refuse to touch live browser databases (cookies etc.) while running
    risky_cats = [c for c in clean_categories if c not in ("cache", "crash")]
    if risky_cats:
        running = _running_browsers(targets.keys())
        for name in running:
            console.print(f"[yellow]{name.title()} is running -- skipping its {'/'.join(risky_cats)} data. Quit it first.[/]")
        targets = {k: v for k, v in targets.items() if k not in running}
        if not targets:
            return

    results = []
    for name, base_path in targets.items():
        if not base_path.exists():
            continue
        for cat in clean_categories:
            patterns = BROWSER_CLEAN_PATTERNS.get(cat, [])
            for pattern in patterns:
                for match in base_path.rglob(pattern):
                    st = safe_stat(match)
                    if st:
                        s = dir_size(match) if match.is_dir() else st.st_size
                        if s > 0:
                            results.append({
                                "name": f"{name.title()} - {cat}/{pattern}",
                                "size": s,
                                "count": 1,
                                "paths": [str(match)],
                            })
        if "cache" in clean_categories:
            vendor_cache = BROWSER_CACHE_DIRS.get(name)
            if vendor_cache and vendor_cache.exists():
                s = dir_size(vendor_cache)
                if s > 0:
                    results.append({
                        "name": f"{name.title()} - cache/{vendor_cache.name}",
                        "size": s,
                        "count": 1,
                        "paths": [str(vendor_cache)],
                    })
            if name == "safari":
                for cache_path in SAFARI_EXTRA.get("cache", []):
                    if cache_path.exists():
                        s = dir_size(cache_path)
                        if s > 0:
                            results.append({
                                "name": f"Safari - cache/{cache_path.name}",
                                "size": s,
                                "count": 1,
                                "paths": [str(cache_path)],
                            })
    if "firefox" in targets or "safari" in targets:
        console.print("[dim]Note: Firefox/Safari use their own layouts -- only their cache dirs are handled; cookies/history are not.[/]")

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
        if info.get("risky"):
            continue  # non-regenerable data: only via explicit --module
        path = info["path"]
        if path.exists():
            st = safe_stat(path)
            s = dir_size(path) if path.is_dir() else (st.st_size if st else 0)
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
        st = safe_stat(path)
        s = dir_size(path) if path.is_dir() else (st.st_size if st else 0)
        console.print(f"[cyan]{info['name']}:[/] {format_size(s)}")
        if scan_only or s == 0:
            return
        if info.get("risky"):
            console.print(f"[yellow bold]WARNING: {info['name']} contains non-regenerable data.[/]")
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
    try:
        subprocess.run(["pbcopy"], input=b"", timeout=5)
        console.print("[green]Clipboard cleared.[/]")
        log_action("clean_clipboard")
    except (OSError, subprocess.TimeoutExpired) as e:
        console.print(f"[red]Could not clear clipboard: {e}[/]")


# Modern macOS (10.13+) stores recents here, not in com.apple.recentitems
_SHAREDFILELIST_DIR = Path.home() / "Library/Application Support/com.apple.sharedfilelist"
_RECENT_SFL_PREFIXES = (
    "com.apple.LSSharedFileList.RecentDocuments",
    "com.apple.LSSharedFileList.RecentApplications",
    "com.apple.LSSharedFileList.RecentHosts",
    "com.apple.LSSharedFileList.RecentServers",
)


def _clean_recent_items(force_yes: bool = False):
    if not confirm_action("Clear macOS recent items lists?", force_yes=force_yes):
        return
    console.print("[cyan]Clearing recent items...[/]")
    cleared = 0
    if _SHAREDFILELIST_DIR.exists():
        for f in _SHAREDFILELIST_DIR.iterdir():
            if f.is_file() and f.name.startswith(_RECENT_SFL_PREFIXES) and f.suffix in (".sfl2", ".sfl3"):
                if _trash_or_rm(f):
                    cleared += 1
                    console.print(f"  [green]Cleared {f.name}[/]")
    # Legacy prefs domains (pre-10.13, harmless if absent)
    commands = [
        (["defaults", "delete", "com.apple.recentitems"], "Recent Items (legacy)"),
        (["defaults", "delete", "com.apple.finder", "FXRecentFolders"], "Finder Recent Folders"),
    ]
    for cmd, name in commands:
        out, err, rc = run_cmd(cmd)
        if rc == 0:
            cleared += 1
            console.print(f"  [green]Cleared {name}[/]")
        else:
            console.print(f"  [dim]{name}: nothing to clear[/]")
    if cleared:
        console.print("[dim]Restart Finder (killall Finder) for menus to refresh.[/]")
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
        <string>{sys.executable}</string>
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
    if plist_path.exists():
        run_cmd(["launchctl", "unload", str(plist_path)])
    plist_path.write_text(plist_content)
    out, err, rc = run_cmd(["launchctl", "load", str(plist_path)])
    if rc != 0:
        console.print(f"[red]launchctl load failed: {err.strip()}[/]")
        return
    console.print(f"[green]Auto-clean scheduled (weekly Sunday 3AM)[/]")
    console.print(f"[dim]Plist: {plist_path}[/]")
    log_action("schedule_clean", str(plist_path))
