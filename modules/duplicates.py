"""Duplicate file finder for macmon."""

import hashlib
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .utils import confirm_action, console, format_size, log_action

try:
    import xxhash
    def fast_hash(data: bytes) -> str:
        return xxhash.xxh64(data).hexdigest()
except ImportError:
    def fast_hash(data: bytes) -> str:
        return hashlib.md5(data).hexdigest()

try:
    from send2trash import send2trash
except ImportError:
    send2trash = None


def _trash_or_rm(path: Path, permanent: bool = False):
    if permanent:
        path.unlink(missing_ok=True)
    elif send2trash:
        try:
            send2trash(str(path))
        except Exception:
            path.unlink(missing_ok=True)
    else:
        path.unlink(missing_ok=True)


SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".Trash", "Library", ".Spotlight-V100", ".fseventsd",
}


def run_dupes(
    paths: list[str],
    scan: bool = False,
    auto_keep_newest: bool = False,
    auto_keep_oldest: bool = False,
    keep_in: str = None,
    empty_dirs: bool = False,
    broken_symlinks: bool = False,
    permanent: bool = False,
    force_yes: bool = False,
):
    if empty_dirs:
        _find_empty_dirs(paths, permanent, force_yes)
        return

    if broken_symlinks:
        _find_broken_symlinks(paths, permanent, force_yes)
        return

    console.print(Panel("[bold]macmon dupes[/] -- Duplicate File Finder", border_style="yellow"))

    # Phase 1: Group by size
    console.print("[cyan]Phase 1: Grouping files by size...[/]")
    size_groups = defaultdict(list)

    for base_path in paths:
        base = Path(base_path).expanduser()
        if not base.exists():
            console.print(f"[yellow]Path not found: {base}[/]")
            continue

        for f in _walk_files(base):
            try:
                st = f.stat()
                if st.st_size > 0:
                    size_groups[st.st_size].append(f)
            except (OSError, PermissionError):
                continue

    # Filter to groups with 2+ files
    candidates = {s: files for s, files in size_groups.items() if len(files) >= 2}
    total_candidates = sum(len(f) for f in candidates.values())
    console.print(f"[dim]Found {total_candidates} files in {len(candidates)} size groups[/]")

    if not candidates:
        console.print("[green]No potential duplicates found![/]")
        return

    # Phase 2: Pre-filter by xxhash of first 64KB
    console.print("[cyan]Phase 2: Quick hash (first 64KB)...[/]")
    hash_groups = defaultdict(list)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), BarColumn(), console=console) as progress:
        task = progress.add_task("Hashing...", total=total_candidates)
        for size, files in candidates.items():
            for f in files:
                try:
                    with open(f, "rb") as fh:
                        chunk = fh.read(65536)
                    h = fast_hash(chunk)
                    hash_groups[(size, h)].append(f)
                except (OSError, PermissionError):
                    pass
                progress.advance(task)

    # Filter
    pre_dupes = {k: v for k, v in hash_groups.items() if len(v) >= 2}

    if not pre_dupes:
        console.print("[green]No duplicates found after quick hash![/]")
        return

    # Phase 3: Full SHA-256 verification
    console.print("[cyan]Phase 3: Full hash verification...[/]")
    final_groups = defaultdict(list)

    for (size, _), files in pre_dupes.items():
        for f in files:
            try:
                h = _full_hash(f)
                # Exclude hardlinks (same inode)
                inode = f.stat().st_ino
                final_groups[h].append({"path": f, "size": size, "inode": inode, "mtime": f.stat().st_mtime})
            except (OSError, PermissionError):
                continue

    # Remove hardlinks and singles
    dupe_groups = {}
    for h, files in final_groups.items():
        unique_inodes = set()
        unique_files = []
        for f in files:
            if f["inode"] not in unique_inodes:
                unique_inodes.add(f["inode"])
                unique_files.append(f)
        if len(unique_files) >= 2:
            dupe_groups[h] = unique_files

    if not dupe_groups:
        console.print("[green]No exact duplicates found![/]")
        return

    # Display results
    total_reclaimable = 0
    group_num = 0

    table = Table(title="Duplicate Files", border_style="yellow")
    table.add_column("Group", width=6)
    table.add_column("Path", width=55)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Modified", width=20)
    table.add_column("Keep", width=6)

    for h, files in sorted(dupe_groups.items(), key=lambda x: x[1][0]["size"], reverse=True):
        group_num += 1
        files.sort(key=lambda x: x["mtime"])  # Oldest first
        oldest_idx = 0
        newest_idx = len(files) - 1

        for i, f in enumerate(files):
            from datetime import datetime
            mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M")

            is_keep = False
            if auto_keep_oldest and i == oldest_idx:
                is_keep = True
            elif auto_keep_newest and i == newest_idx:
                is_keep = True
            elif keep_in and str(f["path"]).startswith(keep_in):
                is_keep = True

            keep_marker = "[green]KEEP[/]" if is_keep else "[dim]-[/]"
            table.add_row(
                str(group_num) if i == 0 else "",
                str(f["path"]),
                format_size(f["size"]),
                mtime_str,
                keep_marker,
            )
            if not is_keep:
                total_reclaimable += f["size"]

        table.add_row("", "", "", "", "")

    console.print(table)
    console.print(f"\n[bold]{group_num} duplicate groups | Reclaimable: {format_size(total_reclaimable)}[/]")

    if scan:
        return

    # Auto modes
    if auto_keep_newest or auto_keep_oldest or keep_in:
        if confirm_action(f"Delete duplicates and free {format_size(total_reclaimable)}?", force_yes=force_yes):
            deleted = 0
            for h, files in dupe_groups.items():
                files.sort(key=lambda x: x["mtime"])
                for i, f in enumerate(files):
                    should_keep = False
                    if auto_keep_oldest and i == 0:
                        should_keep = True
                    elif auto_keep_newest and i == len(files) - 1:
                        should_keep = True
                    elif keep_in and str(f["path"]).startswith(keep_in):
                        should_keep = True

                    if not should_keep:
                        _trash_or_rm(f["path"], permanent)
                        deleted += 1

            console.print(f"[green bold]Deleted {deleted} duplicate files, freed ~{format_size(total_reclaimable)}[/]")
            log_action("dupes", f"deleted {deleted}, freed {format_size(total_reclaimable)}")


def _walk_files(base: Path):
    try:
        for entry in base.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name in SKIP_DIRS:
                    continue
                yield from _walk_files(entry)
            elif entry.is_file():
                yield entry
    except (OSError, PermissionError):
        pass


def _full_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
    except (OSError, PermissionError):
        return ""
    return h.hexdigest()


def _find_empty_dirs(paths: list[str], permanent: bool, force_yes: bool):
    console.print("[cyan]Scanning for empty directories...[/]")
    empty = []

    for base_path in paths:
        base = Path(base_path).expanduser()
        if not base.exists():
            continue
        for d in base.rglob("*"):
            if d.is_dir() and d.name not in SKIP_DIRS:
                try:
                    if not any(d.iterdir()):
                        empty.append(d)
                except (OSError, PermissionError):
                    continue

    if not empty:
        console.print("[green]No empty directories found![/]")
        return

    console.print(f"[yellow]Found {len(empty)} empty directories[/]")
    for d in empty[:30]:
        console.print(f"  [dim]{d}[/]")
    if len(empty) > 30:
        console.print(f"  [dim]... and {len(empty) - 30} more[/]")

    if confirm_action(f"Delete {len(empty)} empty directories?", force_yes=force_yes):
        deleted = 0
        for d in empty:
            try:
                d.rmdir()
                deleted += 1
            except OSError:
                pass
        console.print(f"[green]Deleted {deleted} empty directories[/]")
        log_action("empty_dirs", f"deleted {deleted}")


def _find_broken_symlinks(paths: list[str], permanent: bool, force_yes: bool):
    console.print("[cyan]Scanning for broken symlinks...[/]")
    broken = []

    for base_path in paths:
        base = Path(base_path).expanduser()
        if not base.exists():
            continue
        for entry in base.rglob("*"):
            if entry.is_symlink() and not entry.resolve().exists():
                broken.append(entry)

    if not broken:
        console.print("[green]No broken symlinks found![/]")
        return

    console.print(f"[yellow]Found {len(broken)} broken symlinks[/]")
    for s in broken[:30]:
        target = os.readlink(s)
        console.print(f"  [dim]{s} -> {target}[/]")

    if confirm_action(f"Delete {len(broken)} broken symlinks?", force_yes=force_yes):
        deleted = 0
        for s in broken:
            try:
                s.unlink()
                deleted += 1
            except OSError:
                pass
        console.print(f"[green]Deleted {deleted} broken symlinks[/]")
        log_action("broken_symlinks", f"deleted {deleted}")
