"""Advanced Docker management for macmon."""

import json
import time
from datetime import datetime

from rich.panel import Panel
from rich.table import Table

from .utils import confirm_action, console, format_size, log_action, run_cmd


def _docker_available() -> bool:
    _, _, rc = run_cmd(["docker", "info"], timeout=5)
    if rc != 0:
        console.print("[yellow]Docker is not running or not installed.[/]")
        return False
    return True


def run_docker(
    status: bool = False,
    containers: bool = False,
    images: bool = False,
    volumes: bool = False,
    networks: bool = False,
    prune: bool = False,
    stop_all: bool = False,
    restart: str = None,
    logs: str = None,
    stats: bool = False,
    compose: bool = False,
    scan: bool = False,
    yes: bool = False,
    json_out: bool = False,
):
    if not _docker_available():
        return

    if prune:
        _docker_prune(yes)
        return
    if stop_all:
        _docker_stop_all(yes)
        return
    if restart:
        _docker_restart(restart)
        return
    if logs:
        _docker_logs(logs)
        return
    if stats:
        _docker_stats()
        return
    if containers:
        _list_containers(json_out)
        return
    if images:
        _list_images(json_out)
        return
    if volumes:
        _list_volumes(json_out)
        return
    if networks:
        _list_networks(json_out)
        return
    if compose:
        _list_compose(json_out)
        return
    if scan:
        _docker_security_scan()
        return

    # Default: full overview
    _docker_overview(json_out)


def _docker_overview(json_out: bool = False):
    console.print(Panel("[bold blue]macmon docker[/] -- Docker Overview", border_style="blue"))

    # System info
    out, _, rc = run_cmd(["docker", "system", "df"], timeout=15)
    if rc == 0:
        console.print(Panel(out.strip(), title="Disk Usage", border_style="dim"))

    # Running containers
    out, _, rc = run_cmd(["docker", "ps", "--format", "table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Size}}"], timeout=10)
    if rc == 0:
        lines = out.strip().splitlines()
        if len(lines) > 1:
            console.print(Panel(out.strip(), title=f"Running Containers ({len(lines)-1})", border_style="green"))
        else:
            console.print("[dim]No running containers.[/]")

    # Stopped containers
    out, _, rc = run_cmd(["docker", "ps", "-a", "--filter", "status=exited", "--format", "table {{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Size}}"], timeout=10)
    if rc == 0:
        lines = out.strip().splitlines()
        if len(lines) > 1:
            console.print(Panel(out.strip(), title=f"Stopped Containers ({len(lines)-1})", border_style="yellow"))

    # Dangling images
    out, _, rc = run_cmd(["docker", "images", "-f", "dangling=true", "-q"], timeout=10)
    if rc == 0 and out.strip():
        count = len(out.strip().splitlines())
        console.print(f"[yellow]Dangling images: {count}[/]")

    # Volumes
    out, _, rc = run_cmd(["docker", "volume", "ls", "-q"], timeout=10)
    if rc == 0:
        vol_count = len(out.strip().splitlines()) if out.strip() else 0
        console.print(f"[dim]Volumes: {vol_count}[/]")

    # Suggestions
    console.print("\n[dim]Commands: --containers, --images, --volumes, --prune, --stats, --scan[/]")


def _list_containers(json_out: bool = False):
    console.print(Panel("[bold]Docker Containers[/]", border_style="green"))

    out, _, rc = run_cmd([
        "docker", "ps", "-a", "--format",
        "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Size}}\t{{.State}}"
    ], timeout=10)
    if rc != 0:
        return

    table = Table(title="All Containers", border_style="green")
    table.add_column("ID", width=12, style="dim")
    table.add_column("Name", width=22)
    table.add_column("Image", width=25)
    table.add_column("Status", width=20)
    table.add_column("Ports", width=20)
    table.add_column("Size", width=12, justify="right")

    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 6:
            state = parts[6] if len(parts) > 6 else ""
            status_style = "green" if state == "running" else "yellow" if state == "exited" else "red"
            table.add_row(
                parts[0][:12], parts[1][:22], parts[2][:25],
                f"[{status_style}]{parts[3][:20]}[/]",
                parts[4][:20], parts[5],
            )

    console.print(table)


def _list_images(json_out: bool = False):
    console.print(Panel("[bold]Docker Images[/]", border_style="cyan"))

    out, _, rc = run_cmd([
        "docker", "images", "--format",
        "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}"
    ], timeout=10)
    if rc != 0:
        return

    table = Table(title="Docker Images", border_style="cyan")
    table.add_column("Repository", width=30)
    table.add_column("Tag", width=15)
    table.add_column("ID", width=12, style="dim")
    table.add_column("Size", width=12, justify="right")
    table.add_column("Created", width=18)

    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 5:
            repo_style = "red" if parts[0] == "<none>" else "white"
            table.add_row(
                f"[{repo_style}]{parts[0][:30]}[/]",
                parts[1][:15], parts[2][:12], parts[3], parts[4][:18],
            )

    console.print(table)

    # Count dangling
    out, _, rc = run_cmd(["docker", "images", "-f", "dangling=true", "-q"], timeout=5)
    if rc == 0 and out.strip():
        console.print(f"[yellow]Dangling images: {len(out.strip().splitlines())} (use --prune to clean)[/]")


def _list_volumes(json_out: bool = False):
    console.print(Panel("[bold]Docker Volumes[/]", border_style="magenta"))

    out, _, rc = run_cmd([
        "docker", "volume", "ls", "--format",
        "{{.Name}}\t{{.Driver}}\t{{.Mountpoint}}"
    ], timeout=10)
    if rc != 0:
        return

    # Get volume sizes
    table = Table(title="Docker Volumes", border_style="magenta")
    table.add_column("Name", width=40)
    table.add_column("Driver", width=10)
    table.add_column("In Use", width=8)

    # Get container volumes in use
    used_out, _, _ = run_cmd(["docker", "ps", "-a", "--format", "{{.Mounts}}"], timeout=5)
    used_vols = used_out if used_out else ""

    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name = parts[0]
            in_use = "[green]Yes[/]" if name in used_vols else "[dim]No[/]"
            table.add_row(name[:40], parts[1], in_use)

    console.print(table)

    # Dangling volumes
    out, _, rc = run_cmd(["docker", "volume", "ls", "-f", "dangling=true", "-q"], timeout=5)
    if rc == 0 and out.strip():
        console.print(f"[yellow]Unused volumes: {len(out.strip().splitlines())} (use --prune to clean)[/]")


def _list_networks(json_out: bool = False):
    console.print(Panel("[bold]Docker Networks[/]", border_style="blue"))

    out, _, rc = run_cmd([
        "docker", "network", "ls", "--format",
        "{{.ID}}\t{{.Name}}\t{{.Driver}}\t{{.Scope}}"
    ], timeout=10)
    if rc != 0:
        return

    table = Table(title="Docker Networks", border_style="blue")
    table.add_column("ID", width=12, style="dim")
    table.add_column("Name", width=30)
    table.add_column("Driver", width=12)
    table.add_column("Scope", width=10)

    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            table.add_row(parts[0][:12], parts[1], parts[2], parts[3])

    console.print(table)


def _docker_stats():
    console.print(Panel("[bold]Docker Container Stats[/] (live, Ctrl+C to stop)", border_style="green"))
    import subprocess
    try:
        subprocess.run(
            ["docker", "stats", "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}"],
            timeout=30,
        )
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass


def _docker_prune(force_yes: bool = False):
    console.print(Panel("[bold red]macmon docker --prune[/] -- Full Docker Cleanup", border_style="red"))

    # Show what will be cleaned
    out, _, _ = run_cmd(["docker", "system", "df"], timeout=10)
    if out:
        console.print(Panel(out.strip(), title="Current Usage", border_style="dim"))

    steps = [
        ("Stopped containers", ["docker", "container", "prune", "-f"]),
        ("Dangling images", ["docker", "image", "prune", "-f"]),
        ("Unused volumes", ["docker", "volume", "prune", "-f"]),
        ("Build cache", ["docker", "builder", "prune", "-f"]),
        ("Unused networks", ["docker", "network", "prune", "-f"]),
    ]

    if not confirm_action("Run full Docker prune? This removes stopped containers, dangling images, unused volumes, build cache, and unused networks.", force_yes=force_yes):
        return

    for name, cmd in steps:
        console.print(f"  [cyan]Cleaning {name}...[/]")
        out, err, rc = run_cmd(cmd, timeout=60)
        if rc == 0:
            # Parse freed space from output
            if "reclaimed" in (out or "").lower():
                for line in out.splitlines():
                    if "reclaimed" in line.lower():
                        console.print(f"  [green]{name}: {line.strip()}[/]")
                        break
            else:
                console.print(f"  [green]{name}: done[/]")
        else:
            console.print(f"  [yellow]{name}: {err.strip()[:60] if err else 'skipped'}[/]")

    # Show after
    out, _, _ = run_cmd(["docker", "system", "df"], timeout=10)
    if out:
        console.print(Panel(out.strip(), title="After Cleanup", border_style="green"))

    log_action("docker_prune", "full cleanup")


def _docker_stop_all(force_yes: bool = False):
    out, _, rc = run_cmd(["docker", "ps", "-q"], timeout=5)
    if rc != 0 or not out.strip():
        console.print("[dim]No running containers.[/]")
        return

    container_ids = out.strip().splitlines()
    console.print(f"[yellow]Found {len(container_ids)} running containers.[/]")

    if confirm_action(f"Stop all {len(container_ids)} containers?", force_yes=force_yes):
        run_cmd(["docker", "stop"] + container_ids, timeout=60)
        console.print(f"[green]Stopped {len(container_ids)} containers.[/]")
        log_action("docker_stop_all", f"{len(container_ids)} containers")


def _docker_restart(container: str):
    out, err, rc = run_cmd(["docker", "restart", container], timeout=30)
    if rc == 0:
        console.print(f"[green]Restarted container: {container}[/]")
        log_action("docker_restart", container)
    else:
        console.print(f"[red]Failed to restart {container}: {err.strip()}[/]")


def _docker_logs(container: str):
    out, err, rc = run_cmd(["docker", "logs", "--tail", "50", container], timeout=10)
    if rc == 0:
        console.print(Panel(out.strip()[-3000:] if out else "[dim]No logs[/]", title=f"Logs: {container} (last 50)", border_style="dim"))
    else:
        console.print(f"[red]Could not get logs: {err.strip()}[/]")


def _list_compose(json_out: bool = False):
    console.print(Panel("[bold]Docker Compose Projects[/]", border_style="cyan"))

    # Try docker compose ls
    out, _, rc = run_cmd(["docker", "compose", "ls", "--format", "json"], timeout=10)
    if rc == 0 and out.strip():
        try:
            projects = json.loads(out)
            table = Table(title="Compose Projects", border_style="cyan")
            table.add_column("Name", width=25)
            table.add_column("Status", width=20)
            table.add_column("Config", width=40)

            for p in projects:
                status = p.get("Status", "")
                status_color = "green" if "running" in status.lower() else "yellow"
                table.add_row(
                    p.get("Name", ""),
                    f"[{status_color}]{status}[/]",
                    p.get("ConfigFiles", "")[:40],
                )
            console.print(table)
        except json.JSONDecodeError:
            console.print(out)
    else:
        console.print("[dim]No Compose projects found or Docker Compose not available.[/]")


def _docker_security_scan():
    console.print(Panel("[bold red]macmon docker --scan[/] -- Docker Security Audit", border_style="red"))

    findings = []

    # Check for containers running as root
    console.print("[cyan]Checking container privileges...[/]")
    out, _, rc = run_cmd([
        "docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}"
    ], timeout=10)
    if rc == 0:
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                cid = parts[0]
                name = parts[1]
                # Check if running as root
                inspect_out, _, _ = run_cmd([
                    "docker", "inspect", "--format",
                    "{{.HostConfig.Privileged}} {{.HostConfig.NetworkMode}} {{.Config.User}}",
                    cid,
                ], timeout=5)
                if inspect_out:
                    priv_parts = inspect_out.strip().split()
                    if len(priv_parts) >= 1 and priv_parts[0] == "true":
                        findings.append(f"[red]PRIVILEGED[/] container: {name}")
                    if len(priv_parts) >= 2 and priv_parts[1] == "host":
                        findings.append(f"[yellow]HOST NETWORK[/] container: {name}")
                    if len(priv_parts) >= 3 and (not priv_parts[2] or priv_parts[2] == "root"):
                        findings.append(f"[yellow]ROOT USER[/] container: {name}")

    # Check for exposed ports
    console.print("[cyan]Checking exposed ports...[/]")
    out, _, rc = run_cmd(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], timeout=10)
    if rc == 0:
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and "0.0.0.0:" in parts[1]:
                findings.append(f"[yellow]PUBLIC PORT[/] {parts[0]}: {parts[1][:50]}")

    # Check for outdated base images
    console.print("[cyan]Checking image ages...[/]")
    out, _, rc = run_cmd([
        "docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.CreatedSince}}"
    ], timeout=10)
    if rc == 0:
        for line in out.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                age = parts[1].lower()
                if "year" in age or "month" in age:
                    months = 0
                    if "year" in age:
                        try:
                            months = int(age.split()[0]) * 12
                        except ValueError:
                            pass
                    elif "month" in age:
                        try:
                            months = int(age.split()[0])
                        except ValueError:
                            pass
                    if months > 6:
                        findings.append(f"[yellow]OUTDATED[/] image {parts[0]}: {parts[1]}")

    # Display
    if not findings:
        console.print("[green bold]No Docker security issues found![/]")
        return

    table = Table(title=f"Docker Security ({len(findings)} findings)", border_style="red")
    table.add_column("#", width=4)
    table.add_column("Finding", width=60)

    for i, f in enumerate(findings, 1):
        table.add_row(str(i), f)

    console.print(table)
