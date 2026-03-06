"""Network monitor and port manager for macmon."""

import json
import re

import psutil
from rich.panel import Panel
from rich.table import Table

from .utils import console, format_size, log_action, run_cmd


def run_network(
    listening: bool = False,
    established: bool = False,
    process: str = None,
    json_out: bool = False,
):
    console.print(Panel("[bold]macmon network[/] -- Network Connections", border_style="blue"))

    connections = []

    # Try psutil first, fall back to netstat/lsof
    try:
        for conn in psutil.net_connections(kind="inet"):
            if not conn.pid:
                continue
            status = conn.status
            if listening and status != "LISTEN":
                continue
            if established and status != "ESTABLISHED":
                continue
            try:
                p = psutil.Process(conn.pid)
                pname = p.name()
                ram = p.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pname = "?"
                ram = 0
            if process and process.lower() not in pname.lower():
                continue
            local = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "-"
            remote = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "-"
            connections.append({
                "pid": conn.pid, "process": pname, "local": local,
                "remote": remote, "status": status, "ram": ram,
            })
    except (psutil.AccessDenied, PermissionError):
        # Fallback: parse netstat output
        connections = _netstat_fallback(listening, established, process)

    if json_out:
        console.print_json(json.dumps(connections, default=str))
        return

    if not connections:
        console.print("[dim]No matching connections found.[/]")
        return

    # Deduplicate
    seen = set()
    unique = []
    for c in connections:
        key = (c["pid"], c["local"], c["remote"], c["status"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    unique.sort(key=lambda x: x["process"])

    table = Table(title="Network Connections", border_style="blue")
    table.add_column("Process", width=18)
    table.add_column("PID", width=7, style="dim")
    table.add_column("Local", width=22)
    table.add_column("Remote", width=28)
    table.add_column("Status", width=14)
    table.add_column("RAM", width=10, justify="right")

    for c in unique[:60]:
        status_color = {
            "LISTEN": "cyan", "ESTABLISHED": "green",
            "CLOSE_WAIT": "yellow", "TIME_WAIT": "dim", "SYN_SENT": "yellow",
        }.get(c["status"], "white")
        table.add_row(
            c["process"][:18], str(c["pid"]), c["local"],
            c["remote"], f"[{status_color}]{c['status']}[/]",
            format_size(c["ram"]),
        )

    console.print(table)
    console.print(f"[dim]{len(unique)} connections shown[/]")


def _netstat_fallback(listening: bool, established: bool, process: str = None) -> list[dict]:
    """Parse netstat -anv output as fallback."""
    connections = []
    out, _, rc = run_cmd(["netstat", "-anv", "-p", "tcp"], timeout=10)
    if rc != 0:
        console.print("[yellow]Could not get network connections. Try running with sudo.[/]")
        return connections

    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        if parts[0] not in ("tcp4", "tcp6", "tcp46"):
            continue

        local = parts[3] if len(parts) > 3 else "-"
        remote = parts[4] if len(parts) > 4 else "-"
        status = parts[5] if len(parts) > 5 else "?"

        if listening and status != "LISTEN":
            continue
        if established and status != "ESTABLISHED":
            continue

        # Try to get PID from lsof for the local port
        pid = 0
        pname = "?"
        ram = 0
        port_match = re.search(r'\.(\d+)$', local)
        if port_match:
            port = port_match.group(1)
            pid_out, _, _ = run_cmd(["lsof", "-ti", f"tcp:{port}"], timeout=2)
            if pid_out.strip():
                try:
                    pid = int(pid_out.strip().splitlines()[0])
                    p = psutil.Process(pid)
                    pname = p.name()
                    ram = p.memory_info().rss
                except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        if process and process.lower() not in pname.lower():
            continue

        connections.append({
            "pid": pid, "process": pname, "local": local,
            "remote": remote, "status": status, "ram": ram,
        })

    return connections[:100]


def flush_dns_cache():
    console.print("[cyan]Flushing DNS cache...[/]")
    _, err1, rc1 = run_cmd(["dscacheutil", "-flushcache"], sudo=True)
    _, err2, rc2 = run_cmd(["killall", "-HUP", "mDNSResponder"], sudo=True)

    if rc1 == 0 and rc2 == 0:
        console.print("[green]DNS cache flushed successfully.[/]")
        log_action("flush_dns")
    else:
        console.print("[yellow]DNS flush completed with warnings.[/]")
        if err1:
            console.print(f"  [dim]{err1.strip()}[/]")
        if err2:
            console.print(f"  [dim]{err2.strip()}[/]")
