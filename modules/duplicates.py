"""Duplicate file finder for macmon."""

import errno
import hashlib
import json
import os
import shutil
import stat
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


def _trash_or_rm(path: Path, permanent: bool = False) -> bool:
    if permanent:
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False
    if send2trash:
        try:
            send2trash(str(path))
            return True
        except Exception:
            pass
    console.print(f"[yellow]Skipped (Trash unavailable): {path}[/]")
    return False


SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".Trash", "Library", ".Spotlight-V100", ".fseventsd", "CloudStorage",
}


def _is_under(path: Path, root: str) -> bool:
    try:
        p = str(path.resolve())
    except OSError:
        p = str(path)
    return p == root or p.startswith(root + os.sep)


def _keep_indices(files: list[dict], auto_keep_newest: bool, auto_keep_oldest: bool, keep_in: str) -> set[int]:
    """Indices to keep in a group sorted oldest-first. Always keeps at least one copy."""
    keeps = set()
    for i, f in enumerate(files):
        if auto_keep_oldest and i == 0:
            keeps.add(i)
        elif auto_keep_newest and i == len(files) - 1:
            keeps.add(i)
        elif keep_in and _is_under(f["path"], keep_in):
            keeps.add(i)
    if not keeps:
        keeps.add(len(files) - 1)  # Force-keep the newest copy
    return keeps


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

    if keep_in:
        keep_in = str(Path(keep_in).expanduser().resolve())

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
                # Skip iCloud dataless files -- hashing them would trigger downloads
                if hasattr(stat, "UF_DATALESS") and getattr(st, "st_flags", 0) & stat.UF_DATALESS:
                    continue
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
                if not h:
                    continue  # Unreadable file -- never group it as a duplicate
                st = f.stat()
                final_groups[(size, h)].append({"path": f, "size": size, "dev": st.st_dev, "inode": st.st_ino, "mtime": st.st_mtime})
            except (OSError, PermissionError):
                continue

    # Remove hardlinks and singles
    dupe_groups = {}
    for key, files in final_groups.items():
        unique_links = set()
        unique_files = []
        for f in files:
            link_key = (f["dev"], f["inode"])
            if link_key not in unique_links:
                unique_links.add(link_key)
                unique_files.append(f)
        if len(unique_files) >= 2:
            dupe_groups[key] = unique_files

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

    for key, files in sorted(dupe_groups.items(), key=lambda x: x[1][0]["size"], reverse=True):
        group_num += 1
        files.sort(key=lambda x: x["mtime"])  # Oldest first
        keeps = _keep_indices(files, auto_keep_newest, auto_keep_oldest, keep_in)

        for i, f in enumerate(files):
            from datetime import datetime
            mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M")

            is_keep = i in keeps
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
            freed = 0
            for key, files in dupe_groups.items():
                files.sort(key=lambda x: x["mtime"])
                keeps = _keep_indices(files, auto_keep_newest, auto_keep_oldest, keep_in)
                for i, f in enumerate(files):
                    if i in keeps:
                        continue
                    if _trash_or_rm(f["path"], permanent):
                        deleted += 1
                        freed += f["size"]

            console.print(f"[green bold]Deleted {deleted} duplicate files, freed ~{format_size(freed)}[/]")
            log_action("dupes", f"deleted {deleted}, freed {format_size(freed)}")


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
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]  # Never descend into skipped dirs
            for name in dirs:
                d = Path(root) / name
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
            try:
                if not entry.is_symlink():
                    continue
                os.stat(entry)  # Follows the link
                continue  # Target exists
            except OSError as e:
                # Only a missing target (or symlink loop) means broken;
                # permission errors etc. are not proof of breakage
                if e.errno not in (errno.ENOENT, errno.ELOOP):
                    continue
            try:
                target = os.readlink(entry)
            except OSError:
                continue
            target_abs = target if os.path.isabs(target) else os.path.join(os.path.dirname(str(entry)), target)
            target_abs = os.path.normpath(target_abs)
            if target_abs == "/Volumes" or target_abs.startswith("/Volumes" + os.sep):
                continue  # Volume may simply be unmounted
            broken.append((entry, target))

    if not broken:
        console.print("[green]No broken symlinks found![/]")
        return

    console.print(f"[yellow]Found {len(broken)} broken symlinks[/]")
    for s, target in broken[:30]:
        console.print(f"  [dim]{s} -> {target}[/]")

    if confirm_action(f"Delete {len(broken)} broken symlinks?", force_yes=force_yes):
        deleted = 0
        for s, _ in broken:
            try:
                s.unlink()
                deleted += 1
            except OSError:
                pass
        console.print(f"[green]Deleted {deleted} broken symlinks[/]")
        log_action("broken_symlinks", f"deleted {deleted}")
