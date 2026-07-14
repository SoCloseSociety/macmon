"""Network security, malware detection, and remote connection monitor for macmon."""

import ipaddress
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import psutil
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .utils import (
    confirm_action,
    console,
    format_size,
    get_db,
    log_action,
    run_cmd,
    send_notification,
)
from .platform_compat import require_os

# ── Known suspicious indicators ──────────────────────────────────────────

SUSPICIOUS_PORTS = {
    4444: "Metasploit default",
    5555: "Android ADB / backdoor",
    6666: "IRC botnet",
    6667: "IRC botnet",
    1337: "Common backdoor",
    31337: "Back Orifice",
    12345: "NetBus trojan",
    27374: "SubSeven trojan",
}

# pf anchor under Apple's wildcard anchor ("com.apple/*" in /etc/pf.conf)
# so macmon rules are evaluated without ever replacing the main ruleset.
PF_ANCHOR = "com.apple/250.macmon"

SUSPICIOUS_REMOTE_IPS = {
    # Known malicious ranges -- placeholder patterns
}

KNOWN_REMOTE_TOOLS = [
    "teamviewer", "anydesk", "screenconnect", "rustdesk",
    "vnc", "realvnc", "tightvnc", "splashtop", "logmein",
    "ammyy", "supremo", "ultraviewer", "remotepc",
    "parsec", "nomachine", "chrome remote",
]

SUSPICIOUS_PROCESS_NAMES = [
    "meterpreter", "reverse_shell", "netcat", "ncat",
    "cryptominer", "xmrig", "coinhive", "minerd",
    "keylogger", "screenlogger", "spyware",
    "rat_", "backdoor", "rootkit", "trojan",
]
# These require exact match (too short for substring matching)
SUSPICIOUS_EXACT_NAMES = {"nc"}

LAUNCHD_SUSPICIOUS_PATTERNS = [
    r"^\d+$",  # Numeric only
    r"tmp|temp|cache|hidden|\.hidden",
]

# Known safe macOS processes that listen on network
SAFE_LISTENERS = {
    "rapportd", "airplayxpcsender", "controlcenter",
    "sharingd", "identityservicesd", "bluetoothd",
    "mDNSResponder", "configd", "apsd",
}


# ── Main security command ────────────────────────────────────────────────

def run_security(
    scan: bool = False,
    connections: bool = False,
    firewall: bool = False,
    malware: bool = False,
    remote: bool = False,
    rules: bool = False,
    block_ip: str = None,
    unblock_ip: str = None,
    quarantine: str = None,
    json_out: bool = False,
):
    msg = require_os("macOS")
    if msg:
        console.print(f"[yellow]{msg}[/]")
        return
    if block_ip:
        _block_ip(block_ip)
        return
    if unblock_ip:
        _unblock_ip(unblock_ip)
        return
    if quarantine:
        _quarantine_process(quarantine)
        return
    if firewall:
        _show_firewall()
        return
    if connections:
        _scan_connections(json_out)
        return
    if malware:
        _scan_malware(json_out)
        return
    if remote:
        _scan_remote_access(json_out)
        return
    if rules:
        _show_security_rules()
        return

    # Default: full security scan
    _full_security_scan(json_out)


def _full_security_scan(json_out: bool = False):
    console.print(Panel("[bold red]macmon security[/] -- Full Security Scan", border_style="red"))

    findings = []
    score = 100

    # 1. Firewall status
    console.print("[cyan]Checking firewall...[/]")
    fw = _check_firewall()
    findings.append(fw)
    if fw["status"] == "fail":
        score -= 15

    # 2. SIP status
    console.print("[cyan]Checking System Integrity Protection...[/]")
    sip = _check_sip()
    findings.append(sip)
    if sip["status"] == "fail":
        score -= 20

    # 3. Gatekeeper
    console.print("[cyan]Checking Gatekeeper...[/]")
    gk = _check_gatekeeper()
    findings.append(gk)
    if gk["status"] == "fail":
        score -= 10

    # 4. FileVault
    console.print("[cyan]Checking FileVault encryption...[/]")
    fv = _check_filevault()
    findings.append(fv)
    if fv["status"] == "fail":
        score -= 15

    # 5. Suspicious connections
    console.print("[cyan]Scanning network connections...[/]")
    sus_conns = _find_suspicious_connections()
    if sus_conns:
        findings.append({
            "name": "Suspicious Connections",
            "status": "fail",
            "detail": f"{len(sus_conns)} suspicious connection(s) found",
            "items": sus_conns,
        })
        score -= min(30, len(sus_conns) * 10)
    else:
        findings.append({"name": "Suspicious Connections", "status": "pass", "detail": "No suspicious connections"})

    # 6. Remote access tools
    console.print("[cyan]Scanning for remote access tools...[/]")
    remote_tools = _find_remote_tools()
    if remote_tools:
        findings.append({
            "name": "Remote Access Tools",
            "status": "warn",
            "detail": f"{len(remote_tools)} remote tool(s) running",
            "items": remote_tools,
        })
        score -= 5
    else:
        findings.append({"name": "Remote Access Tools", "status": "pass", "detail": "None running"})

    # 7. Suspicious processes
    console.print("[cyan]Scanning for suspicious processes...[/]")
    sus_procs = _find_suspicious_processes()
    if sus_procs:
        findings.append({
            "name": "Suspicious Processes",
            "status": "fail",
            "detail": f"{len(sus_procs)} suspicious process(es)",
            "items": sus_procs,
        })
        score -= min(30, len(sus_procs) * 15)
    else:
        findings.append({"name": "Suspicious Processes", "status": "pass", "detail": "None found"})

    # 8. Suspicious LaunchAgents/Daemons
    console.print("[cyan]Scanning startup items...[/]")
    sus_launch = _find_suspicious_launch_items()
    if sus_launch:
        findings.append({
            "name": "Suspicious Startup Items",
            "status": "warn",
            "detail": f"{len(sus_launch)} suspicious item(s)",
            "items": sus_launch,
        })
        score -= min(15, len(sus_launch) * 5)
    else:
        findings.append({"name": "Suspicious Startup Items", "status": "pass", "detail": "None found"})

    # 9. Open sharing services
    console.print("[cyan]Checking sharing services...[/]")
    sharing = _check_sharing()
    findings.append(sharing)
    if sharing["status"] == "warn":
        score -= 5

    # 10. SSH check
    console.print("[cyan]Checking SSH...[/]")
    ssh = _check_ssh_security()
    findings.append(ssh)
    if ssh["status"] == "warn":
        score -= 5

    score = max(0, score)

    # Display results
    table = Table(
        title=f"Security Score: {score}/100",
        border_style="green" if score >= 80 else "yellow" if score >= 50 else "red",
    )
    table.add_column("Check", style="cyan", width=28)
    table.add_column("Status", width=8)
    table.add_column("Details", width=45)

    for f in findings:
        status_str = {
            "pass": "[green]PASS[/]",
            "warn": "[yellow]WARN[/]",
            "fail": "[red]FAIL[/]",
        }.get(f["status"], "[dim]?[/]")
        table.add_row(f["name"], status_str, f["detail"])

    console.print(table)

    # Show detailed findings for failures
    for f in findings:
        if f.get("items"):
            console.print(f"\n[bold red]{f['name']}:[/]")
            for item in f["items"]:
                console.print(f"  [red]> {item}[/]")

    # Recommendations
    console.print(f"\n[bold]Security Score: [{'green' if score >= 80 else 'yellow' if score >= 50 else 'red'}]{score}/100[/][/]")

    actions = []
    for f in findings:
        if f["status"] == "fail":
            hint = f.get("fix_hint", f"Fix: {f['name']}")
            actions.append(f"[red]{hint}[/]")
        elif f["status"] == "warn":
            hint = f.get("fix_hint", f"Review: {f['name']}")
            actions.append(f"[yellow]{hint}[/]")

    if actions:
        console.print("\n[bold]Actions:[/]")
        for i, a in enumerate(actions, 1):
            console.print(f"  {i}. {a}")

    if json_out:
        console.print_json(json.dumps({"score": score, "findings": findings}, default=str))

    log_action("security_scan", f"score={score}")

    # Save to DB
    try:
        db = get_db()
        db.execute(
            "INSERT INTO scan_history (scan_type, details) VALUES (?, ?)",
            ("security", f"score={score}"),
        )
        db.commit()
        db.close()
    except Exception:
        pass


# ── Individual checks ────────────────────────────────────────────────────

SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"


def _check_firewall() -> dict:
    if Path(SOCKETFILTERFW).exists():
        out, _, rc = run_cmd([SOCKETFILTERFW, "--getglobalstate"], timeout=5)
        if rc == 0 and out.strip():
            low = out.lower()
            if "disabled" in low or "state = 0" in low:
                return {
                    "name": "macOS Firewall", "status": "fail",
                    "detail": "DISABLED",
                    "fix_hint": "Enable: System Settings > Network > Firewall > ON",
                }
            if "enabled" in low or "state = 1" in low or "state = 2" in low:
                return {"name": "macOS Firewall", "status": "pass", "detail": out.strip().splitlines()[0], "fix_hint": ""}
        return {"name": "macOS Firewall", "status": "warn", "detail": "Could not determine status"}

    # Fallback: legacy plist (only if socketfilterfw binary is missing)
    out, _, rc = run_cmd(["defaults", "read", "/Library/Preferences/com.apple.alf", "globalstate"], timeout=5)
    if rc == 0:
        state = out.strip()
        if state in ("1", "2"):
            return {"name": "macOS Firewall", "status": "pass", "detail": f"Enabled (mode {state})", "fix_hint": ""}
        else:
            return {
                "name": "macOS Firewall", "status": "fail",
                "detail": "DISABLED",
                "fix_hint": "Enable: System Settings > Network > Firewall > ON",
            }
    return {"name": "macOS Firewall", "status": "warn", "detail": "Could not determine status"}


def _check_sip() -> dict:
    out, _, rc = run_cmd(["csrutil", "status"], timeout=5)
    if rc == 0:
        if "enabled" in out.lower():
            return {"name": "System Integrity Protection", "status": "pass", "detail": "Enabled"}
        else:
            return {
                "name": "System Integrity Protection", "status": "fail",
                "detail": "DISABLED -- your system is vulnerable",
                "fix_hint": "Boot to Recovery > Terminal > csrutil enable",
            }
    return {"name": "System Integrity Protection", "status": "warn", "detail": "Could not check"}


def _check_gatekeeper() -> dict:
    out, _, rc = run_cmd(["spctl", "--status"], timeout=5)
    if rc == 0:
        if "enabled" in out.lower():
            return {"name": "Gatekeeper", "status": "pass", "detail": "Enabled"}
        else:
            return {
                "name": "Gatekeeper", "status": "fail",
                "detail": "DISABLED",
                "fix_hint": "Enable: sudo spctl --master-enable",
            }
    return {"name": "Gatekeeper", "status": "warn", "detail": "Could not check"}


def _check_filevault() -> dict:
    out, _, rc = run_cmd(["fdesetup", "status"], timeout=5)
    if rc == 0:
        if re.search(r"FileVault is On", out):
            return {"name": "FileVault Encryption", "status": "pass", "detail": "Enabled"}
        else:
            return {
                "name": "FileVault Encryption", "status": "fail",
                "detail": "DISABLED -- disk not encrypted",
                "fix_hint": "Enable: System Settings > Privacy & Security > FileVault > ON",
            }
    return {"name": "FileVault Encryption", "status": "warn", "detail": "Could not check"}


def _parse_lsof_line(line: str):
    """Parse an `lsof +c 0 -i` line into (process, pid, user, name_col, state).

    Anchors on the IPv4/IPv6 TYPE column so command names containing
    spaces (possible with +c 0) do not shift the fields.
    """
    parts = line.split()
    for i, tok in enumerate(parts):
        if tok in ("IPv4", "IPv6") and i >= 4:
            # Fields after TYPE: DEVICE, SIZE/OFF, NODE, NAME, [STATE]
            process = " ".join(parts[:i - 3])
            pid = parts[i - 3]
            user = parts[i - 2]
            name_col = parts[i + 4] if len(parts) > i + 4 else ""
            state = parts[i + 5] if len(parts) > i + 5 else ""
            return process, pid, user, name_col, state
    return None


def _suspicious_port_hit(name_col: str, state: str):
    """Return (port, desc) if a REMOTE port of an outbound connection or a
    LOCAL listening port is in SUSPICIOUS_PORTS, else None."""
    match = re.search(r'->\S+:(\d+)$', name_col)
    if match:
        # Outbound connection -- check the remote port only
        remote_port = int(match.group(1))
        if remote_port in SUSPICIOUS_PORTS:
            return remote_port, SUSPICIOUS_PORTS[remote_port]
        return None
    if "LISTEN" in state:
        match = re.search(r':(\d+)$', name_col)
        if match:
            local_port = int(match.group(1))
            if local_port in SUSPICIOUS_PORTS:
                return local_port, SUSPICIOUS_PORTS[local_port]
    return None


def _find_suspicious_connections() -> list[str]:
    suspicious = []

    # Use lsof for connection scanning (no root needed)
    out, _, rc = run_cmd(["lsof", "+c", "0", "-i", "-n", "-P"], timeout=15)
    if rc != 0:
        return suspicious

    for line in out.splitlines()[1:]:  # Skip header
        parsed = _parse_lsof_line(line)
        if not parsed:
            continue
        process, pid, _, name_col, state = parsed

        hit = _suspicious_port_hit(name_col, state)
        if hit:
            port, desc = hit
            if "LISTEN" in state:
                suspicious.append(f"PID {pid} ({process}) listening on port {port} ({desc})")
            else:
                suspicious.append(f"PID {pid} ({process}) -> port {port} ({desc})")

    return suspicious


def _find_remote_tools() -> list[str]:
    found = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            pname = p.info["name"].lower()
            for tool in KNOWN_REMOTE_TOOLS:
                if tool in pname:
                    ram = p.memory_info().rss if p.memory_info() else 0
                    found.append(f"PID {p.info['pid']}: {p.info['name']} ({format_size(ram)})")
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _find_suspicious_processes() -> list[str]:
    found = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            pname = p.info["name"].lower()
            for sus in SUSPICIOUS_PROCESS_NAMES:
                if sus in pname:
                    found.append(f"PID {p.info['pid']}: {p.info['name']}")
                    break
            else:
                if pname in SUSPICIOUS_EXACT_NAMES:
                    found.append(f"PID {p.info['pid']}: {p.info['name']}")


            # Check for processes running from /tmp or hidden dirs
            exe = p.info.get("exe") or ""
            if exe:
                if "/tmp/" in exe or "/.hidden" in exe or "/var/tmp/" in exe:
                    found.append(f"PID {p.info['pid']}: {p.info['name']} running from {exe}")

            # Check for crypto mining indicators
            cmdline = p.info.get("cmdline") or []
            cmd_str = " ".join(str(c) for c in cmdline).lower()
            if any(kw in cmd_str for kw in ["stratum+tcp", "xmrig", "minerd", "cryptonight", "monero"]):
                found.append(f"PID {p.info['pid']}: Possible crypto miner -- {p.info['name']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def _find_suspicious_launch_items() -> list[str]:
    suspicious = []
    search_dirs = [
        Path.home() / "Library/LaunchAgents",
        Path("/Library/LaunchAgents"),
        Path("/Library/LaunchDaemons"),
    ]

    for d in search_dirs:
        if not d.exists():
            continue
        for plist in d.glob("*.plist"):
            label = plist.stem
            # Check for suspicious patterns
            for pattern in LAUNCHD_SUSPICIOUS_PATTERNS:
                if re.search(pattern, label.lower()):
                    suspicious.append(f"{plist}: matches suspicious pattern")
                    break

            # Check if binary runs from unusual location
            try:
                import plistlib
                with open(plist, "rb") as f:
                    data = plistlib.load(f)
                program = data.get("Program", "")
                if not program and "ProgramArguments" in data:
                    args = data["ProgramArguments"]
                    if args and isinstance(args, list):
                        program = args[0]
                if program:
                    p = Path(program)
                    if "/tmp/" in str(p) or "/.hidden" in str(p) or "/var/tmp/" in str(p):
                        suspicious.append(f"{plist}: binary in suspicious location ({program})")
                    if not p.exists():
                        pass  # Already handled by startup --broken
            except Exception:
                pass

    return suspicious


def _service_active(label: str, port: int) -> bool:
    """Detect a system daemon via `launchctl print system/<label>` (launchctl
    list cannot see system daemons) with a listening-port check as fallback."""
    _, _, rc = run_cmd(["launchctl", "print", f"system/{label}"], timeout=5)
    if rc == 0:
        return True
    out, _, rc = run_cmd(["lsof", "+c", "0", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], timeout=5)
    return rc == 0 and bool(out.strip())


def _check_sharing() -> dict:
    out, _, rc = run_cmd(["defaults", "read", "/Library/Preferences/com.apple.RemoteManagement", "ARD_AllLocalUsers"], timeout=5)
    remote_mgmt = rc == 0 and out.strip() == "1"

    screen_sharing = _service_active("com.apple.screensharing", 5900)

    # Check file sharing
    file_sharing = _service_active("com.apple.smbd", 445)

    services = []
    if remote_mgmt:
        services.append("Remote Management")
    if screen_sharing:
        services.append("Screen Sharing")
    if file_sharing:
        services.append("File Sharing (SMB)")

    if services:
        return {
            "name": "Sharing Services",
            "status": "warn",
            "detail": f"Active: {', '.join(services)}",
            "fix_hint": "Disable unused sharing in System Settings > General > Sharing",
        }
    return {"name": "Sharing Services", "status": "pass", "detail": "No sharing services active"}


def _check_ssh_security() -> dict:
    # Check if SSH is enabled
    ssh_running = _service_active("com.openssh.sshd", 22)

    issues = []
    if ssh_running:
        issues.append("SSH daemon is running")

    # Check for password auth in sshd_config
    sshd_config = Path("/etc/ssh/sshd_config")
    if sshd_config.exists():
        try:
            content = sshd_config.read_text()
            if "PasswordAuthentication yes" in content:
                issues.append("Password auth enabled (use keys instead)")
            if "PermitRootLogin yes" in content:
                issues.append("Root login permitted")
        except PermissionError:
            pass

    # Check for authorized_keys
    auth_keys = Path.home() / ".ssh/authorized_keys"
    if auth_keys.exists():
        try:
            count = len(auth_keys.read_text().strip().splitlines())
            if count > 0:
                issues.append(f"{count} authorized SSH key(s)")
        except PermissionError:
            pass

    if issues:
        return {
            "name": "SSH Security",
            "status": "warn",
            "detail": "; ".join(issues),
            "fix_hint": "Review: disable SSH if unused, use key-based auth only",
        }
    return {"name": "SSH Security", "status": "pass", "detail": "SSH not running"}


# ── Actions ──────────────────────────────────────────────────────────────

def _scan_connections(json_out: bool = False):
    console.print(Panel("[bold]macmon security --connections[/] -- Live Connection Audit", border_style="red"))

    out, _, rc = run_cmd(["lsof", "+c", "0", "-i", "-n", "-P"], timeout=15)
    if rc != 0:
        console.print("[yellow]Could not scan connections. Try with sudo.[/]")
        return

    table = Table(title="Active Network Connections", border_style="blue")
    table.add_column("Process", width=18)
    table.add_column("PID", width=7, style="dim")
    table.add_column("User", width=10, style="dim")
    table.add_column("Connection", width=45)
    table.add_column("Risk", width=8)

    connections = []
    for line in out.splitlines()[1:]:
        parsed = _parse_lsof_line(line)
        if not parsed:
            continue
        process, pid, user, conn_info, state = parsed

        # Assess risk (whitelist applies only when no suspicious-port match)
        risk = "[green]LOW[/]"
        risk_level = "low"
        if _suspicious_port_hit(conn_info, state):
            risk = "[red]HIGH[/]"
            risk_level = "high"
        elif process.lower() in SAFE_LISTENERS:
            risk = "[green]SAFE[/]"
            risk_level = "safe"
        else:
            for tool in KNOWN_REMOTE_TOOLS:
                if tool in process.lower():
                    risk = "[yellow]MED[/]"
                    risk_level = "medium"
                    break

        full_conn = f"{conn_info} {state}".strip()
        table.add_row(process[:18], pid, user[:10], full_conn[:45], risk)
        connections.append({
            "process": process, "pid": pid, "user": user,
            "connection": full_conn, "risk": risk_level,
        })

    console.print(table)

    high_risk = [c for c in connections if c["risk"] == "high"]
    if high_risk:
        console.print(f"\n[red bold]{len(high_risk)} HIGH RISK connection(s) detected![/]")
        console.print("[dim]Use `macmon security --block-ip <IP>` to block or `macmon kill <PID>` to stop.[/]")

    if json_out:
        console.print_json(json.dumps(connections, default=str))


def _binary_untrusted(path: Path) -> bool:
    """True if a binary fails codesign verification or carries the quarantine xattr."""
    _, _, rc = run_cmd(["codesign", "-dv", str(path)], timeout=5)
    if rc != 0:
        return True
    out, _, rc = run_cmd(["xattr", "-p", "com.apple.quarantine", str(path)], timeout=5)
    return rc == 0 and bool(out.strip())


def _scan_malware(json_out: bool = False):
    console.print(Panel("[bold]macmon security --malware[/] -- Malware Scan", border_style="red"))

    findings = []

    # Check processes
    console.print("[cyan]Scanning processes...[/]")
    sus_procs = _find_suspicious_processes()
    for p in sus_procs:
        findings.append({"type": "process", "detail": p, "severity": "high"})

    # Check for crypto miners
    console.print("[cyan]Checking for crypto miners...[/]")
    procs = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(None)  # Prime the measurement
            procs.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(1)
    for p in procs:
        try:
            if p.cpu_percent(None) > 80:
                # High CPU could be a miner
                cmdline = p.cmdline()
                cmd_str = " ".join(cmdline).lower()
                if any(kw in cmd_str for kw in ["stratum", "xmrig", "mining", "monero", "cryptonight"]):
                    findings.append({
                        "type": "crypto_miner",
                        "detail": f"PID {p.pid}: {p.name()} -- possible crypto miner",
                        "severity": "critical",
                    })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Check for suspicious files in common locations
    console.print("[cyan]Scanning suspicious file locations...[/]")
    sus_paths = [
        Path("/tmp"),
        Path("/var/tmp"),
        Path.home() / ".hidden",
    ]
    for base in sus_paths:
        if not base.exists():
            continue
        checked = 0
        try:
            for f in base.iterdir():
                if checked >= 20:
                    break
                if f.is_file() and os.access(f, os.X_OK) and f.suffix not in (".sh", ".py", ".rb"):
                    checked += 1
                    # Only flag if unsigned or quarantined (avoids build-artifact noise)
                    if _binary_untrusted(f):
                        findings.append({
                            "type": "suspicious_binary",
                            "detail": f"Executable in {base}: {f.name} (unsigned or quarantined)",
                            "severity": "medium",
                        })
        except (OSError, PermissionError):
            continue

    # Check startup items
    console.print("[cyan]Scanning startup items...[/]")
    sus_launch = _find_suspicious_launch_items()
    for item in sus_launch:
        findings.append({"type": "startup", "detail": item, "severity": "medium"})

    # Display
    if not findings:
        console.print("[green bold]No malware indicators found![/]")
        return

    table = Table(title=f"Malware Scan Results ({len(findings)} findings)", border_style="red")
    table.add_column("Type", width=18)
    table.add_column("Severity", width=10)
    table.add_column("Details", width=50)

    for f in findings:
        sev_color = {"critical": "red bold", "high": "red", "medium": "yellow", "low": "dim"}.get(f["severity"], "white")
        table.add_row(f["type"], f"[{sev_color}]{f['severity'].upper()}[/]", f["detail"])

    console.print(table)

    if json_out:
        console.print_json(json.dumps(findings, default=str))


def _scan_remote_access(json_out: bool = False):
    console.print(Panel("[bold]macmon security --remote[/] -- Remote Access Audit", border_style="yellow"))

    results = []

    # Check running remote tools
    remote_procs = _find_remote_tools()
    for r in remote_procs:
        results.append({"type": "Running Tool", "detail": r, "risk": "medium"})

    # Check installed remote tools
    console.print("[cyan]Checking installed remote access apps...[/]")
    remote_apps = ["TeamViewer", "AnyDesk", "RustDesk", "ScreenConnect", "Splashtop", "LogMeIn", "RealVNC"]
    for app_name in remote_apps:
        app_path = Path(f"/Applications/{app_name}.app")
        if app_path.exists():
            results.append({"type": "Installed App", "detail": app_name, "risk": "low"})

    # Check SSH
    if _service_active("com.openssh.sshd", 22):
        results.append({"type": "Service", "detail": "SSH daemon running", "risk": "medium"})

    # Check Screen Sharing
    if _service_active("com.apple.screensharing", 5900):
        results.append({"type": "Service", "detail": "Screen Sharing enabled", "risk": "medium"})

    # Check VNC
    out, _, rc = run_cmd(["defaults", "read", "/Library/Preferences/com.apple.RemoteManagement", "VNCLegacyConnectionsEnabled"], timeout=5)
    if rc == 0 and out.strip() == "1":
        results.append({"type": "Service", "detail": "VNC legacy connections enabled", "risk": "high"})

    # Display
    if not results:
        console.print("[green bold]No remote access tools detected![/]")
        return

    table = Table(title="Remote Access Audit", border_style="yellow")
    table.add_column("Type", width=18)
    table.add_column("Risk", width=8)
    table.add_column("Details", width=45)

    for r in results:
        risk_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(r["risk"], "white")
        table.add_row(r["type"], f"[{risk_color}]{r['risk'].upper()}[/]", r["detail"])

    console.print(table)


def _show_firewall():
    console.print(Panel("[bold]macmon security --firewall[/] -- Firewall Status", border_style="cyan"))

    fw = _check_firewall()
    console.print(f"  Status: {fw['detail']}")

    # Show stealth mode
    if Path(SOCKETFILTERFW).exists():
        out, _, rc = run_cmd([SOCKETFILTERFW, "--getstealthmode"], timeout=5)
        low = out.lower() if rc == 0 else ""
        stealth = bool(low.strip()) and "disabled" not in low and ("enabled" in low or "state = 1" in low or " on" in low)
    else:
        out, _, rc = run_cmd(["defaults", "read", "/Library/Preferences/com.apple.alf", "stealthenabled"], timeout=5)
        stealth = out.strip() == "1" if rc == 0 else False
    console.print(f"  Stealth Mode: {'[green]Enabled[/]' if stealth else '[yellow]Disabled[/]'}")

    # Show app firewall rules
    out, _, rc = run_cmd([SOCKETFILTERFW, "--listapps"], timeout=10)
    if rc == 0:
        console.print("\n[bold]Application Rules:[/]")
        for line in out.splitlines():
            if "ALF" in line or ":" in line:
                if "ALLOW" in line.upper():
                    console.print(f"  [green]{line.strip()}[/]")
                elif "BLOCK" in line.upper() or "DENY" in line.upper():
                    console.print(f"  [red]{line.strip()}[/]")
                else:
                    console.print(f"  [dim]{line.strip()}[/]")

    console.print("\n[dim]Manage: System Settings > Network > Firewall > Options[/]")


def _valid_ip(ip: str) -> bool:
    """Validate an IP address or CIDR network (rejects pf rule injection)."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        pass
    try:
        ipaddress.ip_network(ip, strict=False)
        return True
    except ValueError:
        return False


def _rule_ips(line: str) -> set:
    """Extract the IP tokens from a pf rule line for exact matching."""
    ips = set()
    tokens = line.split()
    for i, tok in enumerate(tokens):
        if tok in ("from", "to") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if nxt != "any":
                ips.add(nxt)
    return ips


def _block_ip(ip: str):
    if not _valid_ip(ip):
        console.print(f"[red]Invalid IP address or network: {ip}[/]")
        return

    console.print(f"[red]Blocking IP: {ip}[/]")
    # Add block rules to macmon's dedicated pf anchor
    rule = f"block drop from {ip} to any\nblock drop from any to {ip}\n"
    anchor_file = Path.home() / ".macmon/blocked_ips.conf"
    anchor_file.parent.mkdir(parents=True, exist_ok=True)

    # Append rule (exact IP match, not substring)
    existing = anchor_file.read_text() if anchor_file.exists() else ""
    if any(ip in _rule_ips(l) for l in existing.splitlines()):
        console.print(f"[yellow]IP {ip} is already blocked.[/]")
        return

    anchor_file.write_text(existing + rule)

    # Load rules into the macmon anchor (never replaces the main ruleset)
    _, err, rc = run_cmd(
        ["pfctl", "-a", PF_ANCHOR, "-f", str(anchor_file)],
        sudo=True, timeout=10,
    )
    if rc == 0:
        console.print(f"[green]Blocked {ip} via pf firewall (anchor {PF_ANCHOR}).[/]")
        info, _, info_rc = run_cmd(["pfctl", "-s", "info"], sudo=True, timeout=5)
        if info_rc == 0 and "Status: Enabled" not in info:
            console.print("[yellow]pf is currently disabled -- the rule will not take effect until you run: sudo pfctl -E[/]")
        log_action("security_block_ip", ip)
    else:
        console.print(f"[yellow]Rule saved to {anchor_file}. Load manually: sudo pfctl -a {PF_ANCHOR} -f {anchor_file}[/]")
        console.print(f"[dim]Enable pf: sudo pfctl -E[/]")


def _unblock_ip(ip: str):
    anchor_file = Path.home() / ".macmon/blocked_ips.conf"
    if not anchor_file.exists():
        console.print("[dim]No blocked IPs.[/]")
        return

    lines = anchor_file.read_text().splitlines()
    new_lines = [l for l in lines if ip not in _rule_ips(l)]
    if len(new_lines) == len(lines):
        console.print(f"[yellow]IP {ip} is not in the block list.[/]")
        return
    content = "\n".join(new_lines).strip()
    anchor_file.write_text(content + "\n" if content else "")

    if content:
        run_cmd(["pfctl", "-a", PF_ANCHOR, "-f", str(anchor_file)], sudo=True, timeout=10)
    else:
        run_cmd(["pfctl", "-a", PF_ANCHOR, "-F", "rules"], sudo=True, timeout=10)
    console.print(f"[green]Unblocked {ip}.[/]")
    log_action("security_unblock_ip", ip)


def _quarantine_process(target: str):
    """Kill process + block its network access."""
    matches = []
    try:
        pid = int(target)
        matches.append(psutil.Process(pid))
    except psutil.NoSuchProcess:
        pass
    except ValueError:
        # Search by exact name (case-insensitive), never the current process
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["pid"] == os.getpid():
                    continue
                if (proc.info["name"] or "").lower() == target.lower():
                    matches.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if not matches:
        console.print(f"[yellow]Process '{target}' not found.[/]")
        return

    if len(matches) > 1:
        console.print(f"[yellow]{len(matches)} processes match '{target}':[/]")
        for proc in matches:
            try:
                exe = proc.exe()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                exe = "?"
            console.print(f"  [dim]PID {proc.pid}: {exe}[/]")

    for p in matches:
        _quarantine_one(p)


def _quarantine_one(p):
    try:
        name = p.name()
        pid = p.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    if not confirm_action(f"Quarantine {name} (PID {pid})? This will kill it and block its binary."):
        return

    # Get binary path
    try:
        exe = p.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        exe = ""

    # Kill
    try:
        p.terminate()
        time.sleep(1)
        if p.is_running():
            p.kill()
        console.print(f"[green]Killed {name} (PID {pid})[/]")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    # Add to firewall block list if possible
    if exe:
        _, err1, rc1 = run_cmd(
            [SOCKETFILTERFW, "--add", exe],
            sudo=True, timeout=5,
        )
        _, err2, rc2 = run_cmd(
            [SOCKETFILTERFW, "--blockapp", exe],
            sudo=True, timeout=5,
        )
        if rc1 == 0 and rc2 == 0:
            console.print(f"[green]Blocked {exe} in application firewall.[/]")
            console.print("[dim]Note: the macOS application firewall blocks INBOUND connections only.[/]")
        else:
            err = (err1 or err2 or "unknown error").strip()
            console.print(f"[red]Failed to block {exe} in application firewall: {err}[/]")

    log_action("security_quarantine", f"{name} (PID {pid})")


def _show_security_rules():
    console.print(Panel("[bold]macmon security --rules[/] -- Auto Security Rules", border_style="cyan"))

    rules = [
        {"rule": "Block suspicious ports", "ports": ", ".join(f"{p}" for p in list(SUSPICIOUS_PORTS.keys())[:8]), "action": "Alert + log", "status": "Active"},
        {"rule": "Detect remote access tools", "ports": "Any", "action": "Alert", "status": "Active"},
        {"rule": "Monitor crypto miners", "ports": "N/A", "action": "Alert (CPU>80%)", "status": "Active"},
        {"rule": "Scan suspicious binaries", "ports": "N/A", "action": "Alert on /tmp exec", "status": "Active"},
        {"rule": "Audit startup items", "ports": "N/A", "action": "Flag suspicious", "status": "Active"},
        {"rule": "Firewall status check", "ports": "N/A", "action": "Warn if disabled", "status": "Active"},
        {"rule": "SIP/Gatekeeper check", "ports": "N/A", "action": "Fail if disabled", "status": "Active"},
        {"rule": "FileVault check", "ports": "N/A", "action": "Fail if disabled", "status": "Active"},
    ]

    table = Table(title="Security Rules", border_style="cyan")
    table.add_column("Rule", width=30)
    table.add_column("Scope", width=25)
    table.add_column("Action", width=20)
    table.add_column("Status", width=8)

    for r in rules:
        table.add_row(r["rule"], r["ports"], r["action"], f"[green]{r['status']}[/]")

    console.print(table)

    # Show blocked IPs
    blocked_file = Path.home() / ".macmon/blocked_ips.conf"
    if blocked_file.exists():
        content = blocked_file.read_text().strip()
        if content:
            console.print("\n[bold]Blocked IPs:[/]")
            ips = set(re.findall(r'from ([\d\.]+)', content))
            for ip in ips:
                console.print(f"  [red]{ip}[/]")
