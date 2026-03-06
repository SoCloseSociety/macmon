"""Privacy cleaner for macmon — remove activity traces."""

import os
import shutil
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

    # Spotlight search history
    traces.append(_check_spotlight())

    # SSH known_hosts
    traces.append(_check_ssh())

    # Siri
    traces.append(_check_siri())

    return traces


def _check_recent_items() -> dict:
    out, _, rc = run_cmd(["defaults", "read", "com.apple.recentitems"], timeout=5)
    found = rc == 0 and len(out.strip()) > 10
    return {"name": "Recent Items (macOS)", "detail": "Documents, apps, servers", "size": 0, "found": found, "action": "clear_recent"}


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
    if db_path.exists():
        st = safe_stat(db_path)
        if st:
            size = st.st_size
            found = size > 1024  # Meaningful entries exist
    return {"name": "Quarantine Events DB", "detail": "Download tracking", "size": size, "found": found, "action": "clear_quarantine"}


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


def _check_spotlight() -> dict:
    out, _, rc = run_cmd(["defaults", "read", "com.apple.spotlight"], timeout=5)
    found = rc == 0 and "orderedItems" in out
    return {"name": "Spotlight History", "detail": "Search history", "size": 0, "found": found, "action": "clear_spotlight"}


def _check_ssh() -> dict:
    known_hosts = Path.home() / ".ssh/known_hosts"
    size = 0
    count = 0
    if known_hosts.exists():
        st = safe_stat(known_hosts)
        size = st.st_size if st else 0
        try:
            count = len(known_hosts.read_text().strip().splitlines())
        except OSError:
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
            _execute_wipe(action, t, keep_lines)
            wiped += 1
            console.print(f"  [green]Wiped: {t['name']}[/]")
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
                _execute_wipe(t.get("action", ""), t, keep_lines)
                wiped += 1
                console.print(f"  [green]Wiped: {t['name']}[/]")
            except Exception as e:
                console.print(f"  [red]Failed: {t['name']}: {e}[/]")

    console.print(f"\n[green bold]Wiped {wiped} trace categories.[/]")
    log_action("privacy_interactive", f"wiped {wiped} categories")


def _execute_wipe(action: str, trace: dict, keep_lines: int = 0):
    if action == "clear_recent":
        run_cmd(["defaults", "delete", "com.apple.recentitems"])
    elif action == "clear_finder_recent":
        run_cmd(["defaults", "delete", "com.apple.finder", "FXRecentFolders"])
    elif action == "clear_quicklook":
        run_cmd(["qlmanage", "-r", "cache"], timeout=10)
        ql_path = Path.home() / "Library/Application Support/Quick Look"
        if ql_path.exists():
            shutil.rmtree(ql_path, ignore_errors=True)
        ql_cache = Path.home() / "Library/Caches/com.apple.QuickLook.thumbnailcache"
        if ql_cache.exists():
            shutil.rmtree(ql_cache, ignore_errors=True)
    elif action == "clear_quarantine":
        db_path = Path.home() / "Library/Preferences/com.apple.LaunchServices.QuarantineEventsV2"
        if db_path.exists():
            import sqlite3
            try:
                conn = sqlite3.connect(str(db_path))
                conn.execute("DELETE FROM LSQuarantineEvent")
                conn.commit()
                conn.close()
            except Exception:
                pass
    elif action == "clear_file":
        path = Path(trace.get("path", ""))
        if path.exists():
            if keep_lines > 0:
                try:
                    lines = path.read_text().splitlines()
                    path.write_text("\n".join(lines[-keep_lines:]) + "\n")
                except OSError:
                    pass
            else:
                path.write_text("")
    elif action == "clear_dir":
        path = Path(trace.get("path", ""))
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
    elif action == "clear_spotlight":
        run_cmd(["defaults", "delete", "com.apple.spotlight"])
    elif action == "clear_ssh":
        known_hosts = Path.home() / ".ssh/known_hosts"
        if known_hosts.exists():
            known_hosts.write_text("")
    elif action == "clear_siri":
        siri_path = Path.home() / "Library/Assistant"
        if siri_path.exists():
            shutil.rmtree(siri_path, ignore_errors=True)
