"""Privacy cleaner for macmon -- remove activity traces."""

import shutil
import sqlite3
from pathlib import Path

from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .utils import (
    confirm_action,
    console,
    dir_size,
    format_size,
    log_action,
    run_cmd,
    safe_stat,
)


def run_privacy(scan: bool = False, clean: bool = False, full: bool = False, force_yes: bool = False):
    console.print(Panel("[bold]macmon privacy[/] -- Privacy Traces Wiper", border_style="red"))

    traces = _scan_all_traces()

    table = Table(title="Privacy Traces Found", border_style="red")
    table.add_column("Category", style="cyan", width=35)
    table.add_column("Details", width=30)
    table.add_column("Size", justify="right", width=12)

    total_size = 0
    for t in traces:
        if t["size"] > 0 or t["found"]:
            table.add_row(t["name"], t["detail"], format_size(t["size"]))
            total_size += t["size"]

    table.add_row("[bold]TOTAL[/]", "", f"[bold]{format_size(total_size)}[/]")
    console.print(table)

    if scan:
        console.print("\n[dim]Preview only. Use --clean or --full to wipe.[/]")
        return

    if total_size == 0 and not any(t["found"] for t in traces):
        console.print("[green]No privacy traces found![/]")
        return

    if full:
        if confirm_action("Wipe ALL privacy traces?", force_yes=force_yes):
            _wipe_all(traces)
    elif clean:
        _interactive_clean(traces)


def _scan_all_traces() -> list[dict]:
    traces = []

    # Recent items
    traces.append(_check_recent_items())

    # Finder recent folders
    traces.append(_check_finder_recent())

    # QuickLook cache
    traces.append(_check_quicklook())

    # Quarantine DB
    traces.append(_check_quarantine())

    # Shell history
    traces.extend(_check_shell_history())

    # REPL histories
    traces.extend(_check_repl_history())

    # Zsh sessions
    traces.append(_check_zsh_sessions())

    # SSH known_hosts
    traces.append(_check_ssh())

    # Siri
    traces.append(_check_siri())

    return traces


# Modern macOS (10.13+) stores recents as sharedfilelist files
_SFL_DIR = Path.home() / "Library/Application Support/com.apple.sharedfilelist"
_RECENT_SFL_PREFIXES = (
    "com.apple.LSSharedFileList.RecentDocuments",
    "com.apple.LSSharedFileList.RecentApplications",
    "com.apple.LSSharedFileList.RecentHosts",
    "com.apple.LSSharedFileList.RecentServers",
)


def _recent_sfl_files() -> list[Path]:
    if not _SFL_DIR.exists():
        return []
    try:
        return [
            f for f in _SFL_DIR.iterdir()
            if f.is_file() and f.name.startswith(_RECENT_SFL_PREFIXES) and f.suffix in (".sfl2", ".sfl3")
        ]
    except (OSError, PermissionError):
        return []


def _check_recent_items() -> dict:
    files = _recent_sfl_files()
    size = 0
    for f in files:
        st = safe_stat(f)
        if st:
            size += st.st_size
    return {"name": "Recent Items (macOS)", "detail": f"{len(files)} sharedfilelist files", "size": size, "found": bool(files), "action": "clear_recent"}


def _check_finder_recent() -> dict:
    out, _, rc = run_cmd(["defaults", "read", "com.apple.finder", "FXRecentFolders"], timeout=5)
    found = rc == 0 and len(out.strip()) > 10
    return {"name": "Finder Recent Folders", "detail": "Recent folder locations", "size": 0, "found": found, "action": "clear_finder_recent"}


def _check_quicklook() -> dict:
    ql_path = Path.home() / "Library/Application Support/Quick Look"
    ql_cache = Path.home() / "Library/Caches/com.apple.QuickLook.thumbnailcache"
    size = 0
    if ql_path.exists():
        size += dir_size(ql_path)
    if ql_cache.exists():
        size += dir_size(ql_cache)
    return {"name": "QuickLook Cache", "detail": "Thumbnail previews", "size": size, "found": size > 0, "action": "clear_quicklook"}


def _check_quarantine() -> dict:
    db_path = Path.home() / "Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
    size = 0
    found = False
    count = 0
    if db_path.exists():
        st = safe_stat(db_path)
        if st:
            size = st.st_size
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            count = conn.execute("SELECT COUNT(*) FROM LSQuarantineEvent").fetchone()[0]
            conn.close()
            found = count > 0
        except sqlite3.Error:
            found = size > 1024
    return {"name": "Quarantine Events DB", "detail": f"{count} download events", "size": size, "found": found, "action": "clear_quarantine"}


def _check_shell_history() -> list[dict]:
    results = []
    histories = {
        "Zsh History": Path.home() / ".zsh_history",
        "Bash History": Path.home() / ".bash_history",
        "Fish History": Path.home() / ".local/share/fish/fish_history",
    }
    for name, path in histories.items():
        if path.exists():
            st = safe_stat(path)
            size = st.st_size if st else 0
            results.append({
                "name": name, "detail": str(path.name),
                "size": size, "found": size > 0,
                "action": "clear_file", "path": str(path),
            })
    return results


def _check_repl_history() -> list[dict]:
    results = []
    repls = {
        "Python REPL History": Path.home() / ".python_history",
        "Node REPL History": Path.home() / ".node_repl_history",
        "SQLite History": Path.home() / ".sqlite_history",
        "IRB History": Path.home() / ".irb_history",
        "MySQL History": Path.home() / ".mysql_history",
        "Redis CLI History": Path.home() / ".rediscli_history",
    }
    for name, path in repls.items():
        if path.exists():
            st = safe_stat(path)
            size = st.st_size if st else 0
            if size > 0:
                results.append({
                    "name": name, "detail": str(path.name),
                    "size": size, "found": True,
                    "action": "clear_file", "path": str(path),
                })
    return results


def _check_zsh_sessions() -> dict:
    sessions = Path.home() / ".zsh_sessions"
    size = 0
    if sessions.exists():
        size = dir_size(sessions)
    return {"name": "Zsh Sessions", "detail": "Session restore files", "size": size, "found": size > 0, "action": "clear_dir", "path": str(sessions)}


def _check_ssh() -> dict:
    known_hosts = Path.home() / ".ssh/known_hosts"
    size = 0
    count = 0
    if known_hosts.exists():
        st = safe_stat(known_hosts)
        size = st.st_size if st else 0
        try:
            count = len(known_hosts.read_text(errors="surrogateescape").strip().splitlines())
        except (OSError, UnicodeDecodeError):
            pass
    return {"name": "SSH known_hosts", "detail": f"{count} entries", "size": size, "found": count > 0, "action": "clear_ssh"}


def _check_siri() -> dict:
    siri_path = Path.home() / "Library/Assistant"
    size = 0
    if siri_path.exists():
        size = dir_size(siri_path)
    return {"name": "Siri History", "detail": "Interaction data", "size": size, "found": size > 0, "action": "clear_siri"}


# ── Wipe actions ─────────────────────────────────────────────────────────

def _wipe_all(traces: list[dict]):
    cfg = load_config()
    keep_lines = cfg.get("privacy", {}).get("shell_history_keep_lines", 0)
    wiped = 0

    for t in traces:
        if not t["found"] and t["size"] == 0:
            continue
        action = t.get("action", "")
        try:
            if _execute_wipe(action, t, keep_lines):
                wiped += 1
                console.print(f"  [green]Wiped: {t['name']}[/]")
            else:
                console.print(f"  [yellow]Could not wipe: {t['name']} (permission denied or protected)[/]")
        except Exception as e:
            console.print(f"  [red]Failed: {t['name']}: {e}[/]")

    console.print(f"\n[green bold]Wiped {wiped} trace categories.[/]")
    log_action("privacy_full", f"wiped {wiped} categories")


def _interactive_clean(traces: list[dict]):
    cfg = load_config()
    keep_lines = cfg.get("privacy", {}).get("shell_history_keep_lines", 0)
    wiped = 0

    for t in traces:
        if not t["found"] and t["size"] == 0:
            continue
        size_str = f" ({format_size(t['size'])})" if t["size"] > 0 else ""
        if confirm_action(f"  Wipe {t['name']}{size_str}?"):
            try:
                if _execute_wipe(t.get("action", ""), t, keep_lines):
                    wiped += 1
                    console.print(f"  [green]Wiped: {t['name']}[/]")
                else:
                    console.print(f"  [yellow]Could not wipe: {t['name']} (permission denied or protected)[/]")
            except Exception as e:
                console.print(f"  [red]Failed: {t['name']}: {e}[/]")

    console.print(f"\n[green bold]Wiped {wiped} trace categories.[/]")
    log_action("privacy_interactive", f"wiped {wiped} categories")


def _execute_wipe(action: str, trace: dict, keep_lines: int = 0) -> bool:
    """Run a wipe action. Returns True only when the traces are actually gone."""
    if action == "clear_recent":
        ok = True
        for f in _recent_sfl_files():
            try:
                f.unlink()
            except (OSError, PermissionError):
                ok = False
        run_cmd(["defaults", "delete", "com.apple.recentitems"])  # legacy, may not exist
        return ok
    elif action == "clear_finder_recent":
        _, _, rc = run_cmd(["defaults", "delete", "com.apple.finder", "FXRecentFolders"])
        return rc == 0
    elif action == "clear_quicklook":
        run_cmd(["qlmanage", "-r", "cache"], timeout=10)
        ok = True
        for p in [
            Path.home() / "Library/Application Support/Quick Look",
            Path.home() / "Library/Caches/com.apple.QuickLook.thumbnailcache",
        ]:
            if p.exists():
                try:
                    shutil.rmtree(p)
                except (OSError, PermissionError):
                    ok = ok and not p.exists()
        return ok
    elif action == "clear_quarantine":
        db_path = Path.home() / "Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute("DELETE FROM LSQuarantineEvent")
                conn.commit()
                conn.execute("VACUUM")  # shrink the file so rescans see it empty
                conn.close()
                return True
            except sqlite3.Error:
                return False
        return True
    elif action == "clear_file":
        path = Path(trace.get("path", ""))
        if path.exists():
            try:
                if keep_lines > 0:
                    lines = path.read_text(errors="surrogateescape").splitlines()
                    path.write_text("\n".join(lines[-keep_lines:]) + "\n", errors="surrogateescape")
                else:
                    path.write_text("")
                return True
            except (OSError, UnicodeDecodeError):
                return False
        return True
    elif action == "clear_dir":
        path = Path(trace.get("path", ""))
        if path.exists():
            try:
                shutil.rmtree(path)
            except (OSError, PermissionError):
                pass
            return not path.exists()
        return True
    elif action == "clear_ssh":
        known_hosts = Path.home() / ".ssh/known_hosts"
        if known_hosts.exists():
            try:
                known_hosts.write_text("")
                return True
            except (OSError, PermissionError):
                return False
        return True
    elif action == "clear_siri":
        siri_path = Path.home() / "Library/Assistant"
        if siri_path.exists():
            try:
                shutil.rmtree(siri_path)
            except (OSError, PermissionError):
                pass
            return not siri_path.exists()
        return True
    return False
