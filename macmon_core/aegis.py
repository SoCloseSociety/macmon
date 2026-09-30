"""AegisForge -- the proactive, anticipatory layer on top of the macmon sentinel.

macmon is the engine (the `macmon` CLI, the `macmon_core` package, the 60s
single-shot sampler). AegisForge is the experience forged on top of it: the
brand the owner sees (console banner, notifications, theme) and the detectors
that make the sentinel self-triggering, so nobody has to come and ask
"fix my mac" -- the machine reports its own trajectory before it saturates.

Detectors (all read the metrics.jsonl history, so they alert on TREND/slope,
not only on the current value):
  - swap saturation + trend      "swap climbing, ~X GB, hits critical in ~Y min"
  - memory-pressure trend        same, on RAM%
  - load-average catastrophe     load >> logical cores, sustained N samples,
                                 names the top CPU offenders
  - process-swarm runaway        a family (headless browsers) or any executable
                                 whose instance count climbs fast; names the
                                 spawner (the "shoot.mjs spawned 24 -> 59 Chrome"
                                 pattern)
  - orphan/leak accumulation     dev processes reparented to launchd whose
                                 parent is PROVEN dead, counted + trended

SAFETY INVARIANTS (each is locked by tests/test_aegis.py):
  1. Detection + alerting are ON by default and NOTIFY-ONLY. The single
     auto-action this module can take -- reaping leaked orphans -- is opt-in
     (`auto_reap_orphans`, default False).
  2. A process with a LIVE parent (ppid != 1) is never signalled. The swarm
     detector has no kill path at all: it alerts, and it never touches the
     spawner or its children.
  3. "Dead parent" means PROVEN by lineage: an earlier sample saw the process
     with a live parent X (ppid == X != 1) and a later sample sees ppid == 1
     with X gone (exited, zombie, or its PID reused by a newer process). A dead
     process-GROUP leader is deliberately NOT accepted as proof: on macOS every
     launchd/brew-services job and the Android emulator look exactly like that.
  4. Never-touch set, checked before anything else: PID <= 1, macmon's own
     process and parent shell, system-critical names (the hardened
     `_is_protected_target` set), other users, IDEs (VSCode/Cursor/Zed/Xcode),
     Claude/codex/MCP agent sessions, ollama and local LLM runtimes,
     Sentinel/NeoBot/fleet processes (WireGuard, sshd), VMs and containers,
     terminals/shells/multiplexers, the user's apps under /Applications and
     anything under /System, /usr/libexec, /usr/sbin.
  5. Before a proven leak is reaped it must also be idle (< 1% CPU, itself and
     its descendants) for `reap_idle_samples` consecutive samples, hold no live
     socket -- no ESTABLISHED connection, and for the node/python families no
     LISTEN or bound unix socket either: an idle backgrounded dev server is
     still in service (unknown => skip) -- still be reparented to PID 1 at
     signal time, and the signal is SIGTERM -- never SIGKILL. In one line:
     never a workload with a live parent or an active/listening socket.
"""
import os
import signal
import socket
from collections import Counter
from dataclasses import dataclass

import psutil
from rich.theme import Theme

from .processes import _is_protected_target
from .utils import categorize_process

# ── Brand ────────────────────────────────────────────────────────────────

BRAND = "AegisForge"
TAGLINE = "fleet health, forged"
ENGINE = "macmon"

# Palette tokens (user-facing only -- the engine keeps its name).
C = {
    "ground": "#070A0F",    # page background
    "surface": "#10171F",   # panel background
    "text": "#EEF3F8",
    "dim": "#8393A6",
    "mint": "#34E5A0",      # ok / dry-run / primary
    "ember": "#FF7A1A",     # action / high / "forge at work"
    "amber": "#FBBF24",     # warn / medium
    "sky": "#7DD3FC",       # low
    "critical": "#FB7185",
    "ok": "#34D399",        # ok-green
}

AEGIS_THEME = Theme({
    "aegis.ground": f"on {C['ground']}",
    "aegis.surface": f"on {C['surface']}",
    "aegis.text": C["text"],
    "aegis.dim": C["dim"],
    "aegis.mint": C["mint"],
    "aegis.ember": C["ember"],
    "aegis.amber": C["amber"],
    "aegis.sky": C["sky"],
    "aegis.critical": C["critical"],
    "aegis.ok": C["ok"],
    # semantic aliases
    "aegis.primary": f"bold {C['mint']}",
    "aegis.dryrun": C["mint"],
    "aegis.action": f"bold {C['ember']}",
    "aegis.high": C["ember"],
    "aegis.warn": C["amber"],
    "aegis.medium": C["amber"],
    "aegis.low": C["sky"],
    "aegis.title": f"bold {C['text']}",
})

LEVEL_STYLE = {
    "critical": C["critical"], "high": C["ember"], "medium": C["amber"],
    "low": C["sky"], "ok": C["ok"],
}


# ── Config helpers (the conf file is user-edited JSON: never trust a type) ─

def _num(cfg: dict, key: str, default: float) -> float:
    try:
        return float(cfg.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _int(cfg: dict, key: str, default: int, lo: int = 0) -> int:
    try:
        return max(lo, int(cfg.get(key, default)))
    except (TypeError, ValueError):
        return max(lo, default)


_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off", ""}


def _bool(cfg: dict, key: str, default: bool = False) -> bool:
    """A flag that may have been hand-typed into the conf. Raw truthiness would
    turn `"auto_reap_orphans": "false"` into ON -- so the false words are
    False, only the usual true words are True, and anything unrecognised is
    the default (every auto_* flag defaults to off)."""
    v = cfg.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        w = v.strip().lower()
        if w in _TRUE_WORDS:
            return True
        if w in _FALSE_WORDS:
            return False
    return bool(default)


# Row-field readers: metrics.jsonl is append-only and written by every sampler
# version there ever was, so a field may be missing, null or the wrong shape.
# A bad field degrades to "no data", never to a crash (a crash in the analyze
# stage used to abort the sample before its state was persisted).

def _as_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v) if v is not None else int(default)
    except (TypeError, ValueError):
        return int(default)


def _ts(r: dict, default: float) -> float:
    ts = r.get("ts")
    return float(ts) if isinstance(ts, (int, float)) else float(default)


def _pair(v) -> list:
    """[under-PID-1, proven-leaks] from a record's 'orph'; [0, 0] when malformed."""
    try:
        return [int(v[0]), int(v[1])]
    except (TypeError, ValueError, IndexError, KeyError):
        return [0, 0]


def _counts(v) -> dict:
    """{name: int} from a record's 'fam' / 'pc'; junk entries dropped."""
    if not isinstance(v, dict):
        return {}
    return {str(k): int(c) for k, c in v.items() if isinstance(c, (int, float))}


def _tuples(v, n: int) -> list:
    """Well-formed [name, num, ...] entries of length n from a top-N list."""
    out = []
    for e in (v if isinstance(v, (list, tuple)) else []):
        if (isinstance(e, (list, tuple)) and len(e) == n and isinstance(e[0], str)
                and all(isinstance(x, (int, float)) for x in e[1:])):
            out.append(list(e))
    return out


def _strs(v) -> list:
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else []


# ── Trend / slope math (anticipation) ────────────────────────────────────

def slope_per_min(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of value per MINUTE over (unix_ts, value) points.

    Needs at least 3 points and some spread in time; otherwise 0.0 (no trend
    claim is ever made from too little evidence)."""
    pts = [(float(t), float(v)) for t, v in points if isinstance(t, (int, float)) and isinstance(v, (int, float))]
    if len(pts) < 3:
        return 0.0
    t0 = pts[0][0]
    xs = [(t - t0) / 60.0 for t, _ in pts]
    ys = [v for _, v in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return 0.0
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / var


def eta_minutes(current: float, target: float, slope: float):
    """Minutes until `current` reaches `target` at `slope` per minute.

    0.0 when already at/over the target, None when not climbing."""
    if current >= target:
        return 0.0
    if slope <= 0:
        return None
    return (target - current) / slope


def recent_rows(rows: list[dict], n: int, now: float) -> list[dict]:
    """The last `n` samples, dropping any older than the window should span
    (a sleep/pause gap must not be read as a slow trend)."""
    max_age = n * 60 * 1.5 + 60
    out = [r for r in rows[-n:] if isinstance(r.get("ts"), (int, float)) and now - r["ts"] <= max_age]
    return out


# ── Process table (one psutil pass; everything downstream is table-based) ─

_HAS_TTY = hasattr(psutil.Process, "terminal")
_ATTRS = ["pid", "ppid", "name", "cmdline", "exe", "create_time", "cpu_percent",
          "memory_info", "username", "status"] + (["terminal"] if _HAS_TTY else [])


def _current_user() -> str:
    try:
        return psutil.Process().username()
    except Exception:
        return ""


_ME = _current_user()


def scan() -> list[dict]:
    """One pass over the process table -> plain dicts (hermetic tests build
    the same dicts by hand, so nothing below needs psutil)."""
    procs = []
    for p in psutil.process_iter(_ATTRS):
        try:
            i = p.info
            mem = i.get("memory_info")
            procs.append({
                "pid": i.get("pid") or 0,
                "ppid": i.get("ppid") or 0,
                "name": i.get("name") or "",
                "cmd": " ".join(i.get("cmdline") or []),
                "exe": i.get("exe") or "",
                "ct": float(i.get("create_time") or 0.0),
                "cpu": float(i.get("cpu_percent") or 0.0),
                "rss": int(mem.rss) if mem else 0,
                "user": i.get("username") or "",
                "status": i.get("status") or "",
                "tty": i.get("terminal") if _HAS_TTY else None,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return procs


# ── Families + never-touch ───────────────────────────────────────────────

FAMILIES = ("headless", "node", "python")
FAMILY_LABEL = {"headless": "headless browser", "node": "node", "python": "python"}
_NAME_W = 32   # "Google Chrome for Testing" must survive in leak names and reap messages

# Browser automation installs (puppeteer / playwright / Chrome for Testing).
# playwright caches under ~/.cache/ms-playwright (Linux) or
# ~/Library/Caches/ms-playwright (macOS); puppeteer under ~/.cache/puppeteer.
_AUTOMATION_PATHS = (
    "/.cache/puppeteer/", "ms-playwright/", "/node_modules/puppeteer",
    "/node_modules/playwright", "chrome-headless-shell", "chrome for testing",
)
_BROWSER_WORDS = ("chrome", "chromium", "firefox", "webkit", "for testing", "headless-shell")

# Anything matching these is a LIVE workload, an agent, the fleet or the OS.
_NEVER_TOUCH_CMD = (
    "anthropic.claude-code", "/native-binary/claude", "claude-code", "@anthropic-ai/",
    "chrome-native-host", "openai.chatgpt", "codex", "mcp", "copilot",   # AI sessions, MCP servers, native hosts
    "ollama", "lmstudio", "llama",                     # local LLM runtimes
    "macmon", "sentinel", "neobot", "aegis",           # engine, body, brain
    "wireguard", "wg-quick", "tailscale", "sshd",      # fleet mesh + remote access
    "com.docker", "docker", "colima", "limactl", "qemu",
    "virtualization", "vmware", "parallels",           # VMs + containers
)
_NEVER_TOUCH_NAME_EXACT = {"sh", "zsh", "bash", "fish", "login", "tmux", "screen", "claude", "codex", "ollama", "utm"}
_NEVER_TOUCH_NAME_PREFIX = ("code helper", "visual studio code", "code -", "electron", "cursor",
                            "zed", "xcode", "terminal", "iterm", "ghostty", "warp", "alacritty", "kitty")
# System binaries: never automation, never a leak.
_SYSTEM_EXE_PREFIX = ("/system/", "/usr/libexec/", "/usr/sbin/", "/sbin/", "/library/apple/", "/system/volumes/preboot/")
# The user's apps -- EXCEPT a browser binary running --headless, which is an
# automation instance (puppeteer/playwright with channel=chrome), not an app
# the user is looking at.
_USER_APP_PREFIX = ("/applications/",)
# Agent / editor infrastructure under the home directory (hooks, plugins, MCP
# servers, native hosts, extension helpers). Absolute prefixes on purpose: a
# project's own `.claude/worktrees/...` checkout is a workspace, not this.
_HOME_PROTECTED = tuple(
    # trailing separator, normalized to '/' so the substring test is separator-agnostic:
    # on Windows a cmdline mixes '\' and '/' (expanduser keeps the tail's '/'), so a
    # backslash-only prefix would never match '...\.claude/hooks/x.js'.
    (os.path.join(os.path.expanduser("~"), d, "").replace("\\", "/").lower())
    for d in (".claude", ".codex", ".cursor", ".vscode", ".ollama", ".macmon")
)


def _home_protected_name(pref: str) -> str:
    """'/users/neo/.claude/' or 'c:\\users\\neo\\.claude\\' -> '.claude'."""
    return os.path.basename(pref.rstrip("/\\").replace("\\", "/"))


def _system_path(exe_l: str) -> bool:
    return exe_l.startswith(_SYSTEM_EXE_PREFIX) or exe_l.startswith(("/bin/", "/usr/bin/"))


def family_of(p: dict):
    """Which leak family a process belongs to, or None.

    'headless' is browser AUTOMATION only (puppeteer/playwright caches, Chrome
    for Testing, or --headless on a browser binary) -- a name merely containing
    'headless' (qemu-system-aarch64-headless, the Android emulator) is NOT it."""
    name = (p.get("name") or "").lower()
    cmd = (p.get("cmd") or "").lower()
    exe = (p.get("exe") or "").lower()
    # '/'-normalized so the forward-slash automation paths match a Windows backslash cmdline too.
    hay = (exe + " " + cmd).replace("\\", "/")
    if any(s in hay for s in _AUTOMATION_PATHS):
        return "headless"
    if "--headless" in cmd and any(w in name or w in exe for w in _BROWSER_WORDS):
        return "headless"
    base = os.path.basename(exe) if exe else ""
    for probe in (name, base):
        cat = categorize_process(probe) if probe else "other"
        if cat in ("node", "python"):
            return cat
    if name.startswith(("next-server", "next-router-worker", "next-render-worker")):
        return "node"
    return None


def never_touch(p: dict):
    """Reason this process must never be signalled by AegisForge, or None.

    Mirrors the judgment an operator (and the OS permission layer) applies:
    a live workload, an agent session, the fleet or the OS is off-limits."""
    pid = int(p.get("pid") or 0)
    name = p.get("name") or ""
    name_l = name.lower()
    if _is_protected_target(pid, name):
        return "system"
    user = p.get("user") or ""
    if user and _ME and user != _ME:
        return "other user"
    cat = categorize_process(name)
    if cat == "ide":
        return "ide"
    if cat == "llm":
        return "llm"
    if cat == "docker":
        return "container"
    if name_l in _NEVER_TOUCH_NAME_EXACT or name_l.startswith(_NEVER_TOUCH_NAME_PREFIX):
        return "protected name"
    # Normalize path separators to '/': the patterns below are forward-slash, but a
    # Windows cmdline/exe uses '\' -- without this a protected path would be missed off macOS.
    hay = ((p.get("cmd") or "") + " " + (p.get("exe") or "")).replace("\\", "/").lower()
    for pat in _NEVER_TOUCH_CMD:
        if pat in hay:
            return f"protected ({pat})"
    for pref in _HOME_PROTECTED:
        if pref in hay:
            return f"protected (~/{_home_protected_name(pref)})"
    exe_l = (p.get("exe") or "").lower()
    if exe_l.startswith(_SYSTEM_EXE_PREFIX):
        return "system path"
    fam = family_of(p)
    if exe_l.startswith(_USER_APP_PREFIX) and fam != "headless":
        return "user app"
    if cat == "browser" and fam != "headless":
        return "user browser"
    return None


# ── Lineage: the only accepted proof that a parent died ──────────────────

def _same(a: float, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1.0
    except (TypeError, ValueError):
        return False


def update_lineage(procs: list[dict], lineage: dict, by_pid: dict) -> dict:
    """Remember, for every dev-family process with a LIVE parent, who that
    parent is (pid + both create times). An entry survives reparenting so the
    evidence is still there once ppid flips to 1; dead pids are pruned."""
    new = {}
    for p in procs:
        if family_of(p) is None or never_touch(p):
            continue
        key = str(p["pid"])
        if p["ppid"] not in (0, 1):
            parent = by_pid.get(p["ppid"])
            new[key] = [int(p["ppid"]), float(p["ct"]), float(parent["ct"]) if parent else None]
        else:
            prev = lineage.get(key)
            if prev and len(prev) == 3 and _same(prev[1], p["ct"]):
                new[key] = prev
    return new


def leak_proof(p: dict, lineage: dict, by_pid: dict):
    """Why this ppid==1 process is a proven leak, or None.

    Proof requires an earlier sample that saw it under a real parent X, and X
    now exited / zombie / replaced by a newer process reusing its PID."""
    if p.get("ppid") != 1:
        return None
    prev = lineage.get(str(p["pid"]))
    if not prev or len(prev) != 3 or not _same(prev[1], p["ct"]):
        return None
    old_ppid, _, parent_ct = prev
    if old_ppid in (0, 1):
        return None
    parent = by_pid.get(old_ppid)
    if parent is None or parent.get("status") == psutil.STATUS_ZOMBIE:
        return f"parent pid {old_ppid} exited"
    if parent_ct is not None and not _same(parent["ct"], parent_ct):
        return f"parent pid {old_ppid} exited (pid reused)"
    return None


def leak_candidates(procs: list[dict]) -> list[dict]:
    """ppid == 1 dev-family processes that are not off-limits and not attached
    to a terminal (a live shell still owns those)."""
    out = []
    for p in procs:
        if p.get("ppid") != 1 or family_of(p) is None:
            continue
        if never_touch(p) or p.get("tty"):
            continue
        out.append(p)
    return out


def _descendants(pid: int, by_ppid: dict, depth: int = 0) -> list[dict]:
    if depth > 8:
        return []
    out = []
    for c in by_ppid.get(pid, []):
        out.append(c)
        out.extend(_descendants(c["pid"], by_ppid, depth + 1))
    return out


# ── Swarm attribution ────────────────────────────────────────────────────

def _short_cmd(p: dict) -> str:
    """'node shoot.mjs' style label: name + the script it runs, if any."""
    name = (p.get("name") or "?")[:24]
    parts = (p.get("cmd") or "").split()
    for a in parts[1:]:
        if not a.startswith("-"):
            base = os.path.basename(a)
            if base and base.lower() != name.lower():
                return f"{name} {base}"[:48]
            break
    return name


def spawner_of(procs: list[dict], fam: str, by_pid: dict) -> str:
    """Name the most common live ancestor OUTSIDE the family (the spawner)."""
    counter: Counter = Counter()
    orphaned = 0
    for p in procs:
        if family_of(p) != fam:
            continue
        q, depth = p, 0
        while depth < 8:
            parent = by_pid.get(q.get("ppid"))
            if parent is None or parent["pid"] <= 1:
                if q.get("ppid") == 1:
                    orphaned += 1
                break
            if family_of(parent) != fam:
                counter[parent["pid"]] += 1
                break
            q, depth = parent, depth + 1
    if not counter:
        return "an exited parent (orphaned)" if orphaned else ""
    pid, _ = counter.most_common(1)[0]
    return f"{_short_cmd(by_pid[pid])} (pid {pid})"


def family_counts(procs: list[dict]) -> dict:
    counts: Counter = Counter()
    for p in procs:
        fam = family_of(p)
        if fam:
            counts[fam] += 1
    return dict(counts)


def name_counts(procs: list[dict], floor: int, top: int = 8) -> dict:
    """Instances per executable name (generic swarm tracking). GUI apps under
    /Applications and /System are skipped (a browser with 40 tabs is not a
    runaway), as are headless-family processes (tracked by family)."""
    counts: Counter = Counter()
    for p in procs:
        exe_l = (p.get("exe") or "").lower()
        if exe_l.startswith(_USER_APP_PREFIX) or _system_path(exe_l):
            continue
        if family_of(p) == "headless":
            continue
        name = p.get("name") or ""
        if name:
            counts[name] += 1
    return {n: c for n, c in counts.most_common(top) if c >= floor}


def top_cpu(procs: list[dict], ncpu: int, n: int = 3) -> list:
    ranked = sorted(procs, key=lambda p: p.get("cpu", 0.0), reverse=True)[:n]
    return [[(p.get("name") or "?")[:24], round(p.get("cpu", 0.0) / max(1, ncpu), 1), p["pid"]]
            for p in ranked if p.get("cpu", 0.0) > 0]


def top_rss(procs: list[dict], n: int = 2) -> list:
    ranked = sorted(procs, key=lambda p: p.get("rss", 0), reverse=True)[:n]
    return [[(p.get("name") or "?")[:24], p.get("rss", 0) // (1024 * 1024)] for p in ranked if p.get("rss", 0) > 0]


# ── Observation (one sample) ─────────────────────────────────────────────

def observe_table(procs: list[dict], astate: dict, cfg: dict, ncpu: int) -> dict:
    """Pure: derive this sample's record fields + the proven-leak list from a
    process table, updating lineage/idle streaks in `astate`."""
    by_pid = {p["pid"]: p for p in procs}
    by_ppid: dict = {}
    for p in procs:
        by_ppid.setdefault(p["ppid"], []).append(p)
    lineage = update_lineage(procs, astate.get("lineage") or {}, by_pid)
    astate["lineage"] = lineage

    cands = leak_candidates(procs)
    leaks = []
    for p in cands:
        proof = leak_proof(p, lineage, by_pid)
        if not proof:
            continue
        busy = p.get("cpu", 0.0) >= 1.0 or any(c.get("cpu", 0.0) >= 1.0 for c in _descendants(p["pid"], by_ppid))
        leaks.append({**p, "proof": proof, "busy": busy, "family": family_of(p)})

    prev = astate.get("leak_streak") or {}
    streaks = {}
    for L in leaks:
        key = str(L["pid"])
        streaks[key] = 0 if L["busy"] else int(prev.get(key, 0)) + 1
    astate["leak_streak"] = streaks

    counts = family_counts(procs)
    record = {
        "ncpu": ncpu,
        "fam": counts,
        "pc": name_counts(procs, _int(cfg, "swarm_floor", 8, lo=2)),
        "orph": [len(cands), len(leaks)],
        "topn": top_cpu(procs, ncpu),
        "toprss": top_rss(procs),
    }
    if counts.get("headless"):
        record["spawn"] = spawner_of(procs, "headless", by_pid)
    if leaks:
        names = Counter((L.get("name") or "?")[:_NAME_W] for L in leaks)
        record["leakn"] = [f"{nm} x{c}" if c > 1 else nm for nm, c in names.most_common(3)]
    return {"record": record, "leaks": leaks, "cands": cands, "streaks": streaks}


def observe(astate: dict, cfg: dict) -> dict:
    """Scan the live process table and observe it (see observe_table)."""
    return observe_table(scan(), astate, cfg, psutil.cpu_count() or 1)


# ── Detectors (pure over the metrics history) ────────────────────────────

@dataclass
class Finding:
    key: str
    title: str
    msg: str
    cooldown: int
    level: str = "medium"


def trends(rows: list[dict], cfg: dict, now: float) -> dict:
    """Slopes, ETAs, sustained counts and growth figures the detectors and the
    console share. Empty dict when there is no history. Null-tolerant: a
    missing / null / misshapen field in any row reads as "no data"."""
    if not rows:
        return {}
    latest = rows[-1]
    n = _int(cfg, "trend_window", 10, lo=3)
    recent = recent_rows(rows, n, now) or [latest]

    swap = _as_float(latest.get("swap_gb"))
    ram = _as_float(latest.get("ram"))
    swap_slope = slope_per_min([(r.get("ts"), r.get("swap_gb")) for r in recent])
    ram_slope = slope_per_min([(r.get("ts"), r.get("ram")) for r in recent])
    swap_crit = _num(cfg, "swap_critical_gb", 8.0)
    ram_crit = _num(cfg, "ram_critical", 90.0)

    ncpu = _as_int(latest.get("ncpu")) or (psutil.cpu_count() or 1)
    load1 = _as_float(latest.get("load1"))
    factor = _num(cfg, "load_factor", 2.0)
    sustained = 0
    for r in reversed(recent):
        if _as_float(r.get("load1")) > ncpu * factor:
            sustained += 1
        else:
            break

    w = _int(cfg, "swarm_window", 5, lo=1)
    base = recent[-(w + 1)] if len(recent) > w else recent[0]
    # Growth is only ever claimed against a base sample that carries the
    # process fields ("orph" marks the new record format): right after an
    # upgrade or a fresh install the base has none, and "0 -> 16" would be a
    # fabricated swarm.
    fam_now = _counts(latest.get("fam"))
    fam_base = _counts(base.get("fam")) if "orph" in base else dict(fam_now)
    pc_now = _counts(latest.get("pc"))
    pc_base = _counts(base.get("pc")) if "orph" in base else dict(pc_now)
    orph = _pair(latest.get("orph"))
    orph_base = _pair(recent[0].get("orph")) if "orph" in recent[0] else orph
    span = (_ts(latest, now) - _ts(recent[0], now)) / 60.0 if len(recent) > 1 else 0.0
    # The span the growth figures really cover: `w` samples when the history
    # is that long, the whole (shorter) history otherwise.
    growth_min = (_ts(latest, now) - _ts(base, now)) / 60.0

    return {
        "swap": {"gb": swap, "slope": swap_slope, "eta": eta_minutes(swap, swap_crit, swap_slope), "crit": swap_crit},
        "ram": {"pct": ram, "slope": ram_slope, "eta": eta_minutes(ram, ram_crit, ram_slope), "crit": ram_crit},
        "load": {"load1": load1, "ncpu": ncpu, "ratio": load1 / max(1, ncpu), "sustained": sustained, "factor": factor},
        "swarm": {f: {"count": c, "growth": c - fam_base.get(f, 0)} for f, c in fam_now.items()},
        "names": {nm: {"count": c, "growth": c - pc_base.get(nm, 0)} for nm, c in pc_now.items()},
        "spawn": str(latest.get("spawn") or ""),
        "leaks": {"p1": orph[0], "leak": orph[1], "growth": orph[1] - orph_base[1],
                  "names": _strs(latest.get("leakn"))},
        "topn": _tuples(latest.get("topn"), 3),
        "toprss": _tuples(latest.get("toprss"), 2),
        "span_min": max(0.0, span),
        "growth_min": max(0.0, growth_min),
        "points": len(recent),
    }


def growth_span(t: dict, cfg: dict) -> int:
    """Minutes the swarm growth figures actually cover, for the alert text:
    the real span when the history is shorter than the window (a '+40 in 2
    min' right after a start must not be labelled '5 min'), else the window."""
    w = _int(cfg, "swarm_window", 5, lo=1)
    gm = t.get("growth_min")
    return max(1, round(gm)) if isinstance(gm, (int, float)) and gm > 0 else w


def _rss_note(t: dict) -> str:
    top = t.get("toprss") or []
    if not top:
        return ""
    return " Biggest RSS: " + ", ".join(f"{nm} {mb / 1024:.1f} GB" if mb >= 1024 else f"{nm} {mb} MB" for nm, mb in top) + "."


def detect_swap_trend(t: dict, cfg: dict):
    """Anticipation only: fires while swap is high and climbing fast enough to
    cross the critical line within `swap_eta_min` -- i.e. BEFORE saturation.
    Once past the line the absolute swap alert owns the incident."""
    s = t.get("swap")
    if not s:
        return None
    if s["gb"] < _num(cfg, "swap_trend_min_gb", 3.0) or s["slope"] < _num(cfg, "swap_slope_gb_min", 0.1):
        return None
    eta = s["eta"]
    if eta is None or eta <= 0 or eta > _num(cfg, "swap_eta_min", 20):
        return None
    return Finding("swap_trend", "swap climbing",
                   f"Swap {s['gb']:.1f} GB, climbing +{s['slope']:.2f} GB/min -- reaches the {s['crit']:.0f} GB "
                   f"critical line in ~{max(1, round(eta))} min at this rate.{_rss_note(t)}",
                   900, "high")


def detect_memory_trend(t: dict, cfg: dict):
    """Same anticipation contract as detect_swap_trend, on RAM%."""
    r = t.get("ram")
    if not r:
        return None
    if r["pct"] < _num(cfg, "ram_trend_min", 75.0) or r["slope"] < _num(cfg, "ram_slope_pct_min", 1.0):
        return None
    eta = r["eta"]
    if eta is None or eta <= 0 or eta > _num(cfg, "ram_eta_min", 15):
        return None
    return Finding("ram_trend", "memory pressure climbing",
                   f"RAM {r['pct']:.0f}%, climbing +{r['slope']:.1f}%/min -- hits the {r['crit']:.0f}% critical line "
                   f"in ~{max(1, round(eta))} min at this rate.{_rss_note(t)}",
                   900, "high")


def detect_load(t: dict, cfg: dict):
    ld = t.get("load")
    if not ld or ld["sustained"] < _int(cfg, "load_sustain", 3, lo=1):
        return None
    top = ", ".join(f"{nm} {pct:.0f}%" for nm, pct, _ in (t.get("topn") or [])[:3]) or "n/a"
    return Finding("load", "load catastrophe",
                   f"Load {ld['load1']:.1f} on {ld['ncpu']} cores ({ld['ratio']:.1f}x) for {ld['sustained']} samples "
                   f"-- top CPU: {top}. Not auto-acted: inspect with 'macmon ps --sort cpu'.",
                   900, "critical")


def detect_swarm(t: dict, cfg: dict) -> list:
    """Alert-only by design: names the family, the growth and the spawner.
    Nothing here (or downstream of it) signals a process."""
    out = []
    span = growth_span(t, cfg)
    h = (t.get("swarm") or {}).get("headless")
    if h:
        h_min = _int(cfg, "swarm_headless_min", 8, lo=1)
        h_growth = _int(cfg, "swarm_headless_growth", 8, lo=1)
        h_max = _int(cfg, "swarm_headless_max", 40, lo=1)
        if h["count"] >= h_min and (h["growth"] >= h_growth or h["count"] >= h_max):
            by = t.get("spawn") or "an unknown spawner"
            out.append(Finding("swarm:headless", "process swarm",
                               f"{h['count']} headless browser processes (+{max(0, h['growth'])} in {span} min), "
                               f"spawned by {by}. Not auto-killed (live workload) -- stop the spawner yourself if unintended.",
                               900, "high"))
    s_min = _int(cfg, "swarm_min", 24, lo=1)
    s_growth = _int(cfg, "swarm_growth", 12, lo=1)
    for name, v in (t.get("names") or {}).items():
        if v["count"] >= s_min and v["growth"] >= s_growth:
            out.append(Finding(f"swarm:{name}", "process swarm",
                               f"{v['count']} '{name}' processes (+{v['growth']} in {span} min) -- an executable is "
                               f"multiplying. Not auto-killed (live workload); check 'macmon ps --tree'.",
                               900, "high"))
    return out


def detect_leaks(t: dict, cfg: dict):
    lk = t.get("leaks")
    if not lk or lk["leak"] < _int(cfg, "orphan_alert_min", 3, lo=1):
        return None
    growth = f", +{lk['growth']} over the window" if lk["growth"] > 0 else ""
    names = ", ".join(lk.get("names") or []) or "dev processes"
    if _bool(cfg, "auto_reap_orphans"):
        tail = f"Auto-reap is ON: idle ones are reaped after {_int(cfg, 'reap_idle_samples', 5, lo=1)} samples."
    else:
        tail = "Auto-reap is OFF: run 'macmon sentinel --reap-orphans', or enable with --enable-auto --reap."
    return Finding("leaks", "leaked orphans",
                   f"{lk['leak']} leaked dev processes (parent exited, reparented to PID 1{growth}): {names}. {tail}",
                   1800, "medium")


def analyze(rows: list[dict], cfg: dict, now: float) -> list:
    """All proactive findings for the current history. Pure; notify-only."""
    t = trends(rows, cfg, now)
    if not t:
        return []
    found = []
    for f in (detect_swap_trend(t, cfg), detect_memory_trend(t, cfg), detect_load(t, cfg), detect_leaks(t, cfg)):
        if f:
            found.append(f)
    found.extend(detect_swarm(t, cfg))
    return found


# ── Auto-reap (opt-in) -- the ONLY action this module can take ───────────

REAP_COOLDOWN = 600
DEFAULT_REAP_FAMILIES = list(FAMILIES)
_AF_UNIX = getattr(socket, "AF_UNIX", None)   # absent on some Windows builds


def reap_families(cfg: dict) -> list:
    """The families auto-reap may touch. Missing => the default set; a list =>
    as given (empty means none); anything else -- a bare string, a dict, a
    number -- is malformed and narrows to NOTHING, never widens to everything."""
    v = cfg.get("reap_families")
    if v is None:
        return list(DEFAULT_REAP_FAMILIES)
    if isinstance(v, (list, tuple, set)):
        return [f for f in v if isinstance(f, str)]
    return []


def _in_service(pid: int, family=None):
    """Is the process (or a descendant) serving or talking to anyone?

    True on an ESTABLISHED connection -- and, for every family but 'headless',
    on a mere LISTEN or a bound unix socket: an idle backgrounded dev server
    with no client right now is still in service and must never be "reaped".
    Puppeteer/playwright's Chrome stays reapable while only listening, because
    its remote-debugging port is what a leaked automation browser looks like,
    not a service anyone uses. Unnamed unix sockets (socketpair IPC plumbing)
    count for nobody. False when nothing of the kind is open; None when it
    cannot be determined (=> caller must skip). An unknown family reads as a
    server (the safe reading)."""
    listening_counts = family != "headless"
    try:
        p = psutil.Process(pid)
        procs = [p] + p.children(recursive=True)
        for q in procs:
            conns = q.net_connections(kind="all") if hasattr(q, "net_connections") else q.connections(kind="all")
            for c in conns:
                if c.status == psutil.CONN_ESTABLISHED:
                    return True
                if listening_counts and (c.status == psutil.CONN_LISTEN
                                         or (_AF_UNIX is not None and c.family == _AF_UNIX and c.laddr)):
                    return True
        return False
    except psutil.NoSuchProcess:
        return None
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _still_leaked(pid: int, create_time: float) -> bool:
    """Fresh check right before signalling: same process (create_time), still
    reparented to PID 1."""
    try:
        p = psutil.Process(pid)
        return _same(p.create_time(), create_time) and p.ppid() == 1
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def _terminate(pid: int) -> bool:
    """SIGTERM only (graceful) -- never SIGKILL."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def reap_leaks(leaks: list[dict], cfg: dict, astate: dict, now: float) -> list:
    """Reap proven leaks. Opt-in (`auto_reap_orphans`), cooldown-gated, and
    every candidate is re-validated at signal time. Returns [(key, msg)].

    Every list entry here already passed: ppid == 1, dev family, never-touch
    (see observe_table). This adds: family allow-list, not busy, idle streak,
    not in service (no ESTABLISHED connection; for node/python no LISTEN or
    bound unix socket either; unknown => skip), still leaked NOW, SIGTERM."""
    if not _bool(cfg, "auto_reap_orphans") or not leaks:
        return []
    if now - float(astate.get("_reap", 0) or 0) < REAP_COOLDOWN:
        return []
    families = reap_families(cfg)
    idle_needed = _int(cfg, "reap_idle_samples", 5, lo=1)
    cap = _int(cfg, "reap_max", 40, lo=1)
    streaks = astate.get("leak_streak") or {}
    reaped = []
    for L in leaks:
        if len(reaped) >= cap:
            break
        pid = int(L["pid"])
        if L.get("family") not in families or L.get("busy"):
            continue
        if int(streaks.get(str(pid), 0)) < idle_needed:
            continue
        if never_touch(L) or L.get("ppid") != 1:      # belt and braces
            continue
        if _in_service(pid, L.get("family")) is not False:
            continue
        if not _still_leaked(pid, L["ct"]):
            continue
        if _terminate(pid):
            reaped.append(L)
    if not reaped:
        return []
    astate["_reap"] = now
    names = Counter((L.get("name") or "?")[:_NAME_W] for L in reaped)
    what = ", ".join(f"{nm} x{c}" if c > 1 else nm for nm, c in names.most_common(4))
    return [("auto_reap", f"Reaped {len(reaped)} leaked orphan(s) (PID 1, parent exited, idle): {what} -- SIGTERM, graceful")]
