"""Startup/login items manager for macmon."""

import plistlib
import re
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.table import Table

from .utils import confirm_action, console, format_size, log_action, run_cmd


AGENT_DIRS = {
    "User LaunchAgents": Path.home() / "Library/LaunchAgents",
    "System LaunchAgents": Path("/Library/LaunchAgents"),
    "System LaunchDaemons": Path("/Library/LaunchDaemons"),
    "macOS LaunchDaemons": Path("/System/Library/LaunchDaemons"),
}


def run_startup(
    list_items: bool = False,
    disable: str = None,
    enable: str = None,
    delete: str = None,
    broken: bool = False,
    audit: bool = False,
    force_yes: bool = False,
):
    if disable:
        _disable_item(disable, force_yes)
        return
    if enable:
        _enable_item(enable)
        return
    if delete:
        _delete_item(delete, force_yes)
        return
    if broken:
        _show_broken()
        return
    if audit:
        _audit_items()
        return

    # Default: list all
    _list_all()


def _parse_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        # Try reading as XML text
        try:
            content = path.read_text()
            label = ""
            program = ""
            m = re.search(r"<key>Label</key>\s*<string>([^<]+)</string>", content)
            if m:
                label = m.group(1)
            m = re.search(r"<string>(/[^<]+)</string>", content)
            if m:
                program = m.group(1)
            return {"Label": label, "Program": program}
        except Exception:
            return {}


def _get_loaded_labels() -> set:
    """Labels loaded in the current user's gui domain (single launchctl call)."""
    labels = set()
    out, _, rc = run_cmd(["launchctl", "list"], timeout=10)
    if rc == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                label = parts[2].strip()
                if label and label != "Label":
                    labels.add(label)
    return labels


def _get_system_labels():
    """Labels loaded in the system domain, or None if unreadable."""
    out, _, rc = run_cmd(["launchctl", "print", "system"], timeout=10)
    if rc != 0 or not out:
        return None
    labels = set()
    in_services = False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("services = {"):
            in_services = True
            continue
        if in_services:
            if stripped == "}":
                break
            parts = stripped.split()
            if parts:
                labels.add(parts[-1])
    return labels


def _get_all_items() -> list[dict]:
    items = []

    loaded_labels = _get_loaded_labels()
    system_labels = _get_system_labels()

    # Build process name -> RSS map once
    proc_ram = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            pname = (p.info["name"] or "").lower()
            if pname and pname not in proc_ram:
                proc_ram[pname] = p.info["memory_info"].rss if p.info["memory_info"] else 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    for category, directory in AGENT_DIRS.items():
        if not directory.exists():
            continue
        read_only = category == "macOS LaunchDaemons"
        is_daemon = "LaunchDaemons" in category

        for plist_path in directory.glob("*.plist"):
            try:
                data = _parse_plist(plist_path)
                label = data.get("Label", plist_path.stem)

                # Determine binary path
                program = data.get("Program", "")
                if not program and "ProgramArguments" in data:
                    args = data["ProgramArguments"]
                    if args:
                        program = args[0] if isinstance(args, list) else str(args)

                # Check if binary exists (only meaningful for absolute paths)
                binary_exists = True
                if program and program.startswith("/"):
                    try:
                        binary_exists = Path(program).exists()
                    except (OSError, PermissionError):
                        binary_exists = True

                # Check if loaded/running (None = unknown)
                if is_daemon:
                    is_loaded = (label in system_labels) if system_labels is not None else None
                else:
                    is_loaded = label in loaded_labels

                # Check RAM if running
                ram = 0
                if is_loaded and program:
                    ram = proc_ram.get(program.split("/")[-1].lower(), 0)

                items.append({
                    "label": label,
                    "path": str(plist_path),
                    "category": category,
                    "program": program,
                    "binary_exists": binary_exists,
                    "is_loaded": is_loaded,
                    "ram": ram,
                    "read_only": read_only,
                    "disabled": data.get("Disabled", False),
                })
            except (OSError, PermissionError):
                continue

    # Cron jobs
    out, _, rc = run_cmd(["crontab", "-l"], timeout=5)
    if rc == 0 and out.strip():
        for line in out.strip().splitlines():
            if line.strip() and not line.startswith("#"):
                items.append({
                    "label": f"cron: {line[:50]}",
                    "path": "crontab",
                    "category": "Cron Jobs",
                    "program": line,
                    "binary_exists": True,
                    "is_loaded": True,
                    "ram": 0,
                    "read_only": False,
                    "disabled": False,
                })

    return items


def _list_all():
    items = _get_all_items()

    console.print(Panel("[bold]macmon startup[/] -- Startup Items Manager", border_style="cyan"))

    for category in AGENT_DIRS.keys():
        cat_items = [i for i in items if i["category"] == category]
        if not cat_items:
            continue

        table = Table(title=category, border_style="cyan" if "User" in category else "dim")
        table.add_column("Label", width=40)
        table.add_column("Status", width=12)
        table.add_column("Binary", width=8)
        table.add_column("RAM", width=10, justify="right")

        for item in cat_items:
            if item["is_loaded"] is None:
                status = "[dim]-[/]"
            elif item["is_loaded"]:
                status = "[green]Running[/]"
            elif item["disabled"]:
                status = "[yellow]Disabled[/]"
            else:
                status = "[dim]Stopped[/]"

            binary = "[green]OK[/]" if item["binary_exists"] else "[red]MISSING[/]"
            ram = format_size(item["ram"]) if item["ram"] > 0 else "[dim]-[/]"

            table.add_row(item["label"], status, binary, ram)

        console.print(table)

    # Cron
    cron_items = [i for i in items if i["category"] == "Cron Jobs"]
    if cron_items:
        console.print("\n[bold]Cron Jobs:[/]")
        for item in cron_items:
            console.print(f"  {item['program']}")

    # Modern login items (macOS 13+ BTM)
    _show_btm_login_items()

    console.print(f"\n[dim]Total: {len(items)} startup items[/]")


def _show_btm_login_items():
    """Best-effort read-only view of modern (macOS 13+) login items."""
    out, _, rc = run_cmd(["sfltool", "dumpbtm"], timeout=5)
    names = []
    if rc == 0 and out.strip():
        for line in out.splitlines():
            m = re.match(r"\s*Name:\s*(.+)", line)
            if m:
                name = m.group(1).strip()
                if name and name != "(null)" and name not in names:
                    names.append(name)
    if names:
        table = Table(title="Login Items (BTM)", border_style="dim")
        table.add_column("Name", width=40)
        table.add_column("Managed By", width=30)
        for n in names:
            table.add_row(n, "[dim]System Settings (read-only)[/]")
        console.print(table)
    else:
        console.print("\n[dim]Note: macOS 13+ Login Items are managed in System Settings > General > Login Items and are not listed here.[/]")


def _show_broken():
    items = _get_all_items()
    broken = [i for i in items if not i["binary_exists"] and not i["read_only"]]

    if not broken:
        console.print("[green]No broken startup items found![/]")
        return

    table = Table(title=f"Broken Startup Items ({len(broken)})", border_style="red")
    table.add_column("Label", width=40)
    table.add_column("Missing Binary", width=40)
    table.add_column("Category", width=20)

    for item in broken:
        table.add_row(item["label"], item["program"], item["category"])

    console.print(table)
    console.print("[dim]Use `macmon startup --delete <label>` to remove broken items.[/]")


def _audit_items():
    items = _get_all_items()

    suspicious_patterns = [
        "updater", "helper", "agent", "daemon", "sync",
        "monitor", "watcher", "service",
    ]

    # Known safe items
    safe_vendors = ["apple", "com.apple", "org.mozilla", "com.google", "com.microsoft"]

    flagged = []
    for item in items:
        if item["read_only"]:
            continue
        label_lower = item["label"].lower()
        is_safe = any(v in label_lower for v in safe_vendors)
        if not is_safe:
            is_suspicious = any(p in label_lower for p in suspicious_patterns)
            if is_suspicious or not item["binary_exists"]:
                flagged.append(item)

    if not flagged:
        console.print("[green]No suspicious startup items found.[/]")
        return

    table = Table(title=f"Suspicious Items ({len(flagged)})", border_style="yellow")
    table.add_column("Label", width=40)
    table.add_column("Status", width=12)
    table.add_column("Binary", width=8)
    table.add_column("Reason", width=20)

    for item in flagged:
        reason = "Missing binary" if not item["binary_exists"] else "Unknown vendor"
        if item["is_loaded"] is None:
            status = "[dim]unknown[/]"
        else:
            status = "[green]Running[/]" if item["is_loaded"] else "[dim]Stopped[/]"
        binary = "[green]OK[/]" if item["binary_exists"] else "[red]MISSING[/]"
        table.add_row(item["label"], status, binary, reason)

    console.print(table)


def _guard_modify(plist: str, force_yes: bool = False) -> bool:
    """Refuse macOS-protected items; extra confirmation for system daemons."""
    if plist.startswith("/System/"):
        console.print(f"[red]Refusing to modify macOS system item: {plist}[/]")
        return False
    if plist.startswith("/Library/LaunchDaemons/"):
        console.print(f"[yellow]System-level daemon: {plist}[/]")
        if force_yes:
            return True
        return confirm_action(f"This is a system-level daemon ({plist}). Continue?")
    return True


def _plist_label(plist: str) -> str:
    data = _parse_plist(Path(plist))
    return data.get("Label") or Path(plist).stem


def _bootout_cmd(plist: str, label: str) -> list[str]:
    if plist.startswith("/Library/LaunchDaemons/"):
        return ["sudo", "launchctl", "bootout", f"system/{label}"]
    return ["launchctl", "bootout", f"gui/{_get_uid()}/{label}"]


def _disable_item(label: str, force_yes: bool = False):
    plist = _find_plist(label)
    if not plist:
        console.print(f"[yellow]Could not find plist for {label}[/]")
        return
    if not _guard_modify(plist, force_yes):
        return
    real_label = _plist_label(plist)
    out, err, rc = run_cmd(_bootout_cmd(plist, real_label), timeout=10)
    if rc == 0:
        console.print(f"[green]Disabled {label}[/]")
        log_action("startup_disable", label)
    else:
        console.print(f"[yellow]Could not disable {label} (rc={rc}): {err.strip()}[/]")


def _enable_item(label: str):
    plist = _find_plist(label)
    if not plist:
        console.print(f"[yellow]Could not find plist for {label}[/]")
        return
    if plist.startswith("/Library/LaunchDaemons/"):
        cmd = ["sudo", "launchctl", "bootstrap", "system", plist]
    else:
        cmd = ["launchctl", "bootstrap", f"gui/{_get_uid()}", plist]
    out, err, rc = run_cmd(cmd, timeout=10)
    if rc == 0:
        console.print(f"[green]Enabled {label}[/]")
        log_action("startup_enable", label)
    else:
        console.print(f"[yellow]Could not enable {label} (rc={rc}): {err.strip()}[/]")


def _delete_item(label: str, force_yes: bool = False):
    plist = _find_plist(label)
    if not plist:
        console.print(f"[yellow]Could not find plist for {label}[/]")
        return

    plist_path = Path(plist)
    if not plist_path.exists():
        console.print(f"[yellow]Plist not found: {plist}[/]")
        return

    if not _guard_modify(plist, force_yes):
        return

    if confirm_action(f"Delete startup item {label} ({plist})?", force_yes=force_yes):
        # Unload first, warn if it failed
        real_label = _plist_label(plist)
        out, err, rc = run_cmd(_bootout_cmd(plist, real_label), timeout=10)
        if rc != 0:
            console.print(f"[yellow]Warning: could not unload {real_label} (rc={rc}): {err.strip()}[/]")
        try:
            plist_path.unlink()
        except (OSError, PermissionError) as e:
            console.print(f"[red]Could not delete {plist}: {e}[/]")
            return
        console.print(f"[green]Deleted {label}[/]")
        log_action("startup_delete", f"{label} ({plist})")


def _find_plist(label: str) -> str:
    for _, directory in AGENT_DIRS.items():
        if not directory.exists():
            continue
        for plist in directory.glob("*.plist"):
            # Exact filename match only (with or without .plist)
            if label == plist.stem or label == plist.name:
                return str(plist)
            try:
                data = _parse_plist(plist)
                if data.get("Label") == label:
                    return str(plist)
            except Exception:
                pass
    return ""


def _get_uid() -> int:
    import os
    return os.getuid()
