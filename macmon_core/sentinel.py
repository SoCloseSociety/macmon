"""MACMON-SENTINEL -- ultra-light always-on monitor + tactical console.

Presented to the owner as AegisForge ("fleet health, forged"): macmon is the
engine, AegisForge is the experience -- the banner, the notifications, the
theme, and the proactive layer in `aegis.py` that turns the sampler from a
threshold alarm into an anticipatory one (trend/slope over the history).

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
import shlex
import shutil
import signal
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

from . import aegis
from .aegis import AEGIS_THEME, BRAND, C, ENGINE, LEVEL_STYLE, TAGLINE
from .utils import MACMON_DIR, console
from .platform_compat import IS_MAC, IS_WINDOWS, OS_NAME, load_average, notify as _os_notify, require_os

REPO_DIR = Path(__file__).resolve().parent.parent
# venv interpreter path differs on Windows (.venv/Scripts/python.exe)
VENV_PY = REPO_DIR / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
MACMON_PY = REPO_DIR / "macmon.py"

METRICS = MACMON_DIR / "metrics.jsonl"
ALERTS_LOG = MACMON_DIR / "sentinel_alerts.log"
ASTATE = MACMON_DIR / "sentinel.state"
SEQ = MACMON_DIR / ".sentinel_seq"
CONF = MACMON_DIR / "sentinel.conf"
MONITOR_LABEL = "co.soclose.macmon.monitor"
WEEKLY_LABEL = "co.soclose.macmon.weekly"
MAX_BYTES = 5 * 1024 * 1024

# Notifier applet -- lets macOS notifications carry the AegisForge icon (a bare
# osascript notification always shows the generic Script Editor icon). The
# bundle path + identifier are kept from the macmon days on purpose: macOS keys
# the notification permission on the identifier, so rebranding the name and
# icon never re-triggers the "allow notifications?" prompt.
NOTIFIER_APP = MACMON_DIR / "MacmonSentinel.app"
NOTIFY_PAYLOAD = MACMON_DIR / ".notify_payload"
AEGIS_ICNS = REPO_DIR / "assets/aegisforge.icns"   # branded icon (preferred)
ICNS_SRC = REPO_DIR / "assets/macmon.icns"          # engine icon (fallback)
NOTIFIER_BRAND = "aegisforge-1"                     # bump to force a rebuild

# AegisForge palette (see aegis.C). The legacy names stay so every gauge,
# sparkline and verdict in this module picks the branded tokens up unchanged.
GREEN, AMBER, RED, DIM = C["ok"], C["amber"], C["critical"], C["dim"]
MINT, EMBER, SKY, TEXT = C["mint"], C["ember"], C["sky"], C["text"]

# Notification title prefix. Platform-neutral: macmon also runs on Windows/Linux,
# where a toast titled "Mac: ..." would make no sense.
ALERT_TITLE = BRAND

DEFAULTS = {
    "swap_used_gb": 6.0,
    "ram_pct": 92.0,
    "proc_cpu": 95.0,
    "rtt_ms": 400.0,
    "disk_free_gb": 15.0,
    "ai_fleet": 12,
    "ping_every": 5,          # ping only every Nth sample (spares a metered link)
    "ollama_gb": 2.0,         # warn when idle ollama models hold more than this
    "vm_gb": 4.0,             # warn when a Docker/Colima VM holds more than this
    # ── Auto-remediation (safe escalation on memory pressure) ──
    "auto_purge": False,      # LEVEL 1 (non-destructive): sudo -n purge inactive RAM
    "auto_unload_ollama": False,  # LEVEL 1 (non-destructive): unload IDLE ollama models
    "auto_trim_fleet": False, # LEVEL 2 (opt-in): close IDLE AI sessions when critical
    "fleet_keep": 4,          # always keep at least this many AI sessions
    "ram_critical": 90.0,     # remediation triggers above this RAM% ...
    "swap_critical_gb": 8.0,  # ... AND above this swap usage
    "idle_samples": 10,       # a session must be idle this many samples (~10 min) first
    # ── AegisForge proactive layer (detection + alerting: ON, notify-only) ──
    "trend_window": 10,       # samples used for slope / ETA (~10 min)
    "swap_trend_min_gb": 3.0, # swap trend alert: swap already at least this ...
    "swap_slope_gb_min": 0.1, # ... climbing at least this fast (GB per minute) ...
    "swap_eta_min": 20,       # ... and due to hit swap_critical_gb within this many minutes
    "ram_trend_min": 75.0,    # memory trend alert: RAM% floor ...
    "ram_slope_pct_min": 1.0, # ... slope floor (%/min) ...
    "ram_eta_min": 15,        # ... ETA to ram_critical (minutes)
    "load_factor": 2.0,       # load catastrophe: load1 > cores * factor ...
    "load_sustain": 3,        # ... for this many consecutive samples
    "swarm_window": 5,        # growth is measured against the sample this many samples back
    "swarm_headless_min": 8,  # headless-browser swarm: at least this many ...
    "swarm_headless_growth": 8,  # ... and grew by this many in the window ...
    "swarm_headless_max": 40, # ... or simply this many, whatever the trend
    "swarm_floor": 8,         # generic swarm: names with fewer instances are not even tracked
    "swarm_min": 24,          # generic swarm: at least this many instances ...
    "swarm_growth": 12,       # ... and grew by this many in the window
    "orphan_alert_min": 3,    # alert when this many PROVEN leaks are present
    # ── Auto-reap (opt-in): SIGTERM proven-leaked orphans -- see aegis.py invariants ──
    "auto_reap_orphans": False,  # LEVEL 2 (opt-in): reap ppid==1 + parent-proven-dead leaks
    "reap_idle_samples": 5,   # a leak must be idle (<1% CPU) this many samples (~5 min) first
    "reap_families": ["headless", "node", "python"],  # which leak families may be reaped
    "reap_max": 40,           # at most this many per cycle
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


def _icon_source():
    """The notifier icon: AegisForge when shipped, the macmon icon as fallback."""
    if AEGIS_ICNS.exists():
        return AEGIS_ICNS
    if ICNS_SRC.exists():
        return ICNS_SRC
    return None


def _notifier_branded() -> bool:
    """Was the installed notifier built by THIS brand version? (A stale
    macmon-era applet keeps working, it just shows the old name and icon.)"""
    try:
        return (NOTIFIER_APP / "Contents/Resources/.brand").read_text().strip() == NOTIFIER_BRAND
    except OSError:
        return False


def _build_notifier():
    """Compile a tiny AppleScript applet carrying the AegisForge icon so that
    notifications show the brand icon instead of the Script Editor icon."""
    icon = _icon_source()
    if icon is None:
        return False
    try:
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
        shutil.copy(icon, NOTIFIER_APP / "Contents/Resources/applet.icns")
        plist = str(NOTIFIER_APP / "Contents/Info.plist")

        def _plist_set(key, value, typ="string"):
            # osacompile applets lack some keys, so Set alone fails -> Add fallback
            r = subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Set :{key} {value}", plist], capture_output=True)
            if r.returncode != 0:
                subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Add :{key} {typ} {value}", plist], capture_output=True)

        _plist_set("CFBundleName", BRAND)
        _plist_set("CFBundleDisplayName", BRAND)
        _plist_set("CFBundleIconFile", "applet")
        _plist_set("CFBundleIdentifier", "com.macmon.app")  # kept: notification permission is keyed on it
        _plist_set("LSUIElement", "true", "bool")  # faceless helper, no Dock icon
        (NOTIFIER_APP / "Contents/Resources/.brand").write_text(NOTIFIER_BRAND)
        NOTIFIER_APP.touch()
        # Register so the icon/identity resolve immediately (no icon-cache lag)
        lsreg = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
        subprocess.run([lsreg, "-f", str(NOTIFIER_APP)], capture_output=True)
        return True
    except Exception:
        return False


def _notify(title: str, msg: str):
    # Non-macOS: use the platform notifier (PowerShell toast / notify-send).
    if not IS_MAC:
        _os_notify(title, msg)
        return
    # macOS: prefer the branded applet (macmon icon); fall back to osascript.
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
    # Ping flags differ per platform: BSD/macOS -t is timeout (secs), but on
    # Linux iputils -t is TTL (a TTL of 3 expires mid-route -> no time= line),
    # and Windows uses -n (count) / -w (timeout ms) instead of -c/-t.
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", "3000", "1.1.1.1"]
    elif IS_MAC:
        cmd = ["ping", "-c", "1", "-t", "3", "1.1.1.1"]
    else:  # Linux
        cmd = ["ping", "-c", "1", "-W", "3", "1.1.1.1"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=6).stdout
        # Matches "time=1.2 ms" (macOS/Linux), "time=1ms" and "time<1ms" (Windows).
        m = re.search(r"time[=<]([\d.]+)", out)
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
    # psutil per-process cpu_percent is 0..100*ncores -> normalize to system-wide 0..100% (no false alarms)
    ncpu = psutil.cpu_count() or 1
    return (best[0], best[1] / ncpu, best[2])


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


def _claude_sessions():
    """Live Claude Code sessions (VSCode extension), with current CPU and age."""
    out = []
    for p in psutil.process_iter(["pid", "cmdline", "cpu_percent", "memory_info", "create_time"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "anthropic.claude-code" in cmd or "/native-binary/claude" in cmd:
                out.append({
                    "pid": p.info["pid"],
                    "cpu": p.info["cpu_percent"] or 0.0,
                    "rss": (p.info["memory_info"].rss if p.info["memory_info"] else 0),
                    "start": p.info["create_time"] or 0,
                })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return out


def _update_idle_streaks(sessions, astate):
    """Track how many consecutive samples each session has been idle (<1% CPU).
    Self-prunes: only currently-alive PIDs are kept."""
    prev = astate.get("idle_streak", {})
    cur = {}
    for s in sessions:
        pid = str(s["pid"])
        cur[pid] = (prev.get(pid, 0) + 1) if s["cpu"] < 1.0 else 0
    astate["idle_streak"] = cur
    return cur


def _sentinel_keep(cfg) -> int:
    """fleet_keep clamped to >= 1. Keeping 0 sessions would protect nothing --
    the guard that spares the session you are using must never be config'd off."""
    try:
        return max(1, int(cfg.get("fleet_keep", 4)))
    except (TypeError, ValueError):
        return 4


def _sentinel_idle(cfg) -> int:
    """idle_samples clamped to >= 1. A value of 0 would make every session
    instantly eligible for trimming, disabling the busy-session protection."""
    try:
        return max(1, int(cfg.get("idle_samples", 10)))
    except (TypeError, ValueError):
        return 10


def _trim_fleet(sessions, streaks, keep, idle_samples, force=False):
    """Close idle sessions beyond `keep`. Protects the `keep` MOST RECENTLY
    ACTIVE sessions (lowest idle streak, newest as tie-break) -- so the session
    you are using is spared even if the sampler runs while you read output. Only
    closes sessions idle for idle_samples samples in a row (unless force).
    SIGTERM (graceful); transcripts persist and each is resumable with --resume."""
    if len(sessions) <= keep:
        return []
    ranked = sorted(sessions, key=lambda s: (streaks.get(str(s["pid"]), 0), -s["start"]))
    protected = {s["pid"] for s in ranked[:keep]}
    closed = []
    for s in sessions:
        if s["pid"] in protected:
            continue
        if force or streaks.get(str(s["pid"]), 0) >= idle_samples:
            try:
                os.kill(s["pid"], signal.SIGTERM)
                closed.append(s["pid"])
            except (ProcessLookupError, PermissionError):
                continue
    return closed


# ── Heavy background services (the usual hidden RAM hogs) ────────────────

# `ollama ps` prints sizes with decimal HumanBytes (1000-based, not 1024-based),
# and can emit any unit -- a missing unit here would silently drop the model from
# `models` so it would never be unloaded.
_SIZE_RE = r"([\d.]+)\s*(B|KB|MB|GB|TB)\b"
_SIZE_FACTOR_GB = {"B": 1e-9, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}


def _ollama_status() -> dict:
    """Loaded ollama models: {'gb': float, 'models': [names], 'busy': bool}.

    `busy` means a runner is actively inferring, so models must NOT be unloaded.
    """
    out = {"gb": 0.0, "models": [], "busy": False}
    try:
        r = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=8)
        if r.returncode != 0:
            return out
        for line in r.stdout.splitlines()[1:]:
            m = re.match(r"^(\S+)\s+\S+\s+" + _SIZE_RE, line)
            if m:
                out["models"].append(m.group(1))
                out["gb"] += float(m.group(2)) * _SIZE_FACTOR_GB[m.group(3).upper()]
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return out
    out["gb"] = round(out["gb"], 1)
    # A runner burning CPU is mid-inference -- treat as busy and never unload it.
    # Self-priming: psutil returns 0.0 on the FIRST cpu_percent read, so we must
    # prime + sample here rather than trust process_iter's unprimed value (which
    # would report "idle" always and let us unload a model mid-inference).
    runners = []
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "ollama" in cmd and "runner" in cmd:
                p.cpu_percent(None)      # prime
                runners.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if runners:
        time.sleep(0.2)                  # short window, only when ollama is up
        for p in runners:
            try:
                if p.cpu_percent(None) > 5:
                    out["busy"] = True
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    return out


def _vm_status() -> dict:
    """Virtual machine footprint: {'gb': float, 'owner': str}.

    com.apple.Virtualization.VirtualMachine is Apple's GENERIC VZ host, shared by
    UTM, Podman, Colima and Docker Desktop -- so we report the owning process name
    instead of guessing which tool is running.
    """
    gb = 0.0
    owners: list[tuple[float, str]] = []
    for p in psutil.process_iter(["name", "cmdline", "memory_info"]):
        try:
            cmd = " ".join(p.info["cmdline"] or [])
            if "Virtualization.VirtualMachine" in cmd or "com.docker.virtualization" in cmd:
                rss = (p.info["memory_info"].rss if p.info["memory_info"] else 0) / 1e9
                gb += rss
                owners.append((rss, (p.info["name"] or "?")[:24]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    # Attribute the alert to the heaviest VM process (best available hint).
    owner = max(owners)[1] if owners else ""
    return {"gb": round(gb, 1), "owner": owner}


def _unload_ollama(models: list[str]) -> list[str]:
    """Unload models from memory. Non-destructive: ollama reloads on demand."""
    done = []
    for m in models:
        try:
            r = subprocess.run(["ollama", "stop", m], capture_output=True, timeout=20)
            if r.returncode == 0:
                done.append(m)
        except subprocess.TimeoutExpired:
            continue  # one wedged model must not abandon the others
        except FileNotFoundError:
            break     # ollama is not installed at all -- nothing more to try
    return done


def _remediate(vm, sw, cfg, astate, now, sessions, streaks, oll):
    """Safe escalation when memory pressure is genuinely high. Always notifies
    what it did. Level 1 (purge) is non-destructive; level 2 (trim) is opt-in.

    `oll` is the ollama status already measured by the caller -- recomputing it
    here would cost a second `ollama ps` plus a full process scan on every
    critical sample.
    """
    critical = vm.percent > cfg["ram_critical"] and sw.used / 1e9 > cfg["swap_critical_gb"]
    if not critical:
        return []
    done = []
    # LEVEL 1a -- unload idle ollama models. Non-destructive: they reload on the
    # next request. Often the single biggest hidden hog (multi-GB on the GPU).
    if aegis._bool(cfg, "auto_unload_ollama") and now - astate.get("_ollama", 0) > 900:
        if oll["models"] and not oll["busy"] and oll["gb"] >= cfg["ollama_gb"]:
            # Stamp the cooldown on every ATTEMPT: a persistently failing
            # `ollama stop` must not be retried on every 60s sample.
            astate["_ollama"] = now
            freed = _unload_ollama(oll["models"])
            if freed:
                done.append(("auto_ollama",
                             f"Unloaded {oll['gb']:.1f} GB of ollama models ({', '.join(freed)}) -- they reload automatically on demand"))
    if IS_MAC and aegis._bool(cfg, "auto_purge") and now - astate.get("_purge", 0) > 1800:
        # Stamp the cooldown on every ATTEMPT (like the ollama branch): a
        # persistently failing purge must not be retried on every 60s sample.
        astate["_purge"] = now
        try:
            r = subprocess.run(["sudo", "-n", "purge"], capture_output=True, timeout=60)
            if r.returncode == 0:
                done.append(("auto_purge", f"Purged inactive RAM (RAM {vm.percent:.0f}%, swap {sw.used/1e9:.0f} GB)"))
        except (subprocess.TimeoutExpired, OSError):
            # A hung/broken purge must not crash the sampler -- ASTATE would
            # never be written and every cooldown would be lost (alert spam).
            pass
    if aegis._bool(cfg, "auto_trim_fleet") and now - astate.get("_trim", 0) > 900:
        closed = _trim_fleet(sessions, streaks, _sentinel_keep(cfg), _sentinel_idle(cfg))
        if closed:
            astate["_trim"] = now
            done.append(("auto_trim", f"Closed {len(closed)} idle AI session(s) (RAM critical) -- resumable"))
    for key, msg in done:
        _notify(f"{ALERT_TITLE} auto", msg)
    return done


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
    oll = _ollama_status()
    vmst = _vm_status()

    # ── AegisForge observation: one process-table pass (families, lineage,
    # proven leaks). Needs the persisted state, so it is read here.
    astate = _read_json(ASTATE, {})
    forge = aegis.observe(astate, cfg)

    rec = {
        "ts": now, "cpu": round(cpu, 1), "ram": round(vm.percent, 1),
        "swap_gb": round(sw.used / 1e9, 2), "load1": round(load_average()[0], 2),
        "disk_free_gb": round(du.free / 1e9, 1),
        "claude": fleet["claude"], "codex": fleet["codex"], "mcp": fleet["mcp"],
        "top": [tname, round(tcpu, 1), trss], "rtt": rtt,
        "ollama_gb": oll["gb"], "vm_gb": vmst["gb"],
    }
    rec.update(forge["record"])
    _rotate()
    with open(METRICS, "a") as f:
        f.write(json.dumps(rec) + "\n")

    fired = []
    fleet_total = fleet["claude"][0] + fleet["codex"][0]
    vm_owner = f" ({vmst['owner']})" if vmst.get("owner") else ""
    checks = [
        ("swap", sw.used / 1e9 > cfg["swap_used_gb"], f"{ALERT_TITLE}: high swap",
         f"Swap {sw.used/1e9:.1f} GB -- restart or close some apps.", 3600),
        ("ram", vm.percent > cfg["ram_pct"], f"{ALERT_TITLE}: memory pressure",
         f"RAM {vm.percent:.0f}% used.", 1800),
        ("proc", tcpu > cfg["proc_cpu"], f"{ALERT_TITLE}: runaway process",
         f"{tname} is at {tcpu:.0f}% CPU.", 900),
        ("net", rtt is not None and rtt > cfg["rtt_ms"], f"{ALERT_TITLE}: network saturated",
         f"Latency {rtt or 0:.0f} ms -- a transfer is monopolizing the link.", 1800),
        ("disk", du.free / 1e9 < cfg["disk_free_gb"], f"{ALERT_TITLE}: low disk",
         f"{du.free/1e9:.0f} GB free -- run 'macmon sentinel --force-clean'.", 3600),
        ("fleet", fleet_total > cfg["ai_fleet"], f"{ALERT_TITLE}: AI fleet",
         f"{fleet_total} AI sessions open (claude+codex).", 7200),
        # Hidden hogs: an idle ollama model or a virtual machine can silently
        # hold multiple GB. Notify only -- unloading is handled by remediation.
        ("ollama", oll["gb"] >= cfg["ollama_gb"] and not oll["busy"], f"{ALERT_TITLE}: ollama in memory",
         f"{oll['gb']:.1f} GB of ollama models loaded and idle ({', '.join(oll['models'][:2])}). "
         f"Unload with: macmon sentinel --unload-ollama", 3600),
        ("vm", vmst["gb"] >= cfg["vm_gb"], f"{ALERT_TITLE}: virtual machine",
         f"A virtual machine{vm_owner} is holding {vmst['gb']:.1f} GB. Stop it if you are not using it.", 7200),
    ]
    for key, cond, title, msg, cd in checks:
        if cond and now - astate.get(key, 0) >= cd:
            astate[key] = now
            _notify(title, msg)
            fired.append((key, msg))

    # Each stage below is fenced on its own: a bad history row or a psutil
    # hiccup must not abort the sample between the record write and the
    # ASTATE write -- that lost every cooldown just stamped (the alerts
    # re-fired every minute) and every idle streak. A failed stage is written
    # to the alerts log as an "error" line instead.

    # ── AegisForge anticipation: trend/slope over the recent history. Notify-only.
    try:
        window = aegis._int(cfg, "trend_window", 10, lo=3) + 2
        fired.extend(_fire_findings(aegis.analyze(_load_tail(window), cfg, now), astate, now))
    except Exception as e:
        fired.append(("error", f"anticipation skipped this sample: {e!r}"))

    # ── Auto-remediation (safe escalation on memory pressure) ──
    try:
        sessions = _claude_sessions()
        streaks = _update_idle_streaks(sessions, astate)
        fired.extend(_remediate(vm, sw, cfg, astate, now, sessions, streaks, oll))
    except Exception as e:
        fired.append(("error", f"remediation skipped this sample: {e!r}"))

    # ── Auto-reap (opt-in, default OFF): only lineage-proven leaks under PID 1,
    # idle, with no live or listening socket -- the invariants live in aegis.reap_leaks.
    try:
        for key, msg in aegis.reap_leaks(forge["leaks"], cfg, astate, now):
            _notify(f"{ALERT_TITLE} auto", msg)
            fired.append((key, msg))
    except Exception as e:
        fired.append(("error", f"auto-reap skipped this sample: {e!r}"))

    try:
        ASTATE.write_text(json.dumps(astate))
    except Exception:
        pass
    if fired:
        with open(ALERTS_LOG, "a") as f:
            for key, msg in fired:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}  {key}: {msg}\n")


def _fire_findings(findings, astate: dict, now: int) -> list:
    """Notify each proactive finding once per cooldown (state in astate)."""
    fired = []
    for f in findings:
        if now - astate.get(f.key, 0) >= f.cooldown:
            astate[f.key] = now
            _notify(f"{ALERT_TITLE}: {f.title}", f.msg)
            fired.append((f.key, f.msg))
    return fired


def _load_tail(n: int = 16) -> list:
    """The last n samples, reading only the tail of metrics.jsonl (the sampler
    must not re-read a 5 MB file every minute)."""
    if not METRICS.exists():
        return []
    try:
        with open(METRICS, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if size > 256 * 1024:
        lines = lines[1:]  # the first line is almost certainly cut mid-record
    rows = []
    for ln in lines[-n:]:
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows


def _tail_lines(path: Path, n: int, chunk: int = 64 * 1024) -> list[str]:
    """The last n non-empty lines of a text log, reading only its tail (the
    alerts log grows for months; readers on a UI thread must not slurp it)."""
    if n <= 0 or not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - chunk))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    if size > chunk:
        lines = lines[1:]  # the first line is almost certainly cut mid-record
    return [ln for ln in lines if ln.strip()][-n:]


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


_FLAG = {"swap_trend": "SWAP↑", "ram_trend": "RAM↑", "load": "LOAD", "leaks": "LEAKS", "swarm": "SWARM"}


def _verdict(latest, findings=()):
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
    seen = set()
    for f in findings:  # anticipatory flags (trajectory, not just level)
        base = f.key.split(":")[0]
        label = _FLAG.get(base, base.upper())
        if label not in seen:
            seen.add(label)
            flags.append((label, LEVEL_STYLE.get(f.level, AMBER)))
    if not flags:
        return Text("● OPERATIONAL -- all systems nominal", style=f"bold {MINT}")
    t = Text("● ALERT -- ", style=f"bold {RED}")
    for i, (f, col) in enumerate(flags):
        if i:
            t.append(" | ", style=DIM)
        t.append(f, style=f"bold {col}")
    return t


def _banner(extra: str = "") -> str:
    tail = f" | {extra}" if extra else ""
    return f"[bold {MINT}]{BRAND.upper()}[/]  [{DIM}]{TAGLINE} | engine {ENGINE} sentinel{tail}[/]"


def _forge_panel(rows, cfg, findings, now=None):
    """The anticipation readout: where each vital is HEADING, not just where it
    is -- slope, ETA to the critical line, sustained load, swarms and leaks."""
    t = aegis.trends(rows, cfg, now or time.time())
    if not t:
        return None
    fired = {f.key.split(":")[0] for f in findings}
    g = Table.grid(padding=(0, 2))

    def row(label, value, note, level):
        g.add_row(Text(label, style=f"bold {TEXT}"), Text(value, style=LEVEL_STYLE.get(level, DIM)), Text(note, style=DIM))

    def heading(slope, eta, crit, unit):
        if slope > 0 and eta is not None:
            return f"past the {crit:.0f}{unit} critical line" if eta <= 0 else f"-> {crit:.0f}{unit} in ~{max(1, round(eta))} min"
        return "stable" if abs(slope) < 1e-9 else "falling"

    s = t["swap"]
    row("SWAP", f"{s['gb']:.1f} GB  {s['slope']:+.2f} GB/min", heading(s["slope"], s["eta"], s["crit"], " GB"),
        "high" if "swap_trend" in fired else "medium" if s["slope"] > 0.05 and s["gb"] >= 1 else "ok")
    r = t["ram"]
    row("RAM", f"{r['pct']:.0f}%  {r['slope']:+.1f}%/min", heading(r["slope"], r["eta"], r["crit"], "%"),
        "high" if "ram_trend" in fired else "medium" if r["slope"] > 0.3 and r["pct"] >= 60 else "ok")
    ld = t["load"]
    row("LOAD", f"{ld['load1']:.1f} / {ld['ncpu']} cores  ({ld['ratio']:.1f}x)",
        f"{ld['sustained']} sample(s) above {ld['factor']:.0f}x" if ld["sustained"] else "nominal",
        "critical" if "load" in fired else "medium" if ld["ratio"] > 1.0 else "ok")
    h = t["swarm"].get("headless")
    w = aegis.growth_span(t, cfg)
    if h:
        row("SWARM", f"headless x{h['count']}  ({h['growth']:+d} / {w} min)", f"<- {t['spawn'] or 'unknown spawner'}",
            "high" if "swarm" in fired else "low")
    elif t["names"]:
        name, v = max(t["names"].items(), key=lambda kv: (kv[1]["growth"], kv[1]["count"]))
        row("SWARM", f"{name} x{v['count']}  ({v['growth']:+d} / {w} min)", "largest family",
            "high" if "swarm" in fired else "low")
    else:
        row("SWARM", "none", "no runaway family", "ok")
    lk = t["leaks"]
    row("LEAKS", f"{lk['leak']} proven  /  {lk['p1']} under PID 1",
        ("auto-reap ON (opt-in)" if aegis._bool(cfg, "auto_reap_orphans") else "auto-reap OFF -- notify-only")
        + (f"  {', '.join(lk['names'])}" if lk["names"] else ""),
        "medium" if "leaks" in fired else "low" if lk["leak"] else "ok")
    title = f"[bold {EMBER}]FORGE[/] [{DIM}]anticipation -- slope over {t['points']} pts / {t['span_min']:.0f} min[/]"
    return Panel(g, title=title, border_style=EMBER if fired else DIM, padding=(0, 1), style=f"on {C['surface']}")


def _snapshot_panel():
    rows = _load()
    cfg = _conf()
    latest = rows[-1] if rows else None
    if not latest:
        return Panel(Align.center(Text("Sentinel has no samples yet.\nInstall: macmon sentinel --install", style=AMBER)),
                     title=_banner(), border_style=AMBER, style=f"on {C['ground']}")
    findings = aegis.analyze(rows, cfg, time.time())

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

    label = f"bold {TEXT}"
    right = Table.grid(padding=(0, 1))
    right.add_row(Text("LOAD", style=label), Text(f"{latest['load1']:.2f}", style=GREEN if latest['load1'] < 8 else AMBER))
    right.add_row(Text("DISK", style=label), Text(f"{latest['disk_free_gb']:.0f}G free", style=GREEN if latest['disk_free_gb'] > 30 else AMBER))
    rtt = latest.get("rtt")
    rtt_col = DIM if rtt is None else (GREEN if rtt < 120 else AMBER if rtt < 300 else RED)
    right.add_row(Text("NET", style=label), Text(f"{rtt:.0f}ms" if rtt else "--", style=rtt_col))
    if rtt_h:
        right.add_row(Text("rtt", style=DIM), Text(_spark(rtt_h, 20, 500), style=DIM))
    right.add_row(Text("AI FLEET", style=label), Text(f"{ai_total} ({ai_gb:.1f}G)", style=AMBER if ai_total > 12 else GREEN))
    right.add_row(Text("  claude", style=DIM), Text(f"{fleet[0]}x {fleet[1]/1024:.1f}G", style=DIM))
    right.add_row(Text("  codex", style=DIM), Text(f"{codex[0]}x {codex[1]/1024:.1f}G", style=DIM))
    right.add_row(Text("  mcp", style=DIM), Text(f"{mcp[0]}x {mcp[1]/1024:.1f}G", style=DIM))
    # Hidden hogs
    og = latest.get("ollama_gb", 0) or 0
    vg = latest.get("vm_gb", 0) or 0
    if og:
        right.add_row(Text("OLLAMA", style=label), Text(f"{og:.1f}G loaded", style=AMBER if og >= 2 else DIM))
    if vg:
        right.add_row(Text("VM", style=label), Text(f"{vg:.1f}G", style=AMBER if vg >= 4 else DIM))

    top = latest["top"]
    verdict = _verdict(latest, findings)
    parts = [
        Columns([Panel(left, title=f"[{DIM}]VITALS[/]", border_style=DIM, padding=(0, 1), style=f"on {C['surface']}"),
                 Panel(right, title=f"[{DIM}]RECON[/]", border_style=DIM, padding=(0, 1), style=f"on {C['surface']}")],
                equal=True, expand=True),
    ]
    forge = _forge_panel(rows, cfg, findings)
    if forge is not None:
        parts.append(forge)
    parts += [
        Text(f"TOP CPU  {top[0]}  {top[1]:.0f}%  {top[2]}MB", style=DIM),
        Text(""),
        Align.center(verdict),
    ]
    title = _banner(f"sample {age_s} | {len(rows)} pts | {datetime.now():%H:%M:%S}")
    border = RED if "ALERT" in verdict.plain else MINT
    return Panel(Group(*parts), title=title, border_style=border, padding=(1, 2), style=f"on {C['ground']}")


def _tail_alerts(n):
    return _tail_lines(ALERTS_LOG, n)


def show_snapshot():
    console.print(_snapshot_panel())
    recent = _tail_alerts(3)
    if recent:
        console.print(Panel("\n".join(recent), title=f"[bold {AMBER}]LAST ALERTS[/]", border_style=AMBER, padding=(0, 1)))
    console.print(Text("  levers: macmon sentinel --watch | --status | --log | --force-purge | --force-clean | --reap-orphans | --pause/--resume", style=DIM))


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


def _scheduler_has(label: str) -> bool:
    """Is a scheduled task registered? Cross-platform."""
    try:
        if IS_MAC:
            out = subprocess.run(["launchctl", "list"], capture_output=True, text=True).stdout
            return label in out
        if IS_WINDOWS:
            r = subprocess.run(["schtasks", "/query", "/tn", _TASK_NAME], capture_output=True, text=True)
            return r.returncode == 0
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        return _TASK_NAME in out
    except FileNotFoundError:
        return False


def show_status():
    cfg = _conf()
    mon_active = _scheduler_has(MONITOR_LABEL)
    weekly_active = _scheduler_has(WEEKLY_LABEL) if IS_MAC else False
    t = Table.grid(padding=(0, 2))
    t.add_row("Platform", Text(OS_NAME, style=DIM))
    t.add_row("Sentinel (60s sampler)", Text("ACTIVE" if mon_active else "STOPPED", style=GREEN if mon_active else RED))
    t.add_row("Weekly health agent", Text("ACTIVE" if weekly_active else ("STOPPED" if IS_MAC else "macOS only"), style=GREEN if weekly_active else DIM))
    t.add_row("AegisForge detectors", Text("ON (notify-only): swap/RAM trend, load, swarm, leaks", style=MINT))
    unload, purge_on = aegis._bool(cfg, "auto_unload_ollama"), aegis._bool(cfg, "auto_purge")
    trim, reap_on = aegis._bool(cfg, "auto_trim_fleet"), aegis._bool(cfg, "auto_reap_orphans")
    t.add_row("Auto-unload ollama models", Text("ON" if unload else "OFF", style=GREEN if unload else DIM))
    t.add_row("Auto-purge (RAM)", Text("ON" if purge_on else "OFF (notify-only)", style=GREEN if purge_on else DIM))
    t.add_row("Auto-trim idle AI sessions", Text("ON" if trim else "OFF", style=AMBER if trim else DIM))
    t.add_row("Auto-reap leaked orphans", Text("ON (proven leaks only)" if reap_on else "OFF (notify-only)",
                                               style=EMBER if reap_on else DIM))
    t.add_row("Metrics collected", Text(f"{len(_load(100000))} samples", style=DIM))
    console.print(Panel(t, title=_banner("status"), border_style=DIM, style=f"on {C['ground']}"))


# ── Manual override + lifecycle ──────────────────────────────────────────

def _macmon(*args):
    py = str(VENV_PY) if os.path.exists(str(VENV_PY)) else sys.executable   # .venv may be absent
    try:
        subprocess.run([py, str(MACMON_PY), *args], cwd=str(REPO_DIR))
    except FileNotFoundError as e:
        console.print(f"[red]macmon is unavailable ({e})[/]")


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


_TASK_NAME = "macmon-sentinel"  # schtasks / cron identifier (Windows/Linux)
# Well-known homes of the `macmon` launcher when it is not on PATH: install.sh's
# wrapper, Homebrew, pipx / pip --user. (A Finder-launched .app has a minimal PATH.)
_MACMON_CANDIDATES = ("/usr/local/bin/macmon", "/opt/homebrew/bin/macmon",
                      str(Path.home() / ".local/bin/macmon"))


def find_macmon():
    """Path of the installed `macmon` launcher, or None."""
    exe = shutil.which("macmon")
    if exe:
        return exe
    for cand in _MACMON_CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _sample_cmd() -> list[str]:
    """argv the scheduler runs every 60s. A checkout uses its own venv +
    macmon.py (unchanged); otherwise the installed `macmon` launcher, else this
    interpreter on macmon.py. The frozen AegisForge.app has neither a venv nor
    macmon.py and its sys.executable IS the app, so there an empty list means
    "nothing runnable": the install must refuse rather than register a job
    that fails every minute while --resume reports success."""
    if VENV_PY.exists() and MACMON_PY.exists():
        return [str(VENV_PY), str(MACMON_PY), "sentinel", "--sample"]
    exe = find_macmon()
    if exe:
        return [exe, "sentinel", "--sample"]
    if not getattr(sys, "frozen", False) and MACMON_PY.exists():
        return [sys.executable, str(MACMON_PY), "sentinel", "--sample"]
    return []


def _schedule_install() -> tuple[bool, str]:
    """Register a per-minute sampler with the OS scheduler. Returns (ok, note)."""
    cmd = _sample_cmd()
    if not cmd:
        return (False, "no macmon to schedule -- install the CLI (install.sh or pip install -e .) "
                       "and run 'macmon sentinel --resume'")
    if IS_MAC:
        la_dir = Path.home() / "Library/LaunchAgents"
        la_dir.mkdir(parents=True, exist_ok=True)
        uid = os.getuid()
        plist_path = la_dir / f"{MONITOR_LABEL}.plist"
        plist_path.write_text(_plist(MONITOR_LABEL, cmd, 60))
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{MONITOR_LABEL}"], capture_output=True)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True, text=True)
        return (r.returncode == 0, r.stderr.strip())
    if IS_WINDOWS:
        tr = " ".join(f'"{c}"' for c in cmd)
        r = subprocess.run(["schtasks", "/create", "/tn", _TASK_NAME, "/tr", tr,
                            "/sc", "minute", "/mo", "1", "/f"], capture_output=True, text=True)
        return (r.returncode == 0, r.stderr.strip())
    # Linux: cron (per-minute)
    # Quote each arg -- an unquoted join breaks if the repo/home path has a space.
    line = "* * * * * " + " ".join(shlex.quote(c) for c in cmd) + f"  # {_TASK_NAME}\n"
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    cur = "\n".join(l for l in cur.splitlines() if _TASK_NAME not in l)
    new = (cur + "\n" + line).strip() + "\n"
    r = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
    return (r.returncode == 0, r.stderr.strip())


def _schedule_remove():
    if IS_MAC:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{MONITOR_LABEL}"], capture_output=True)
        (Path.home() / f"Library/LaunchAgents/{MONITOR_LABEL}.plist").unlink(missing_ok=True)
    elif IS_WINDOWS:
        subprocess.run(["schtasks", "/delete", "/tn", _TASK_NAME, "/f"], capture_output=True)
    else:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = "\n".join(l for l in cur.splitlines() if _TASK_NAME not in l).strip() + "\n"
        subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)


def install():
    ok, note = _schedule_install()
    if ok:
        console.print(f"[bold {MINT}]{BRAND} armed[/] [{DIM}]-- {TAGLINE}.[/] {ENGINE} sentinel sampling every 60s (~0.1% CPU) on {OS_NAME}.")
        if IS_MAC:
            icon = _build_notifier()
            console.print(f"[{DIM}]Notifications: {'branded ' + BRAND + ' icon' if icon else 'system icon'}.[/]")
        console.print(f"[{DIM}]Proactive detectors ON (notify-only): swap/RAM trend, load, process swarm, leaked orphans.[/]")
        console.print(f"[{DIM}]View anytime: macmon sentinel   |   Live: macmon sentinel --watch[/]")
        run_sample()
    else:
        console.print(f"[red]Install failed: {note or 'scheduler error'}[/]")
        console.print("[dim]You can still run 'macmon sentinel --sample' manually or via your own scheduler.[/]")


def uninstall():
    _schedule_remove()
    console.print("[yellow]Sentinel uninstalled.[/]")


def pause():
    _schedule_remove()
    console.print(Text("Sentinel PAUSED. Resume: macmon sentinel --resume", style=AMBER))


def resume():
    ok, note = _schedule_install()
    console.print(Text("Sentinel RESUMED (60s sampling)." if ok else f"Resume failed: {note}",
                       style=GREEN if ok else RED))


def force_clean():
    _macmon("clean", "--scan")
    if console.input("[bold]Proceed with clean? [y/N] [/]").strip().lower() == "y":
        _macmon("clean", "--all", "-y")


def _write_conf(updates: dict):
    cfg = {}
    try:
        cfg = json.loads(CONF.read_text())
    except Exception:
        pass
    cfg.update(updates)
    CONF.write_text(json.dumps(cfg, indent=2))


def _purge_nopasswd_ready() -> bool:
    """Is `purge` allowed without a password? Probes the sudoers GRANT only.

    `sudo -n -l <cmd>` answers the question without executing anything: running a
    real purge here would flush the whole filesystem buffer cache, stalling the
    machine for seconds and leaving every app with a cold cache -- just to ask.
    """
    if not IS_MAC:
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", "/usr/sbin/purge"], capture_output=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def enable_auto(aggressive: bool = False, reap: bool = False):
    """Turn on auto-remediation. Level 1 (purge, macOS) is non-destructive;
    --aggressive also enables closing idle AI sessions when memory is critical;
    --reap also enables reaping lineage-proven leaked orphans (see aegis.py)."""
    _write_conf({"auto_purge": True, "auto_unload_ollama": True, "auto_trim_fleet": bool(aggressive),
                 "auto_reap_orphans": bool(reap)})
    purge_line = ("purge inactive RAM on memory pressure -- non-destructive.\n" if IS_MAC
                  else f"(macOS only -- not applicable on {OS_NAME}).\n")
    label = f"bold {TEXT}"
    console.print(Panel(
        Text.assemble(
            ("Auto-remediation ENABLED\n\n", f"bold {MINT}"),
            ("Level 1a (auto_unload_ollama): ", label),
            ("unload IDLE ollama models when RAM is critical -- they reload on demand.\n", DIM),
            ("Level 1b (auto_purge): ", label), (purge_line, DIM),
            ("Level 2 (auto_trim_fleet): ", label),
            (f"{'ON -- closes IDLE AI sessions when RAM is critical (resumable).' if aggressive else 'OFF -- enable with --enable-auto --aggressive.'}\n", DIM),
            ("Level 2 (auto_reap_orphans): ", label),
            (f"{'ON -- SIGTERMs dev processes under PID 1 whose parent the sentinel WATCHED die, once idle 5 min with no live or listening socket. Never a workload with a live parent or an active/listening socket.' if reap else 'OFF -- enable with --enable-auto --reap.'}\n", DIM),
        ),
        title=_banner("auto-remediation"), border_style=MINT, style=f"on {C['ground']}"))
    if IS_MAC and not _purge_nopasswd_ready():
        console.print(Text("\nFor unattended purge, allow it without a password (one-time, run this):", style=AMBER))
        console.print(Text("  macmon sentinel --setup-purge", style="bold white"))
        console.print(Text("  (without it, auto_purge is skipped -- notifications still fire.)", style=DIM))
    elif IS_MAC:
        console.print(Text("Passwordless purge already configured -- auto_purge is fully unattended.", style=GREEN))


def disable_auto():
    _write_conf({"auto_purge": False, "auto_trim_fleet": False, "auto_unload_ollama": False, "auto_reap_orphans": False})
    console.print(Text("Auto-remediation DISABLED (notify-only). Proactive detectors keep alerting.", style=AMBER))


def manual_unload_ollama():
    """Unload ollama models now. Non-destructive: they reload on demand."""
    oll = _ollama_status()
    if not oll["models"]:
        console.print(Text("No ollama model is loaded.", style=DIM))
        return
    if oll["busy"]:
        console.print(Text("ollama is mid-inference -- refusing to unload. Retry when idle.", style=AMBER))
        return
    freed = _unload_ollama(oll["models"])
    if freed:
        console.print(Text(f"Unloaded {oll['gb']:.1f} GB: {', '.join(freed)}", style=GREEN))
        console.print(Text("They reload automatically on the next request.", style=DIM))
    else:
        console.print(Text("Could not unload (is ollama running?).", style=AMBER))


def setup_purge():
    """Allow `purge` without a password so auto_purge can run unattended.

    Validates the sudoers snippet with visudo BEFORE installing it -- a malformed
    sudoers file would break sudo system-wide.
    """
    m = require_os("macOS")
    if m:
        console.print(f"[yellow]{m}[/]")
        return
    # Derive the user from the UID, never from $USER/$LOGNAME: those are
    # attacker-controllable env vars, and a value such as
    #   "neo ALL=(ALL) NOPASSWD: ALL #"
    # would comment out the rest of the line and grant permanent full root --
    # visudo validates syntax, so it accepts that injection happily.
    # pwd.getpwuid(os.getuid()) reads the passwd database by real UID and cannot
    # be spoofed via $USER/$LOGNAME the way getpass.getuser() can. pwd is
    # Unix-only, imported here (past the macOS gate) so the module still imports
    # on Windows.
    try:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        user = ""
    if not user:
        console.print("[red]Cannot determine the current user.[/]")
        return
    if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
        console.print(f"[red]Refusing: unsafe username {user!r} -- cannot build a sudoers rule.[/]")
        return
    line = f"{user} ALL=(root) NOPASSWD: /usr/sbin/purge\n"
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False) as f:
        f.write(line)
        tmp = f.name
    os.chmod(tmp, 0o440)
    console.print("[cyan]Validating the sudoers snippet...[/]")
    ok = False
    try:
        # stdio is inherited so sudo can prompt for the password on the user's TTY
        if subprocess.run(["sudo", "visudo", "-cf", tmp]).returncode != 0:
            console.print("[red]Validation failed (wrong password or invalid syntax) -- aborting, nothing changed.[/]")
            return
        # `install` sets the mode atomically: no window where the file exists
        # world-readable/writable, and no separate chmod that could be missed.
        ok = subprocess.run(["sudo", "install", "-m", "0440", tmp,
                             "/etc/sudoers.d/macmon-purge"]).returncode == 0
    finally:
        # Always remove the temp copy: a Ctrl-C at the sudo prompt would
        # otherwise leave sudoers content sitting in TMPDIR forever.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
    if ok and _purge_nopasswd_ready():
        _write_conf({"auto_purge": True})
        console.print(Text("Passwordless purge configured -- auto_purge is now fully unattended.", style=GREEN))
    else:
        console.print(Text("Setup did not take effect. Remove with: sudo rm /etc/sudoers.d/macmon-purge", style=AMBER))


def manual_trim():
    """Close idle AI sessions now, on demand (keeps the configured minimum)."""
    cfg = _conf()
    for p in psutil.process_iter(["cpu_percent"]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.5)
    sessions = _claude_sessions()
    # Build streaks from the CPU just measured over the 0.5s priming window:
    # a busy session (>= 1% CPU) gets streak 0 -> below idle_samples=1, so
    # _trim_fleet can NEVER close it (and ranks it first for protection).
    # Idle sessions get a huge streak so they are eligible beyond `keep`.
    streaks = {str(s["pid"]): (0 if s["cpu"] >= 1.0 else 10**6) for s in sessions}
    closed = _trim_fleet(sessions, streaks, _sentinel_keep(cfg), 1)
    console.print(Text(f"Closed {len(closed)} idle AI session(s), kept {_sentinel_keep(cfg)}. Resumable via --resume.",
                       style=GREEN if closed else DIM))


def manual_reap():
    """Reap lineage-proven leaked orphans now, after listing them and asking.

    Same invariants as auto-reap (PID 1 + a parent the sentinel WATCHED die +
    never-touch set + no live connection + SIGTERM); the idle-streak wait is
    replaced by this explicit confirmation. The sampler owns sentinel.state, so
    nothing is persisted here."""
    cfg = _conf()
    astate = _read_json(ASTATE, {})
    for p in psutil.process_iter(["cpu_percent"]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.5)
    forge = aegis.observe(astate, cfg)
    leaks = forge["leaks"]
    if not leaks:
        console.print(Text(f"No proven leak. ({len(forge['cands'])} dev process(es) sit under PID 1, but a leak needs a "
                           f"parent the sentinel watched die -- see 'macmon sweep --orphans' for the heuristic sweep.)", style=DIM))
        return
    t = Table(title=f"Leaked orphans ({len(leaks)})", border_style=EMBER)
    for col, w in (("PID", 7), ("Name", 26), ("Family", 9), ("Proof", 30), ("State", 6)):
        t.add_column(col, width=w)
    for L in leaks:
        t.add_row(str(L["pid"]), L["name"][:26], L["family"], L["proof"][:30], "busy" if L["busy"] else "idle")
    console.print(t)
    idle = [L for L in leaks if not L["busy"]]
    if not idle:
        console.print(Text("All of them are busy right now -- refusing to touch a working process.", style=AMBER))
        return
    if console.input(f"[bold]SIGTERM {len(idle)} idle leaked orphan(s)? [y/N] [/]").strip().lower() != "y":
        return
    one_shot = {**cfg, "auto_reap_orphans": True, "reap_idle_samples": 1}
    astate["leak_streak"] = {str(L["pid"]): 1 for L in idle}
    astate.pop("_reap", None)
    done = aegis.reap_leaks(idle, one_shot, astate, time.time())
    if done:
        console.print(Text(done[0][1], style=MINT))
    else:
        console.print(Text("Nothing reaped (each one was busy, connected, or no longer under PID 1 at signal time).", style=DIM))


def test_notify():
    if not NOTIFIER_APP.exists() or not _notifier_branded():
        _build_notifier()
    _notify(ALERT_TITLE, f"{TAGLINE} -- test notification, the {BRAND} icon is active.")
    console.print(Text(f"Test notification sent (with the {BRAND} icon).", style=MINT))


def run_sentinel(sample=False, install_flag=False, uninstall_flag=False, watch=False,
                 status=False, log=False, pause_flag=False, resume_flag=False,
                 force_purge=False, force_clean_flag=False, force_focus=False,
                 test_notify_flag=False, enable_auto_flag=False, disable_auto_flag=False,
                 aggressive=False, trim=False, unload_ollama=False, setup_purge_flag=False,
                 reap=False, reap_orphans=False):
    console.push_theme(AEGIS_THEME)
    if sample:
        run_sample()
    elif test_notify_flag:
        test_notify()
    elif enable_auto_flag:
        enable_auto(aggressive=aggressive, reap=reap)
    elif disable_auto_flag:
        disable_auto()
    elif unload_ollama:
        manual_unload_ollama()
    elif setup_purge_flag:
        setup_purge()
    elif reap_orphans:
        manual_reap()
    elif trim:
        manual_trim()
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
        if console.input("[bold]Quit non-essential apps + purge RAM? [y/N] [/]").strip().lower() == "y":
            _macmon("focus")  # confirm before quitting the owner's apps (like force_clean)
    else:
        show_snapshot()
