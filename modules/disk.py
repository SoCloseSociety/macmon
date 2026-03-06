"""Disk analyzer and large file finder for macmon."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .utils import console, dir_size, format_size, get_db, log_action


FILE_CATEGORIES = {
    "disk_image": {
        "emoji": "\U0001f4c0",
        "extensions": {".dmg", ".iso", ".img", ".sparseimage", ".sparsebundle"},
    },
    "video": {
        "emoji": "\U0001f3ac",
        "extensions": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"},
    },
    "archive": {
        "emoji": "\U0001f4e6",
        "extensions": {".zip", ".tar.gz", ".rar", ".7z", ".tar.xz", ".tar.bz2", ".gz", ".bz2", ".xz", ".tgz"},
    },
    "vm_image": {
        "emoji": "\U0001f4be",
        "extensions": {".vmdk", ".vdi", ".qcow2", ".vhd", ".vhdx"},
    },
    "document": {
        "emoji": "\U0001f4c4",
        "extensions": {".pdf", ".psd", ".ai", ".indd", ".sketch"},
    },
}

MOBILE_BACKUP = Path.home() / "Library/Application Support/MobileSync"


def _categorize_file(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    # Check compound extensions like .tar.gz
    name_lower = path.name.lower()
    if name_lower.endswith(".tar.gz") or name_lower.endswith(".tar.xz") or name_lower.endswith(".tar.bz2"):
        return "\U0001f4e6", "archive"

    for cat_name, info in FILE_CATEGORIES.items():
        if suffix in info["extensions"]:
            return info["emoji"], cat_name

    # Check if in Downloads and old
    if "Downloads" in str(path):
        return "\U0001f5c2\ufe0f", "download"

    return "\U0001f4c4", "other"


def _parse_size(size_str: str) -> int:
    size_str = size_str.strip().upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for unit, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if size_str.endswith(unit):
            try:
                return int(float(size_str[: -len(unit)].strip()) * mult)
            except ValueError:
                pass
    try:
        return int(size_str)
    except ValueError:
        return 50 * 1024 * 1024  # Default 50MB


def find_big_files(
    path: str = "~",
    min_size: str = "50MB",
    file_type: str = None,
    older: int = None,
    json_out: bool = False,
):
    base = Path(path).expanduser()
    min_bytes = _parse_size(min_size)

    console.print(Panel(f"[bold]macmon bigfiles[/] -- {base} (min: {format_size(min_bytes)})", border_style="cyan"))

    big_files = []
    now = time.time()

    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__", ".Trash", "Library"}

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scanning for large files...", total=None)

        for root, dirs, files in os.walk(base):
            # Skip system directories
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".Spotlight")]
            for fname in files:
                fpath = Path(root) / fname
                if fpath.is_symlink():
                    continue
                try:
                    st = fpath.stat()
                    if st.st_size < min_bytes:
                        continue

                    # Filter by type
                    if file_type and not fpath.suffix.lower().lstrip(".") == file_type.lower().lstrip("."):
                        continue

                    # Filter by age
                    if older:
                        days_since_access = (now - st.st_atime) / 86400
                        if days_since_access < older:
                            continue

                    emoji, category = _categorize_file(fpath)
                    big_files.append({
                        "path": str(fpath),
                        "size": st.st_size,
                        "atime": st.st_atime,
                        "mtime": st.st_mtime,
                        "emoji": emoji,
                        "category": category,
                    })
                except (OSError, PermissionError):
                    continue

            if len(big_files) >= 200:
                break

        progress.remove_task(task)

    # iOS/macOS backups
    if MOBILE_BACKUP.exists() and str(base) in str(Path.home()):
        try:
            s = dir_size(MOBILE_BACKUP)
            if s >= min_bytes:
                big_files.append({
                    "path": str(MOBILE_BACKUP),
                    "size": s,
                    "atime": MOBILE_BACKUP.stat().st_atime,
                    "mtime": MOBILE_BACKUP.stat().st_mtime,
                    "emoji": "\U0001f4f1",
                    "category": "iOS backup",
                })
        except (OSError, PermissionError):
            pass

    big_files.sort(key=lambda x: x["size"], reverse=True)
    big_files = big_files[:50]

    if json_out:
        console.print_json(json.dumps(big_files, default=str))
        return

    if not big_files:
        console.print(f"[green]No files larger than {format_size(min_bytes)} found![/]")
        return

    table = Table(title=f"Large Files (top {len(big_files)})", border_style="cyan")
    table.add_column("", width=2)
    table.add_column("Path", width=50)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Category", width=12)
    table.add_column("Last Access", width=14)
    table.add_column("Modified", width=14)

    total = 0
    for f in big_files:
        atime = datetime.fromtimestamp(f["atime"]).strftime("%Y-%m-%d")
        mtime = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d")
        # Truncate path for display
        display_path = f["path"]
        home_str = str(Path.home())
        if display_path.startswith(home_str):
            display_path = "~" + display_path[len(home_str):]
        if len(display_path) > 50:
            display_path = "..." + display_path[-47:]

        table.add_row(f["emoji"], display_path, format_size(f["size"]), f["category"], atime, mtime)
        total += f["size"]

    table.add_row("", "[bold]TOTAL[/]", f"[bold]{format_size(total)}[/]", "", "", "")
    console.print(table)


def analyze_disk(path: str = "~", json_out: bool = False):
    base = Path(path).expanduser()
    console.print(Panel(f"[bold]macmon disk[/] -- {base}", border_style="cyan"))

    entries = []
    try:
        for d in base.iterdir():
            if d.is_symlink():
                continue
            try:
                if d.is_dir():
                    s = dir_size(d)
                    # Count files
                    count = sum(1 for _ in d.rglob("*") if _.is_file())
                    mtime = d.stat().st_mtime
                    entries.append({
                        "path": str(d),
                        "name": d.name,
                        "size": s,
                        "count": count,
                        "mtime": mtime,
                    })
                elif d.is_file():
                    st = d.stat()
                    entries.append({
                        "path": str(d),
                        "name": d.name,
                        "size": st.st_size,
                        "count": 1,
                        "mtime": st.st_mtime,
                    })
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        console.print(f"[red]Cannot read {base}[/]")
        return

    entries.sort(key=lambda x: x["size"], reverse=True)
    total = sum(e["size"] for e in entries)

    if json_out:
        console.print_json(json.dumps(entries[:15], default=str))
        return

    table = Table(title=f"Disk Usage: {base}", border_style="cyan")
    table.add_column("Directory", width=30)
    table.add_column("Size", justify="right", width=12)
    table.add_column("%", justify="right", width=6)
    table.add_column("Files", justify="right", width=8)
    table.add_column("Modified", width=14)

    for e in entries[:15]:
        pct = (e["size"] / total * 100) if total > 0 else 0
        mtime = datetime.fromtimestamp(e["mtime"]).strftime("%Y-%m-%d")
        size_color = "red" if e["size"] > 1024**3 else "yellow" if e["size"] > 500 * 1024**2 else "white"
        table.add_row(
            e["name"][:30],
            f"[{size_color}]{format_size(e['size'])}[/]",
            f"{pct:.1f}%",
            str(e["count"]),
            mtime,
        )

    table.add_row("[bold]TOTAL[/]", f"[bold]{format_size(total)}[/]", "100%", "", "")
    console.print(table)

    # Track in DB for growth detection
    try:
        db = get_db()
        db.execute(
            "INSERT INTO scan_history (scan_type, total_size, details) VALUES (?, ?, ?)",
            ("disk", total, str(base)),
        )
        db.commit()
        db.close()
    except Exception:
        pass
