"""Dev garbage collector for macmon."""

import json
import os
import shutil
import time
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
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    else:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def run_gc(
    scan: bool = False,
    clean: bool = False,
    all_gc: bool = False,
    force_yes: bool = False,
    permanent: bool = False,
    json_out: bool = False,
):
    console.print(Panel("[bold]macmon gc[/] -- Dev Garbage Collector", border_style="yellow"))

    cfg = load_config()
    gc_cfg = cfg.get("gc", {})
    nm_stale = gc_cfg.get("node_modules_stale_days", 14)
    venv_stale = gc_cfg.get("venv_stale_days", 14)

    categories = []

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        task = progress.add_task("Scanning dev garbage...", total=None)

        # npm cache
        progress.update(task, description="Scanning npm cache...")
        npm_cache = Path.home() / ".npm"
        if npm_cache.exists():
            s = dir_size(npm_cache)
            if s > 0:
                categories.append({"name": "npm cache (~/.npm)", "size": s, "count": 1, "paths": [str(npm_cache)], "action": "delete"})

        # pnpm store
        progress.update(task, description="Scanning pnpm store...")
        pnpm = Path.home() / ".pnpm-store"
        if pnpm.exists():
            s = dir_size(pnpm)
            if s > 0:
                categories.append({"name": "pnpm store", "size": s, "count": 1, "paths": [str(pnpm)], "action": "delete"})

        # yarn cache
        progress.update(task, description="Scanning yarn cache...")
        yarn = Path.home() / ".yarn/cache"
        if yarn.exists():
            s = dir_size(yarn)
            if s > 0:
                categories.append({"name": "yarn cache", "size": s, "count": 1, "paths": [str(yarn)], "action": "delete"})

        # bun cache
        bun_cache = Path.home() / ".bun/install/cache"
        if bun_cache.exists():
            s = dir_size(bun_cache)
            if s > 0:
                categories.append({"name": "bun cache", "size": s, "count": 1, "paths": [str(bun_cache)], "action": "delete"})

        # pip cache
        pip_cache = Path.home() / "Library/Caches/pip"
        if pip_cache.exists():
            s = dir_size(pip_cache)
            if s > 0:
                categories.append({"name": "pip cache", "size": s, "count": 1, "paths": [str(pip_cache)], "action": "delete"})

        # Stale node_modules
        progress.update(task, description="Scanning stale node_modules...")
        nm_result = _find_stale_node_modules(nm_stale)
        if nm_result["size"] > 0:
            categories.append(nm_result)

        # Stale venvs
        progress.update(task, description="Scanning stale venvs...")
        venv_result = _find_stale_venvs(venv_stale)
        if venv_result["size"] > 0:
            categories.append(venv_result)

        # __pycache__
        progress.update(task, description="Scanning __pycache__...")
        pycache_result = _find_pycache()
        if pycache_result["size"] > 0:
            categories.append(pycache_result)

        # Homebrew
        progress.update(task, description="Checking Homebrew...")
        brew_result = _check_homebrew()
        if brew_result:
            categories.append(brew_result)

        # Docker
        progress.update(task, description="Checking Docker...")
        docker_results = _check_docker()
        categories.extend(docker_results)

        # .DS_Store
        progress.update(task, description="Scanning .DS_Store files...")
        ds_result = _find_ds_store()
        if ds_result["size"] > 0:
            categories.append(ds_result)

        # Xcode DerivedData
        derived = Path.home() / "Library/Developer/Xcode/DerivedData"
        if derived.exists():
            s = dir_size(derived)
            if s > 0:
                categories.append({"name": "Xcode DerivedData", "size": s, "count": 1, "paths": [str(derived)], "action": "delete"})

        # iOS Simulators (unavailable)
        progress.update(task, description="Checking iOS Simulators...")
        sim_result = _check_simulators()
        if sim_result:
            categories.append(sim_result)

        # Go module cache
        go_result = _check_go_cache()
        if go_result:
            categories.append(go_result)

        # Cargo registry
        cargo_reg = Path.home() / ".cargo/registry"
        if cargo_reg.exists():
            s = dir_size(cargo_reg)
            if s > 0:
                categories.append({"name": "Cargo registry", "size": s, "count": 1, "paths": [str(cargo_reg)], "action": "delete"})

        progress.remove_task(task)

    # Display
    total_size = sum(c["size"] for c in categories)
    total_count = sum(c["count"] for c in categories)

    table = Table(title="Dev Garbage Summary", border_style="yellow")
    table.add_column("Category", style="cyan", width=35)
    table.add_column("Items", justify="right", width=8)
    table.add_column("Size", justify="right", width=12)

    for c in sorted(categories, key=lambda x: x["size"], reverse=True):
        table.add_row(c["name"], str(c["count"]), format_size(c["size"]))

    table.add_row("[bold]TOTAL[/]", f"[bold]{total_count}[/]", f"[bold]{format_size(total_size)}[/]")
    console.print(table)

    if json_out:
        console.print_json(json.dumps(categories, default=str))
        return

    if scan or total_size == 0:
        if total_size == 0:
            console.print("[green]No dev garbage found![/]")
        else:
            console.print(f"\n[dim]Preview only. Use --clean or --all to clean.[/]")
        return

    if all_gc:
        if confirm_action(f"Clean {format_size(total_size)} of dev garbage?", force_yes=force_yes):
            _execute_gc(categories, permanent)
    elif clean:
        _interactive_gc(categories, permanent)


def _execute_gc(categories: list[dict], permanent: bool = False):
    total_freed = 0
    for c in categories:
        action = c.get("action", "delete")
        if action == "brew_cleanup":
            run_cmd(["brew", "cleanup", "--prune=all"], timeout=120)
            total_freed += c["size"]
            console.print(f"  [green]Homebrew cleanup done[/]")
        elif action == "docker_prune":
            for cmd in c.get("commands", []):
                run_cmd(cmd, timeout=120)
            total_freed += c["size"]
            console.print(f"  [green]Docker cleanup done[/]")
        elif action == "simctl":
            run_cmd(["xcrun", "simctl", "delete", "unavailable"], timeout=60)
            total_freed += c["size"]
        else:
            for path_str in c.get("paths", []):
                p = Path(path_str)
                if p.exists():
                    _trash_or_rm(p, permanent)
            total_freed += c["size"]
            console.print(f"  [green]Cleaned {c['name']}[/]")

    console.print(f"\n[green bold]Total freed: ~{format_size(total_freed)}[/]")
    log_action("gc", f"freed ~{format_size(total_freed)}")

    db = get_db()
    db.execute(
        "INSERT INTO scan_history (scan_type, total_size, freed_size) VALUES (?, ?, ?)",
        ("gc", sum(c["size"] for c in categories), total_freed),
    )
    db.commit()
    db.close()


def _interactive_gc(categories: list[dict], permanent: bool = False):
    total_freed = 0
    for c in sorted(categories, key=lambda x: x["size"], reverse=True):
        if c["size"] == 0:
            continue
        if confirm_action(f"  Clean {c['name']} ({format_size(c['size'])})?"):
            action = c.get("action", "delete")
            if action == "brew_cleanup":
                run_cmd(["brew", "cleanup", "--prune=all"], timeout=120)
            elif action == "docker_prune":
                for cmd in c.get("commands", []):
                    run_cmd(cmd, timeout=120)
            elif action == "simctl":
                run_cmd(["xcrun", "simctl", "delete", "unavailable"], timeout=60)
            else:
                for path_str in c.get("paths", []):
                    p = Path(path_str)
                    if p.exists():
                        _trash_or_rm(p, permanent)
            total_freed += c["size"]
            console.print(f"  [green]Cleaned {c['name']}[/]")

    console.print(f"\n[green bold]Total freed: ~{format_size(total_freed)}[/]")
    log_action("gc_interactive", f"freed ~{format_size(total_freed)}")


# ── Scanners ─────────────────────────────────────────────────────────────

def _find_stale_node_modules(stale_days: int) -> dict:
    cutoff = time.time() - (stale_days * 86400)
    total_size = 0
    count = 0
    paths = []
    home = Path.home()

    # Search common project directories
    search_dirs = [
        home / "Projects", home / "Documents", home / "Desktop",
        home / "Developer", home / "dev", home / "code", home / "src",
        home / "work", home / "repos",
    ]

    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for nm in base.rglob("node_modules"):
                if not nm.is_dir():
                    continue
                # Check if parent project is stale
                project = nm.parent
                pkg = project / "package.json"
                if pkg.exists():
                    try:
                        if pkg.stat().st_mtime < cutoff:
                            s = dir_size(nm)
                            total_size += s
                            count += 1
                            paths.append(str(nm))
                    except OSError:
                        pass
                if count >= 100:  # Safety limit
                    break
        except (OSError, PermissionError):
            continue

    return {"name": f"Stale node_modules (>{stale_days}d)", "size": total_size, "count": count, "paths": paths, "action": "delete"}


def _find_stale_venvs(stale_days: int) -> dict:
    cutoff = time.time() - (stale_days * 86400)
    total_size = 0
    count = 0
    paths = []
    home = Path.home()

    search_dirs = [
        home / "Projects", home / "Documents", home / "Desktop",
        home / "Developer", home / "dev", home / "code",
    ]

    venv_names = [".venv", "venv", "env", ".env"]

    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for d in base.rglob("*"):
                if d.is_dir() and d.name in venv_names and (d / "pyvenv.cfg").exists():
                    try:
                        project = d.parent
                        if project.stat().st_mtime < cutoff:
                            s = dir_size(d)
                            total_size += s
                            count += 1
                            paths.append(str(d))
                    except OSError:
                        pass
                if count >= 50:
                    break
        except (OSError, PermissionError):
            continue

    return {"name": f"Stale venvs (>{stale_days}d)", "size": total_size, "count": count, "paths": paths, "action": "delete"}


def _find_pycache() -> dict:
    total_size = 0
    count = 0
    paths = []
    home = Path.home()

    search_dirs = [
        home / "Projects", home / "Documents", home / "Developer",
        home / "dev", home / "code",
    ]

    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for d in base.rglob("__pycache__"):
                if d.is_dir():
                    s = dir_size(d)
                    total_size += s
                    count += 1
                    paths.append(str(d))
                if count >= 200:
                    break
        except (OSError, PermissionError):
            continue

    return {"name": "__pycache__ dirs", "size": total_size, "count": count, "paths": paths, "action": "delete"}


def _check_homebrew():
    out, _, rc = run_cmd(["brew", "--cache"], timeout=10)
    if rc != 0:
        return None
    cache_path = Path(out.strip())
    if cache_path.exists():
        s = dir_size(cache_path)
        if s > 0:
            return {"name": "Homebrew cache", "size": s, "count": 1, "paths": [str(cache_path)], "action": "brew_cleanup"}
    return None


def _check_docker() -> list[dict]:
    results = []
    _, _, rc = run_cmd(["docker", "info"], timeout=5)
    if rc != 0:
        return results

    # Stopped containers
    out, _, rc = run_cmd(["docker", "ps", "-a", "--filter", "status=exited", "--format", "{{.ID}}"], timeout=10)
    if rc == 0 and out.strip():
        count = len(out.strip().splitlines())
        if count > 0:
            results.append({
                "name": f"Docker stopped containers ({count})",
                "size": 0,  # Can't easily determine without `docker system df`
                "count": count,
                "paths": [],
                "action": "docker_prune",
                "commands": [["docker", "container", "prune", "-f"]],
            })

    # Dangling images
    out, _, rc = run_cmd(["docker", "images", "-f", "dangling=true", "-q"], timeout=10)
    if rc == 0 and out.strip():
        count = len(out.strip().splitlines())
        if count > 0:
            results.append({
                "name": f"Docker dangling images ({count})",
                "size": 0,
                "count": count,
                "paths": [],
                "action": "docker_prune",
                "commands": [["docker", "image", "prune", "-f"]],
            })

    # Build cache
    out, _, rc = run_cmd(["docker", "system", "df", "--format", "{{.Size}}"], timeout=10)
    if rc == 0:
        results.append({
            "name": "Docker build cache",
            "size": 0,
            "count": 1,
            "paths": [],
            "action": "docker_prune",
            "commands": [["docker", "builder", "prune", "-f"]],
        })

    return results


def _find_ds_store() -> dict:
    total_size = 0
    count = 0
    paths = []
    home = Path.home()

    search_dirs = [home / "Desktop", home / "Documents", home / "Downloads", home / "Projects", home / "Developer"]

    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for f in base.rglob(".DS_Store"):
                if f.is_file():
                    total_size += f.stat().st_size
                    count += 1
                    paths.append(str(f))
                if count >= 500:
                    break
        except (OSError, PermissionError):
            continue

    return {"name": ".DS_Store files", "size": total_size, "count": count, "paths": paths, "action": "delete"}


def _check_simulators():
    out, _, rc = run_cmd(["xcrun", "simctl", "list", "devices", "unavailable", "-j"], timeout=10)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        count = sum(len(devices) for devices in data.get("devices", {}).values())
        if count > 0:
            return {
                "name": f"Unavailable iOS Simulators ({count})",
                "size": 0,
                "count": count,
                "paths": [],
                "action": "simctl",
            }
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _check_go_cache():
    out, _, rc = run_cmd(["go", "env", "GOPATH"], timeout=5)
    if rc != 0:
        return None
    gopath = Path(out.strip())
    cache = gopath / "pkg/mod/cache"
    if cache.exists():
        s = dir_size(cache)
        if s > 0:
            return {"name": "Go module cache", "size": s, "count": 1, "paths": [str(cache)], "action": "delete"}
    return None
