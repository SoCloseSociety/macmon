"""MACMON-SENTINEL -- ultra-light always-on monitor + tactical console.

Design goals: precise, surgical, near-zero cost.
  - Collector: a single-shot sampler (macmon sentinel --sample) fired every 60s
    by a LaunchAgent. It measures in ~0.5s, appends one compact JSON line, fires
    threshold alerts (macOS notification + optional gentle remediation), exits.
    No resident process between samples => ~0.1% average CPU.
  - Console: a dense military-style readout (macmon sentinel) that reads the
    collected metrics. Zero cost when not open.
  - Manual override: force-purge / force-clean / force-focus / pause / resume.

State lives under ~/.macmon/ (metrics.jsonl, alerts, config).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
from rich.align import Align
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .utils import MACMON_DIR, console

REPO_DIR = Path(__file__).resolve().parent.parent
VENV_PY = REPO_DIR / ".venv/bin/python"
MACMON_PY = REPO_DIR / "macmon.py"

METRICS = MACMON_DIR / "metrics.jsonl"
ALERTS_LOG = MACMON_DIR / "sentinel_alerts.log"
ASTATE = MACMON_DIR / "sentinel.state"
SEQ = MACMON_DIR / ".sentinel_seq"
CONF = MACMON_DIR / "sentinel.conf"
MONITOR_LABEL = "co.soclose.macmon.monitor"
WEEKLY_LABEL = "co.soclose.macmon.weekly"
MAX_BYTES = 5 * 1024 * 1024

# Notifier applet -- lets macOS notifications carry the macmon icon (a bare
# osascript notification always shows the generic Script Editor icon).
NOTIFIER_APP = MACMON_DIR / "MacmonSentinel.app"
NOTIFY_PAYLOAD = MACMON_DIR / ".notify_payload"
ICNS_SRC = REPO_DIR / "assets/macmon.icns"

GREEN, AMBER, RED, DIM = "bright_green", "yellow", "bright_red", "grey50"

DEFAULTS = {
    "swap_used_gb": 6.0,
    "ram_pct": 92.0,
    "proc_cpu": 95.0,
    "rtt_ms": 400.0,
    "disk_free_gb": 15.0,
    "ai_fleet": 12,
    "auto_purge": False,   # gentle opt-in remediation (sudo -n purge)
    "ping_every": 5,       # ping only every Nth sample (spares a metered link)
}


# ── Config ───────────────────────────────────────────────────────────────

def _conf() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONF.read_text()))
    except Exception:
        pass
    return cfg


# ── Collector (single shot) ──────────────────────────────────────────────

_NOTIFIER_SCRIPT = '''on deliver()
    try
        set p to (POSIX path of (path to home folder)) & ".macmon/.notify_payload"
        set txt to (read (POSIX file p) as «class utf8»)
        set AppleScript's text item delimiters to linefeed
        set L to text items of txt
        set t to item 1 of L
        set AppleScript's text item delimiters to " "
        set m to (items 2 thru -1 of L) as text
        display notification m with title t
    end try
end deliver
on run
    deliver()
    quit
end run
on reopen
    deliver()
    quit
end reopen'''


def _build_notifier():
    """Compile a tiny AppleScript applet carrying the macmon icon so that
    notifications show the project icon instead of the Script Editor icon."""
    if not ICNS_SRC.exists():
        return False
    try:
        import shutil
        import tempfile
        if NOTIFIER_APP.exists():
            shutil.rmtree(NOTIFIER_APP)
        with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as f:
            f.write(_NOTIFIER_SCRIPT)
            src = f.name
        r = subprocess.run(["osacompile", "-o", str(NOTIFIER_APP), src], capture_output=True, timeout=30)
        os.unlink(src)
        if r.returncode != 0:
            return False
        shutil.copy(ICNS_SRC, NOTIFIER_APP / "Contents/Resources/applet.icns")
        plist = str(NOTIFIER_APP / "Contents/Info.plist")

        def _plist_set(key, value, typ="string"):
            # osacompile applets lack some keys, so Set alone fails -> Add fallback
            r = subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Set :{key} {value}", plist], capture_output=True)
            if r.returncode != 0:
                subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Add :{key} {typ} {value}", plist], capture_output=True)

        _plist_set("CFBundleName", "macmon")
        _plist_set("CFBundleIconFile", "applet")
        _plist_set("CFBundleIdentifier", "com.macmon.app")  # canonical macmon identity
        _plist_set("LSUIElement", "true", "bool")  # faceless helper, no Dock icon
        NOTIFIER_APP.touch()
        # Register so the icon/identity resolve immediately (no icon-cache lag)
        lsreg = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
        subprocess.run([lsreg, "-f", str(NOTIFIER_APP)], capture_output=True)
        return True
    except Exception:
        return False


def _notify(title: str, msg: str):
    # Prefer the branded applet (macmon icon); fall back to plain osascript.
    if NOTIFIER_APP.exists():
        try:
            t = " ".join(title.splitlines())
            m = " ".join(msg.splitlines())
            NOTIFY_PAYLOAD.write_text(f"{t}\n{m}")
            subprocess.run(["open", "-a", str(NOTIFIER_APP)], capture_output=True, timeout=5)
            return
        except Exception:
            pass
    t = title.replace("\\", "\\\\").replace('"', '\\"')
    m = msg.replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(["osascript", "-e", f'display notification "{m}" with title "{t}"'],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _ping_rtt():
    try:
        out = subprocess.run(["ping", "-c", "1", "-t", "3", "1.1.1.1"],
                             capture_output=True, text=True, timeout=6).stdout
        m = re.search(r"time=([\d.]+)", out)
        return round(float(m.group(1)), 1) if m else None
    except Exception:
        return None


def _ai_fleet():
    groups = {"claude": [0, 0], "codex": [0, 0], "mcp": [0, 0]}
    for p in psutil.process_iter(["cmdline", "memory_info"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            rss = (p.info["memory_info"].rss if p.info["memory_info"] else 0) // (1024 * 1024)
            if "anthropic.claude-code" in cmd or "/native-binary/claude" in cmd:
                groups["claude"][0] += 1; groups["claude"][1] += rss
            elif "openai.chatgpt" in cmd and "codex" in cmd:
                groups["codex"][0] += 1; groups["codex"][1] += rss
            elif "tradingview-mcp" in cmd or (cmd.endswith("server.js") and "mcp" in cmd.lower()):
                groups["mcp"][0] += 1; groups["mcp"][1] += rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return groups


def _top_proc():
    best = ("", 0.0, 0)
    for p in psutil.process_iter(["name", "cpu_percent", "memory_info"]):
        try:
            cpu = p.info["cpu_percent"] or 0.0
            if cpu > best[1]:
                rss = (p.info["memory_info"].rss if p.info["memory_info"] else 0) // (1024 * 1024)
                best = ((p.info["name"] or "?")[:24], cpu, rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return best


def _rotate():
    try:
        if METRICS.exists() and METRICS.stat().st_size > MAX_BYTES:
            METRICS.replace(METRICS.with_suffix(".jsonl.1"))
    except Exception:
        pass


def _read_json(path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def run_sample():
    """Take one measurement, record it, fire alerts. Called by the LaunchAgent."""
    MACMON_DIR.mkdir(exist_ok=True)
    cfg = _conf()
    now = int(time.time())
    seq = _read_json(SEQ, 0) + 1 if SEQ.exists() else 1
    try:
        SEQ.write_text(str(seq))
    except Exception:
        pass

    for p in psutil.process_iter(["cpu_percent"]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    cpu = psutil.cpu_percent(interval=0.5)

    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    du = psutil.disk_usage("/")
    fleet = _ai_fleet()
    tname, tcpu, trss = _top_proc()
    rtt = _ping_rtt() if seq % max(1, int(cfg["ping_every"])) == 0 else None

    rec = {
        "ts": now, "cpu": round(cpu, 1), "ram": round(vm.percent, 1),
        "swap_gb": round(sw.used / 1e9, 2), "load1": round(os.getloadavg()[0], 2),
        "disk_free_gb": round(du.free / 1e9, 1),
        "claude": fleet["claude"], "codex": fleet["codex"], "mcp": fleet["mcp"],
        "top": [tname, round(tcpu, 1), trss], "rtt": rtt,
    }
    _rotate()
    with open(METRICS, "a") as f:
        f.write(json.dumps(rec) + "\n")

    astate = _read_json(ASTATE, {})
    fired = []
    fleet_total = fleet["claude"][0] + fleet["codex"][0]
    checks = [
        ("swap", sw.used / 1e9 > cfg["swap_used_gb"], "Mac: swap eleve",
         f"Swap {sw.used/1e9:.1f} Go -- redemarrez ou fermez des apps.", 3600),
        ("ram", vm.percent > cfg["ram_pct"], "Mac: pression memoire",
         f"RAM {vm.percent:.0f}% utilisee.", 1800),
        ("proc", tcpu > cfg["proc_cpu"], "Mac: processus emballe",
         f"{tname} a {tcpu:.0f}% CPU.", 900),
        ("net", rtt is not None and rtt > cfg["rtt_ms"], "Mac: reseau sature",
         f"Latence {rtt or 0:.0f} ms -- un transfert monopolise le lien.", 1800),
        ("disk", du.free / 1e9 < cfg["disk_free_gb"], "Mac: disque faible",
         f"{du.free/1e9:.0f} Go libres -- lancez 'macmon sentinel --force-clean'.", 3600),
        ("fleet", fleet_total > cfg["ai_fleet"], "Mac: flotte IA",
         f"{fleet_total} sessions IA ouvertes (claude+codex).", 7200),
    ]
    for key, cond, title, msg, cd in checks:
        if cond and now - astate.get(key, 0) >= cd:
            astate[key] = now
            _notify(title, msg)
            fired.append((key, msg))

    if cfg.get("auto_purge") and vm.percent > 95 and sw.used / 1e9 > cfg["swap_used_gb"]:
        if now - astate.get("_purge", 0) > 3600:
            astate["_purge"] = now
            subprocess.run(["sudo", "-n", "purge"], capture_output=True, timeout=60)

    try:
        ASTATE.write_text(json.dumps(astate))
    except Exception:
        pass
    if fired:
        with open(ALERTS_LOG, "a") as f:
            for key, msg in fired:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}  {key}: {msg}\n")


# ── Tactical console ─────────────────────────────────────────────────────

def _load(n=120):
    if not METRICS.exists():
        return []
    rows = []
    try:
        for ln in METRICS.read_text().splitlines()[-n:]:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    except Exception:
        pass
    return rows


def _spark(vals, lo=0, hi=100):
    blocks = "▁▂▃▄▅▆▇█"
    out = []
    for v in vals:
        if v is None:
            out.append(" "); continue
        f = max(0.0, min(1.0, (v - lo) / (hi - lo) if hi > lo else 0))
        out.append(blocks[int(f * (len(blocks) - 1))])
    return "".join(out)


def _gauge(pct, width=22):
    pct = max(0.0, min(100.0, pct))
    fill = int(pct / 100 * width)
    color = GREEN if pct < 70 else AMBER if pct < 88 else RED
    bar = Text()
    bar.append("█" * fill, style=color)
    bar.append("░" * (width - fill), style=DIM)
    return bar


def _stat(label, value, pct, warn=70, crit=88):
    col = GREEN if pct < warn else AMBER if pct < crit else RED
    t = Text()
    t.append(f"{label:<7}", style="bold white")
    t.append_text(_gauge(pct))
    t.append(f" {value:>10}", style=col)
    return t


def _verdict(latest):
    if not latest:
        return Text("NO DATA -- sentinel not yet reporting", style=RED)
    flags = []
    if latest["swap_gb"] > 6: flags.append(("SWAP", RED))
    if latest["ram"] > 90: flags.append(("RAM", RED))
    elif latest["ram"] > 80: flags.append(("RAM", AMBER))
    if latest["top"][1] > 90: flags.append((f"PROC:{latest['top'][0]}", RED))
    if latest.get("rtt") and latest["rtt"] > 300: flags.append(("NET", RED))
    if latest["disk_free_gb"] < 15: flags.append(("DISK", RED))
    fleet = latest["claude"][0] + latest["codex"][0]
    if fleet > 12: flags.append((f"AI:{fleet}", AMBER))
    if not flags:
        return Text("● OPERATIONAL -- all systems nominal", style=f"bold {GREEN}")
    t = Text("● ALERT -- ", style=f"bold {RED}")
    for i, (f, col) in enumerate(flags):
        if i:
            t.append(" | ", style=DIM)
        t.append(f, style=f"bold {col}")
    return t


def _snapshot_panel():
    rows = _load()
    latest = rows[-1] if rows else None
    if not latest:
        return Panel(Align.center(Text("Sentinel has no samples yet.\nInstall: macmon sentinel --install", style=AMBER)),
                     title="[bold]MACMON-SENTINEL[/]", border_style=AMBER)

    cpu_h = [r["cpu"] for r in rows[-48:]]
    ram_h = [r["ram"] for r in rows[-48:]]
    swap_h = [r["swap_gb"] for r in rows[-48:]]
    rtt_h = [r.get("rtt") for r in rows[-48:] if r.get("rtt") is not None]

    age = int(time.time() - latest["ts"])
    age_s = f"{age}s ago" if age < 120 else f"{age // 60}m ago"

    left = Table.grid(padding=(0, 1))
    left.add_row(_stat("CPU", f"{latest['cpu']:.0f}%", latest["cpu"]))
    left.add_row(Text(f"        {_spark(cpu_h)}", style=DIM))
    left.add_row(_stat("RAM", f"{latest['ram']:.0f}%", latest["ram"]))
    left.add_row(Text(f"        {_spark(ram_h)}", style=DIM))
    sw_pct = min(100, latest["swap_gb"] / 12 * 100)
    left.add_row(_stat("SWAP", f"{latest['swap_gb']:.1f}G", sw_pct, warn=25, crit=50))
    left.add_row(Text(f"        {_spark(swap_h, 0, 12)}", style=DIM))

    fleet, codex, mcp = latest["claude"], latest["codex"], latest["mcp"]
    ai_total = fleet[0] + codex[0]
    ai_gb = (fleet[1] + codex[1] + mcp[1]) / 1024

    right = Table.grid(padding=(0, 1))
    right.add_row(Text("LOAD", style="bold white"), Text(f"{latest['load1']:.2f}", style=GREEN if latest['load1'] < 8 else AMBER))
    right.add_row(Text("DISK", style="bold white"), Text(f"{latest['disk_free_gb']:.0f}G free", style=GREEN if latest['disk_free_gb'] > 30 else AMBER))
    rtt = latest.get("rtt")
    rtt_col = DIM if rtt is None else (GREEN if rtt < 120 else AMBER if rtt < 300 else RED)
    right.add_row(Text("NET", style="bold white"), Text(f"{rtt:.0f}ms" if rtt else "--", style=rtt_col))
    if rtt_h:
        right.add_row(Text("rtt", style=DIM), Text(_spark(rtt_h, 20, 500), style=DIM))
    right.add_row(Text("AI FLEET", style="bold white"), Text(f"{ai_total} ({ai_gb:.1f}G)", style=AMBER if ai_total > 12 else GREEN))
    right.add_row(Text("  claude", style=DIM), Text(f"{fleet[0]}x {fleet[1]/1024:.1f}G", style=DIM))
    right.add_row(Text("  codex", style=DIM), Text(f"{codex[0]}x {codex[1]/1024:.1f}G", style=DIM))
    right.add_row(Text("  mcp", style=DIM), Text(f"{mcp[0]}x {mcp[1]/1024:.1f}G", style=DIM))

    top = latest["top"]
    body = Group(
        Columns([Panel(left, title="VITALS", border_style=DIM, padding=(0, 1)),
                 Panel(right, title="RECON", border_style=DIM, padding=(0, 1))], equal=True, expand=True),
        Text(f"TOP CPU  {top[0]}  {top[1]:.0f}%  {top[2]}MB", style=DIM),
        Text(""),
        Align.center(_verdict(latest)),
    )
    title = f"[bold]MACMON-SENTINEL[/]  [dim]sample {age_s} | {len(rows)} pts | {datetime.now():%H:%M:%S}[/]"
    border = RED if "ALERT" in _verdict(latest).plain else GREEN
    return Panel(body, title=title, border_style=border, padding=(1, 2))


def _tail_alerts(n):
    if not ALERTS_LOG.exists():
        return []
    try:
        return ALERTS_LOG.read_text().splitlines()[-n:]
    except Exception:
        return []


def show_snapshot():
    console.print(_snapshot_panel())
    recent = _tail_alerts(3)
    if recent:
        console.print(Panel("\n".join(recent), title="[bold]LAST ALERTS[/]", border_style=AMBER, padding=(0, 1)))
    console.print(Text("  levers: macmon sentinel --watch | --status | --force-purge | --force-clean | --pause/--resume", style=DIM))


def show_watch(interval=3):
    from rich.live import Live
    if not sys.stdin.isatty():
        console.print("[yellow]--watch requires a TTY[/]")
        return
    try:
        with Live(_snapshot_panel(), console=console, refresh_per_second=2, screen=True) as live:
            while True:
                time.sleep(interval)
                live.update(_snapshot_panel())
    except KeyboardInterrupt:
        pass


def show_log(n=20):
    lines = _tail_alerts(n)
    console.print(Panel("\n".join(lines) if lines else "No alerts recorded.",
                        title=f"[bold]ALERT LOG (last {n})[/]", border_style=AMBER))


def show_status():
    out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
    cfg = _conf()
    t = Table.grid(padding=(0, 2))
    t.add_row("Sentinel (60s sampler)", Text("ACTIVE" if MONITOR_LABEL in out else "STOPPED", style=GREEN if MONITOR_LABEL in out else RED))
    t.add_row("Weekly health agent", Text("ACTIVE" if WEEKLY_LABEL in out else "STOPPED", style=GREEN if WEEKLY_LABEL in out else RED))
    t.add_row("Auto-purge remediation", Text("ON" if cfg.get("auto_purge") else "OFF (notify-only)", style=AMBER if cfg.get("auto_purge") else DIM))
    t.add_row("Metrics collected", Text(f"{len(_load(100000))} samples", style=DIM))
    console.print(Panel(t, title="[bold]SENTINEL STATUS[/]", border_style=DIM))


# ── Manual override + lifecycle ──────────────────────────────────────────

def _macmon(*args):
    subprocess.run([str(VENV_PY), str(MACMON_PY), *args], cwd=str(REPO_DIR))


def _plist(label: str, program_args: list[str], interval: int) -> str:
    args = "\n".join(f"        <string>{a}</string>" for a in program_args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>StartInterval</key><integer>{interval}</integer>
    <key>RunAtLoad</key><true/>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
    <key>Nice</key><integer>10</integer>
</dict>
</plist>
"""


def install():
    la_dir = Path.home() / "Library/LaunchAgents"
    la_dir.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    plist_path = la_dir / f"{MONITOR_LABEL}.plist"
    plist_path.write_text(_plist(MONITOR_LABEL,
                                 [str(VENV_PY), str(MACMON_PY), "sentinel", "--sample"], 60))
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MONITOR_LABEL}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True, text=True)
    if r.returncode == 0:
        console.print(f"[green]MACMON-SENTINEL armed[/] -- sampling every 60s (~0.1% CPU).")
        icon = _build_notifier()
        console.print(f"[dim]Notifications: {'branded macmon icon' if icon else 'system icon'}.[/]")
        console.print(f"[dim]View anytime: macmon sentinel   |   Live: macmon sentinel --watch[/]")
        run_sample()
    else:
        console.print(f"[red]Install failed: {r.stderr.strip()}[/]")


def uninstall():
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MONITOR_LABEL}"], capture_output=True)
    p = Path.home() / f"Library/LaunchAgents/{MONITOR_LABEL}.plist"
    p.unlink(missing_ok=True)
    console.print("[yellow]Sentinel uninstalled.[/]")


def pause():
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{MONITOR_LABEL}"], capture_output=True)
    console.print(Text("Sentinel PAUSED. Resume: macmon sentinel --resume", style=AMBER))


def resume():
    plist = Path.home() / f"Library/LaunchAgents/{MONITOR_LABEL}.plist"
    if not plist.exists():
        install(); return
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True)
    console.print(Text("Sentinel RESUMED (60s sampling).", style=GREEN))


def force_clean():
    _macmon("clean", "--scan")
    if console.input("[bold]Proceed with clean? [y/N] [/]").strip().lower() == "y":
        _macmon("clean", "--all", "-y")


def test_notify():
    if not NOTIFIER_APP.exists():
        _build_notifier()
    _notify("macmon", "Notification de test -- icone macmon active.")
    console.print(Text("Notification de test envoyee (avec l'icone macmon).", style=GREEN))


def run_sentinel(sample=False, install_flag=False, uninstall_flag=False, watch=False,
                 status=False, log=False, pause_flag=False, resume_flag=False,
                 force_purge=False, force_clean_flag=False, force_focus=False,
                 test_notify_flag=False):
    if sample:
        run_sample()
    elif test_notify_flag:
        test_notify()
    elif install_flag:
        install()
    elif uninstall_flag:
        uninstall()
    elif watch:
        show_watch()
    elif status:
        show_status()
    elif log:
        show_log()
    elif pause_flag:
        pause()
    elif resume_flag:
        resume()
    elif force_purge:
        _macmon("purge")
    elif force_clean_flag:
        force_clean()
    elif force_focus:
        _macmon("focus")
    else:
        show_snapshot()
