"""AegisForge proactive layer -- detector tests + safety invariants.

Hermetic: process tables are plain dicts in the exact shape aegis.scan()
produces (so no psutil is needed), every signalling primitive is
monkeypatched (os.kill / psutil.Process.* raise if reached), and every file
lives under tmp_path. Cross-platform.

The invariants locked here (see the module docstring of aegis.py):
  1. detection is ON by default and notify-only; auto_reap_orphans defaults to False
  2. the swarm detector never signals anything: not the spawner, not a child
  3. only a LINEAGE-proven dead parent (watched by an earlier sample) makes a leak
  4. the never-touch set (IDE, agent sessions, ollama, fleet, VMs, shells, system
     paths, user apps, other users) is applied before anything else
  5. a proven leak is reaped only when opt-in + idle N samples + not in service
     (no ESTABLISHED connection; for node/python no LISTEN / bound unix socket
     either) + still under PID 1 at signal time, and only with SIGTERM
  6. config flags are coerced, never read by raw truthiness: the string
     "false" never enables an auto_* feature, a malformed reap_families
     reaps nothing
  7. a failing stage in run_sample never loses the persisted state
"""
import inspect
import json
import os
import signal
import socket
import sys
import time
import types

import psutil
import pytest

from macmon_core import aegis, sentinel
from macmon_core.aegis import Finding

NOW = 1_800_000_000.0
CFG = dict(sentinel.DEFAULTS)

PUPPETEER = ("/Users/neo/.cache/puppeteer/chrome/mac_arm-140.0.7339.82/chrome-mac-arm64/"
             "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing")
PLAYWRIGHT = "/Users/neo/Library/Caches/ms-playwright/chromium-1187/chrome-mac/Chromium.app/Contents/MacOS/Chromium"
SYS_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
NODE = "/Users/neo/.nvm/versions/node/v22.22.0/bin/node"
PY = "/Users/neo/proj/.venv/bin/python3.12"


def P(pid, ppid, name, cmd="", exe="", cpu=0.0, rss=0, ct=1000.0, user="neo", status="running", tty=None):
    """One process-table row (same keys as aegis.scan())."""
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd or exe, "exe": exe, "ct": float(ct),
            "cpu": cpu, "rss": rss, "user": user, "status": status, "tty": tty}


def LAUNCHD():
    return P(1, 0, "launchd", exe="/sbin/launchd", user="root")


def rows(series, key="swap_gb", start=NOW - 9 * 60, step=60, **extra):
    """Metric rows (new record format), one per minute, with `key` = series[i]."""
    out = []
    for i, v in enumerate(series):
        r = {"ts": start + i * step, "cpu": 10.0, "ram": 50.0, "swap_gb": 1.0, "load1": 2.0, "disk_free_gb": 100.0,
             "claude": [0, 0], "codex": [0, 0], "mcp": [0, 0], "top": ["x", 1.0, 10], "rtt": None,
             "ollama_gb": 0, "vm_gb": 0, "ncpu": 12, "fam": {}, "pc": {}, "orph": [0, 0], "topn": [], "toprss": []}
        r.update(extra)
        r[key] = v
        out.append(r)
    return out


@pytest.fixture(autouse=True)
def _identity(monkeypatch):
    # Pin the identity the never-touch set compares against, and the home dir
    # the protected-config prefixes derive from (the tests run on any account).
    monkeypatch.setattr(aegis, "_ME", "neo")
    monkeypatch.setattr(aegis, "_HOME_PROTECTED",
                        tuple(f"/users/neo/{d}/" for d in (".claude", ".codex", ".cursor", ".vscode", ".ollama", ".macmon")))


@pytest.fixture
def no_signals(monkeypatch):
    """Any attempt to signal a process fails the test."""
    def boom(*a, **k):
        raise AssertionError("a process was signalled")
    monkeypatch.setattr(os, "kill", boom)
    monkeypatch.setattr(psutil.Process, "terminate", boom)
    monkeypatch.setattr(psutil.Process, "kill", boom)
    monkeypatch.setattr(psutil.Process, "send_signal", boom)


# ── Trend / slope math ────────────────────────────────────────────────────

class TestTrendMath:
    def test_linear_series_slope_is_exact(self):
        pts = [(NOW + i * 60, 1.0 + 0.5 * i) for i in range(10)]
        assert aegis.slope_per_min(pts) == pytest.approx(0.5)

    def test_flat_or_too_short_claims_no_trend(self):
        assert aegis.slope_per_min([(NOW + i * 60, 4.0) for i in range(10)]) == 0.0
        assert aegis.slope_per_min([(NOW, 1.0), (NOW + 60, 9.0)]) == 0.0
        assert aegis.slope_per_min([]) == 0.0
        assert aegis.slope_per_min([(NOW, 1.0), (NOW, 2.0), (NOW, 3.0)]) == 0.0  # zero time spread

    def test_irregular_spacing_uses_real_timestamps(self):
        # samples at 0, 2 and 5 minutes; value == minutes -> slope 1.0/min
        pts = [(NOW, 0.0), (NOW + 120, 2.0), (NOW + 300, 5.0)]
        assert aegis.slope_per_min(pts) == pytest.approx(1.0)

    def test_eta(self):
        assert aegis.eta_minutes(4.0, 8.0, 0.5) == pytest.approx(8.0)
        assert aegis.eta_minutes(9.0, 8.0, 0.5) == 0.0          # already past
        assert aegis.eta_minutes(4.0, 8.0, 0.0) is None          # not climbing
        assert aegis.eta_minutes(4.0, 8.0, -0.2) is None

    def test_recent_rows_drops_samples_across_a_sleep_gap(self):
        early = rows([1.0] * 5, start=NOW - 3 * 3600)               # before a 3h sleep
        late = rows([1.0] * 5, start=NOW - 4 * 60)                  # after waking
        got = aegis.recent_rows(early + late, 10, NOW)
        assert len(got) == 5 and all(r["ts"] >= NOW - 4 * 60 for r in got)


# ── Families ─────────────────────────────────────────────────────────────

class TestFamilies:
    def test_puppeteer_install_is_headless_even_without_the_flag(self):
        assert aegis.family_of(P(5, 4, "Google Chrome for Testing", exe=PUPPETEER)) == "headless"

    def test_playwright_install_is_headless(self):
        assert aegis.family_of(P(5, 4, "Chromium", exe=PLAYWRIGHT)) == "headless"

    def test_system_chrome_with_headless_flag_is_headless(self):
        p = P(5, 1, "Google Chrome", cmd=f"{SYS_CHROME} --no-startup-window --headless=new --hide-scrollbars", exe=SYS_CHROME)
        assert aegis.family_of(p) == "headless"

    def test_headed_user_chrome_is_no_family(self):
        assert aegis.family_of(P(5, 1, "Google Chrome", exe=SYS_CHROME)) is None

    def test_android_emulator_named_headless_is_not_a_browser(self):
        exe = "/Users/neo/Library/Android/sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64-headless"
        p = P(7, 1, "qemu-system-aarch64-headless", cmd=f"{exe} -avd Pixel_8", exe=exe)
        assert aegis.family_of(p) is None
        assert aegis.never_touch(p) is not None  # a VM is a live workload

    def test_node_and_python_families(self):
        assert aegis.family_of(P(8, 1, "next-server (v15.5.2)", cmd=f"{NODE} next-server", exe=NODE)) == "node"
        assert aegis.family_of(P(9, 1, "node", cmd="npm exec vite --port 5173", exe=NODE)) == "node"
        assert aegis.family_of(P(10, 1, "python3.12", cmd=f"{PY} worker.py", exe=PY)) == "python"
        assert aegis.family_of(P(11, 1, "Python", exe="/opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python")) == "python"


# ── Never-touch set ──────────────────────────────────────────────────────

_PROTECTED = [
    ("launchd pid 1", LAUNCHD()),
    ("own process", P(os.getpid(), 1, "python3", exe=PY)),
    ("system-critical name", P(777, 1, "WindowServer", exe="/System/Library/PrivateFrameworks/SkyLight.framework/WindowServer")),
    ("VSCode helper", P(700, 690, "Code Helper (Renderer)", cmd="/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/Contents/MacOS/Code Helper (Renderer) --type=renderer", exe="/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/Contents/MacOS/Code Helper (Renderer)")),
    ("Claude Code session", P(701, 1, "claude", cmd="/Users/neo/.vscode/extensions/anthropic.claude-code-2.1.281-darwin-arm64/resources/native-binary/claude", exe="/Users/neo/.vscode/extensions/anthropic.claude-code-2.1.281-darwin-arm64/resources/native-binary/claude")),
    ("Claude native host in Chrome", P(702, 636, "node", cmd=f"{NODE} /Users/neo/.nvm/versions/node/v22.22.0/lib/node_modules/@anthropic-ai/claude-code/cli.js --chrome-native-host", exe=NODE)),
    ("Claude hook under ~/.claude", P(703, 1, "node", cmd=f"{NODE} /Users/neo/.claude/hooks/notify.js", exe=NODE)),
    ("codex", P(704, 1, "codex", cmd="/Users/neo/.vscode/extensions/openai.chatgpt-26.917.62051-darwin-arm64/bin/macos/codex app-server", exe="/Users/neo/.vscode/extensions/openai.chatgpt-26.917.62051-darwin-arm64/bin/macos/codex")),
    ("MCP server", P(705, 1, "node", cmd=f"{NODE} /Users/neo/tools/tradingview-mcp/server.js", exe=NODE)),
    ("VSCode extension helper", P(706, 1, "pet", exe="/Users/neo/.vscode/extensions/ms-python.vscode-python-envs-1.36.0-darwin-arm64/pet")),
    ("ollama", P(710, 1, "ollama", cmd="/opt/homebrew/bin/ollama serve", exe="/opt/homebrew/Cellar/ollama/0.17.0/bin/ollama")),
    ("ollama runner", P(711, 710, "ollama", cmd="/opt/homebrew/bin/ollama runner --model x", exe="/opt/homebrew/bin/ollama")),
    ("sentinel sampler", P(720, 1, "Python", cmd=f"{PY} /Users/neo/dev/macmoncli/macmon.py sentinel --sample", exe=PY)),
    ("NeoBot", P(721, 1, "python3", cmd="python3 /root/SAAS/neobot/bot/main.py", exe="/usr/bin/python3")),
    ("Sentinel body", P(722, 1, "Sentinel", exe="/Applications/Sentinel.app/Contents/MacOS/Sentinel")),
    ("WireGuard", P(730, 1, "wireguard-go", cmd="wireguard-go utun3", exe="/opt/homebrew/bin/wireguard-go")),
    ("sshd", P(731, 1, "sshd", exe="/usr/sbin/sshd")),
    ("Docker backend", P(740, 1, "com.docker.backend", exe="/Applications/Docker.app/Contents/MacOS/com.docker.backend")),
    ("colima", P(741, 1, "colima", cmd="colima daemon start", exe="/opt/homebrew/bin/colima")),
    ("Apple VM host", P(742, 1, "com.apple.Virtualization.VirtualMachine", cmd="/System/Library/Frameworks/Virtualization.framework/Versions/A/XPCServices/com.apple.Virtualization.VirtualMachine.xpc/Contents/MacOS/com.apple.Virtualization.VirtualMachine")),
    ("zsh", P(750, 749, "zsh", cmd="/bin/zsh -il", exe="/bin/zsh")),
    ("Terminal", P(751, 1, "Terminal", exe="/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")),
    ("tmux", P(752, 1, "tmux", exe="/opt/homebrew/bin/tmux")),
    ("system daemon", P(760, 1, "distnoted", exe="/usr/sbin/distnoted")),
    ("/usr/libexec", P(761, 1, "knowledge-agent", exe="/usr/libexec/knowledge-agent")),
    ("user's headed Chrome", P(770, 1, "Google Chrome", exe=SYS_CHROME)),
    ("user's Chrome helper", P(771, 770, "Google Chrome Helper (Renderer)", cmd=f"{SYS_CHROME} Helper --type=renderer", exe="/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/154/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper")),
    ("user's app", P(772, 1, "Slack", exe="/Applications/Slack.app/Contents/MacOS/Slack")),
    ("root-owned", P(780, 1, "node", exe=NODE, user="root")),
    ("another user", P(781, 1, "python3", exe=PY, user="_www")),
]


class TestNeverTouch:
    @pytest.mark.parametrize("label,proc", _PROTECTED, ids=[x[0] for x in _PROTECTED])
    def test_protected(self, label, proc):
        assert aegis.never_touch(proc) is not None, label

    @pytest.mark.parametrize("label,proc", _PROTECTED, ids=[x[0] for x in _PROTECTED])
    def test_protected_is_never_a_leak_candidate(self, label, proc):
        p = dict(proc, ppid=1)  # even sitting under PID 1
        assert aegis.leak_candidates([LAUNCHD(), p]) == [], label

    @pytest.mark.parametrize("label,proc", [
        ("leaked puppeteer Chrome", P(5000, 1, "Google Chrome for Testing", cmd=f"{PUPPETEER} --headless=new", exe=PUPPETEER)),
        ("headless system Chrome (automation instance)", P(5001, 1, "Google Chrome", cmd=f"{SYS_CHROME} --no-startup-window --headless=new", exe=SYS_CHROME)),
        ("dev server in a project worktree", P(5002, 1, "node", cmd=f"{NODE} /Users/neo/Documents/proj/.claude/worktrees/x/node_modules/.bin/vite --port 5173", exe=NODE)),
        ("next-server", P(5003, 1, "next-server (v15.5.2)", cmd=f"{NODE} next-server", exe=NODE)),
        ("python worker", P(5004, 1, "python3.12", cmd=f"{PY} worker.py", exe=PY)),
    ], ids=lambda x: x if isinstance(x, str) else "")
    def test_leak_kinds_are_not_protected(self, label, proc):
        assert aegis.never_touch(proc) is None, label

    def test_terminal_attached_process_is_not_a_candidate(self):
        p = P(5005, 1, "node", cmd=f"{NODE} server.js", exe=NODE, tty="/dev/ttys004")
        assert aegis.leak_candidates([LAUNCHD(), p]) == []

    def test_live_parent_is_never_a_candidate(self):
        p = P(5006, 4000, "Google Chrome for Testing", exe=PUPPETEER)
        assert aegis.leak_candidates([LAUNCHD(), P(4000, 3000, "node", exe=NODE), p]) == []


# ── Lineage: the only accepted proof of a dead parent ────────────────────

def _t1(child_cpu=0.0):
    return [LAUNCHD(), P(3000, 900, "zsh", exe="/bin/zsh"),
            P(4000, 3000, "node", cmd=f"{NODE} /Users/neo/proj/shoot.mjs", exe=NODE, cpu=30.0),
            P(5000, 4000, "Google Chrome for Testing", cmd=f"{PUPPETEER} --headless=new", exe=PUPPETEER, cpu=child_cpu)]


def _t2(child_cpu=0.0, extra=()):
    return [LAUNCHD(), P(3000, 900, "zsh", exe="/bin/zsh"),
            P(5000, 1, "Google Chrome for Testing", cmd=f"{PUPPETEER} --headless=new", exe=PUPPETEER, cpu=child_cpu)] + list(extra)


class TestLineageProof:
    def test_parent_watched_dying_is_proof(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        forge = aegis.observe_table(_t2(), astate, CFG, 12)
        assert [L["pid"] for L in forge["leaks"]] == [5000]
        assert forge["leaks"][0]["proof"] == "parent pid 4000 exited"
        assert forge["record"]["orph"] == [1, 1]

    def test_parent_pid_reused_by_a_newer_process_is_still_dead(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        impostor = P(4000, 3000, "python3", exe=PY, ct=999999.0)  # same PID, different create time
        forge = aegis.observe_table(_t2(extra=[impostor]), astate, CFG, 12)
        assert len(forge["leaks"]) == 1 and "pid reused" in forge["leaks"][0]["proof"]

    def test_zombie_parent_is_dead(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        zombie = P(4000, 3000, "node", exe=NODE, status="zombie")
        forge = aegis.observe_table(_t2(extra=[zombie]), astate, CFG, 12)
        assert len(forge["leaks"]) == 1

    def test_first_seen_under_pid1_is_a_candidate_but_never_proven(self):
        # No earlier sample saw the parent: launchd-spawned services and
        # pre-existing orphans look exactly like this. Count, never reap.
        forge = aegis.observe_table(_t2(), {}, CFG, 12)
        assert forge["leaks"] == [] and len(forge["cands"]) == 1
        assert forge["record"]["orph"] == [1, 0]

    def test_parent_still_alive_means_no_proof(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        alive = P(4000, 3000, "node", cmd=f"{NODE} /Users/neo/proj/shoot.mjs", exe=NODE)  # same create time
        forge = aegis.observe_table(_t2(extra=[alive]), astate, CFG, 12)
        assert forge["leaks"] == []

    def test_child_pid_reuse_is_not_the_same_process(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        t2 = [LAUNCHD(), P(5000, 1, "Google Chrome for Testing", exe=PUPPETEER, ct=424242.0)]
        assert aegis.observe_table(t2, astate, CFG, 12)["leaks"] == []

    def test_proof_persists_across_later_samples_and_dead_pids_are_pruned(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        assert "4000" in astate["lineage"]
        aegis.observe_table(_t2(), astate, CFG, 12)
        assert "4000" not in astate["lineage"]          # exited -> pruned
        forge = aegis.observe_table(_t2(), astate, CFG, 12)  # two samples later
        assert len(forge["leaks"]) == 1 and "5000" in astate["lineage"]

    def test_idle_streak_counts_idle_samples_and_resets_when_busy(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        aegis.observe_table(_t2(), astate, CFG, 12)
        aegis.observe_table(_t2(), astate, CFG, 12)
        assert astate["leak_streak"] == {"5000": 2}
        aegis.observe_table(_t2(child_cpu=45.0), astate, CFG, 12)
        assert astate["leak_streak"] == {"5000": 0}

    def test_busy_descendant_makes_the_leak_busy(self):
        astate = {}
        aegis.observe_table(_t1(), astate, CFG, 12)
        helper = P(5001, 5000, "Google Chrome for Testing Helper (GPU)", cmd=f"{PUPPETEER} --type=gpu-process --headless=new", exe=PUPPETEER, cpu=25.0)
        forge = aegis.observe_table(_t2(extra=[helper]), astate, CFG, 12)
        assert forge["leaks"][0]["busy"] is True and astate["leak_streak"] == {"5000": 0}


# ── Observation record ───────────────────────────────────────────────────

def _swarm_table(n, child_ppid=4000, child_cpu=5.0):
    t = [LAUNCHD(), P(3000, 900, "zsh", exe="/bin/zsh"),
         P(4000, 3000, "node", cmd=f"{NODE} /Users/neo/proj/shoot.mjs", exe=NODE, cpu=40.0, rss=200 << 20)]
    for i in range(n):
        t.append(P(5000 + i, child_ppid, "Google Chrome for Testing", cmd=f"{PUPPETEER} --headless=new --remote-debugging-port=0",
                   exe=PUPPETEER, cpu=child_cpu, rss=150 << 20))
    return t


class TestObserveRecord:
    def test_record_fields_and_spawner(self):
        t = _swarm_table(3) + [P(6000, 1, "python3.12", cmd=f"{PY} worker.py", exe=PY, rss=900 << 20),
                              P(6001, 1, "Google Chrome", exe=SYS_CHROME, cpu=120.0, rss=2 << 30)]
        rec = aegis.observe_table(t, {}, CFG, 12)["record"]
        assert rec["ncpu"] == 12
        assert rec["fam"] == {"headless": 3, "node": 1, "python": 1}
        assert rec["orph"] == [1, 0]                      # the python worker: under PID 1, unproven
        assert rec["spawn"] == "node shoot.mjs (pid 4000)"
        assert rec["topn"][0] == ["Google Chrome", 10.0, 6001]   # 120% of one core / 12 cores
        assert rec["toprss"][0][0] == "Google Chrome"
        assert "pc" in rec and "leakn" not in rec

    def test_spawner_gone_is_named_orphaned(self):
        rec = aegis.observe_table(_swarm_table(4, child_ppid=1), {}, CFG, 12)["record"]
        assert rec["spawn"] == "an exited parent (orphaned)"

    def test_generic_counts_skip_system_binaries_apps_and_the_headless_family(self):
        t = [LAUNCHD()]
        t += [P(100 + i, 1, "Code Helper (Plugin)", exe="/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Plugin).app/Contents/MacOS/Code Helper (Plugin)") for i in range(30)]
        t += [P(200 + i, 1, "distnoted", exe="/usr/sbin/distnoted") for i in range(20)]
        t += [P(300 + i, 1, "zsh", exe="/bin/zsh") for i in range(28)]
        t += [P(400 + i, 1, "esbuild", exe="/Users/neo/proj/node_modules/@esbuild/darwin-arm64/bin/esbuild") for i in range(9)]
        t += _swarm_table(12)[3:]
        assert aegis.name_counts(t, 8) == {"esbuild": 9}

    def test_leak_names_are_summarised(self):
        astate = {}
        aegis.observe_table(_swarm_table(3), astate, CFG, 12)
        rec = aegis.observe_table(_swarm_table(3, child_ppid=1)[:2] + _swarm_table(3, child_ppid=1)[3:], astate, CFG, 12)["record"]
        assert rec["orph"] == [3, 3] and rec["leakn"] == ["Google Chrome for Testing x3"]


# ── Detectors ────────────────────────────────────────────────────────────

def _find(findings, key):
    return next((f for f in findings if f.key == key), None)


class TestDetectors:
    def test_swap_climbing_alerts_before_saturation_with_eta(self):
        r = rows([3.0 + 0.3 * i for i in range(10)], toprss=[["Google Chrome for Testing", 4300]])
        f = _find(aegis.analyze(r, CFG, NOW), "swap_trend")
        assert f is not None and f.level == "high"
        assert "Swap 5.7 GB" in f.msg and "+0.30 GB/min" in f.msg and "~8 min" in f.msg
        assert "Google Chrome for Testing 4.2 GB" in f.msg

    def test_swap_high_but_flat_is_quiet(self):
        assert _find(aegis.analyze(rows([7.0] * 10), CFG, NOW), "swap_trend") is None

    def test_swap_climbing_but_still_low_is_quiet(self):
        assert _find(aegis.analyze(rows([0.5 + 0.2 * i for i in range(10)]), CFG, NOW), "swap_trend") is None

    def test_swap_already_past_critical_is_owned_by_the_absolute_alert(self):
        assert _find(aegis.analyze(rows([9.0 + 0.5 * i for i in range(10)]), CFG, NOW), "swap_trend") is None

    def test_memory_pressure_climbing(self):
        f = _find(aegis.analyze(rows([70.0 + 1.5 * i for i in range(10)], key="ram"), CFG, NOW), "ram_trend")
        assert f is not None and "RAM 84%" in f.msg and "~4 min" in f.msg
        assert _find(aegis.analyze(rows([84.0] * 10, key="ram"), CFG, NOW), "ram_trend") is None

    def test_load_catastrophe_needs_sustained_samples_and_names_offenders(self):
        top = [["node", 312.0, 4242], ["Google Chrome for Testing", 140.0, 5000]]
        r = rows([5.0] * 7 + [30.0, 31.0, 32.0], key="load1", topn=top)
        f = _find(aegis.analyze(r, CFG, NOW), "load")
        assert f is not None and f.level == "critical"
        assert "32.0 on 12 cores" in f.msg and "node 312%" in f.msg and "Google Chrome for Testing 140%" in f.msg
        r2 = rows([5.0] * 8 + [30.0, 31.0], key="load1", topn=top)
        assert _find(aegis.analyze(r2, CFG, NOW), "load") is None

    def test_headless_swarm_growth_names_the_spawner_and_never_kills(self):
        fam = [{"headless": 24}] * 5 + [{"headless": c} for c in (30, 40, 50, 55, 59)]
        r = rows(fam, key="fam", spawn="node shoot.mjs (pid 4242)")
        f = _find(aegis.analyze(r, CFG, NOW), "swarm:headless")
        assert f is not None
        assert "59 headless browser processes (+35 in 5 min)" in f.msg
        assert "node shoot.mjs (pid 4242)" in f.msg and "Not auto-killed" in f.msg

    def test_headless_steady_below_max_is_quiet_and_above_max_is_not(self):
        assert _find(aegis.analyze(rows([{"headless": 30}] * 10, key="fam"), CFG, NOW), "swarm:headless") is None
        assert _find(aegis.analyze(rows([{"headless": 45}] * 10, key="fam"), CFG, NOW), "swarm:headless") is not None

    def test_generic_executable_swarm(self):
        r = rows([{"esbuild": 10}] * 5 + [{"esbuild": 30}] * 5, key="pc")
        f = _find(aegis.analyze(r, CFG, NOW), "swarm:esbuild")
        assert f is not None and "30 'esbuild' processes (+20 in 5 min)" in f.msg
        assert _find(aegis.analyze(rows([{"esbuild": 30}] * 10, key="pc"), CFG, NOW), "swarm:esbuild") is None

    def test_no_growth_claim_against_old_format_history(self):
        # Right after the upgrade the history has no process fields: "0 -> 30"
        # must not be fabricated into a swarm.
        r = rows([{}] * 9 + [{"headless": 30}], key="fam")
        for old in r[:9]:
            for k in ("fam", "pc", "orph", "topn", "toprss", "ncpu"):
                old.pop(k, None)
        assert [f.key for f in aegis.analyze(r, CFG, NOW)] == []
        t = aegis.trends(r, CFG, NOW)
        assert t["swarm"]["headless"]["growth"] == 0 and t["leaks"]["growth"] == 0

    def test_leaks_alert_threshold_growth_and_reap_hint(self):
        r = rows([[10, 0]] * 5 + [[10, 3]] * 5, key="orph", leakn=["Google Chrome for Testing x3"])
        f = _find(aegis.analyze(r, CFG, NOW), "leaks")
        assert f is not None and "3 leaked dev processes" in f.msg and "+3 over the window" in f.msg
        assert "Google Chrome for Testing x3" in f.msg and "Auto-reap is OFF" in f.msg
        on = _find(aegis.analyze(r, {**CFG, "auto_reap_orphans": True}, NOW), "leaks")
        assert "Auto-reap is ON" in on.msg
        assert _find(aegis.analyze(rows([[10, 2]] * 10, key="orph"), CFG, NOW), "leaks") is None

    def test_no_history_no_findings(self):
        assert aegis.analyze([], CFG, NOW) == []

    def test_detectors_have_no_kill_path(self):
        src = "".join(inspect.getsource(fn) for fn in (aegis.analyze, aegis.trends, aegis.detect_swarm,
                                                       aegis.detect_leaks, aegis.detect_load,
                                                       aegis.detect_swap_trend, aegis.detect_memory_trend))
        # The alert TEXT may say "not auto-killed"; the code must contain no
        # signalling primitive at all.
        for token in ("os.kill", ".kill(", ".terminate(", "_terminate(", "send_signal", "SIGTERM", "SIGKILL", "reap_leaks"):
            assert token not in src, token

    def test_swarm_growth_label_uses_the_real_span_when_history_is_short(self):
        # Three samples two minutes apart: the label must not claim the 5-min window.
        r = rows([{"headless": 5}, {"headless": 5}, {"headless": 45}], key="fam", spawn="node shoot.mjs (pid 1)")
        f = _find(aegis.analyze(r, CFG, NOW), "swarm:headless")
        assert f is not None and "(+40 in 2 min)" in f.msg
        assert aegis.growth_span(aegis.trends(r, CFG, NOW), CFG) == 2
        long = rows([{"headless": 5}] * 5 + [{"headless": 45}] * 5, key="fam")
        assert "(+40 in 5 min)" in _find(aegis.analyze(long, CFG, NOW), "swarm:headless").msg
        assert aegis.growth_span({}, CFG) == 5                       # no span known: the window

    def test_trends_tolerates_null_and_misshapen_fields(self):
        r = rows([1.0] * 4)
        r[0]["orph"] = [None, None]
        r[-1].update({"orph": None, "toprss": [None, ["x"], ["Chrome", "big"], ["Chrome", 512]],
                      "topn": [["node"], None, ["node", 312.0, 4242]], "fam": "junk", "pc": None,
                      "leakn": "junk", "ncpu": "many", "load1": None, "spawn": None, "swap_gb": "n/a"})
        t = aegis.trends(r, CFG, NOW)
        assert t["toprss"] == [["Chrome", 512]] and t["topn"] == [["node", 312.0, 4242]]
        assert t["swarm"] == {} and t["names"] == {} and t["spawn"] == ""
        assert t["leaks"] == {"p1": 0, "leak": 0, "growth": 0, "names": []}
        assert t["swap"]["gb"] == 0.0 and t["load"]["load1"] == 0.0
        assert aegis.analyze(r, CFG, NOW) == []
        r[-1]["ts"] = None                                          # even the timestamp
        assert aegis.analyze(r, CFG, NOW) == []


# ── Sentinel firing + cooldowns ──────────────────────────────────────────

class TestFiring:
    def test_findings_fire_once_per_cooldown(self, monkeypatch):
        notes = []
        monkeypatch.setattr(sentinel, "_notify", lambda t, m: notes.append((t, m)))
        astate = {}
        f = [Finding("swap_trend", "swap climbing", "msg", 900, "high")]
        assert sentinel._fire_findings(f, astate, 1000) == [("swap_trend", "msg")]
        assert sentinel._fire_findings(f, astate, 1500) == []
        assert sentinel._fire_findings(f, astate, 1901) == [("swap_trend", "msg")]
        assert notes == [("AegisForge: swap climbing", "msg")] * 2


# ── Auto-reap invariants ─────────────────────────────────────────────────

def _proven_leak_state(n=1, child_cpu=0.0):
    """A proven, idle leak observed for 6 samples (streak 5 >= reap_idle_samples)."""
    astate = {}
    aegis.observe_table(_swarm_table(n), astate, CFG, 12)
    forge = None
    for _ in range(5):
        forge = aegis.observe_table(_swarm_table(n, child_ppid=1, child_cpu=child_cpu)[:2] + _swarm_table(n, child_ppid=1, child_cpu=child_cpu)[3:], astate, CFG, 12)
    return forge, astate


class TestReapInvariants:
    def test_default_is_notify_only(self):
        assert sentinel.DEFAULTS["auto_reap_orphans"] is False

    def test_default_config_never_reaps_even_proven_idle_leaks(self, monkeypatch, no_signals):
        forge, astate = _proven_leak_state()
        assert len(forge["leaks"]) == 1 and astate["leak_streak"] == {"5000": 5}
        assert aegis.reap_leaks(forge["leaks"], CFG, astate, NOW) == []
        assert "_reap" not in astate

    def test_opt_in_reaps_only_the_proven_idle_leak_with_sigterm(self, monkeypatch):
        forge, astate = _proven_leak_state()
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: False)
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        done = aegis.reap_leaks(forge["leaks"], {**CFG, "auto_reap_orphans": True}, astate, NOW)
        assert sent == [(5000, signal.SIGTERM)]
        assert done and done[0][0] == "auto_reap" and "Google Chrome for Testing" in done[0][1]
        assert astate["_reap"] == NOW

    @pytest.mark.parametrize("why,patch", [
        ("busy", {"busy": True}),
        ("short idle streak", {"streak": 2}),
        ("established connection", {"established": True}),
        ("connections unknown", {"established": None}),
        ("no longer under PID 1 at signal time", {"still": False}),
        ("family not allowed", {"families": ["python"]}),
    ])
    def test_every_guard_blocks_the_reap(self, monkeypatch, no_signals, why, patch):
        forge, astate = _proven_leak_state()
        leaks = [dict(L) for L in forge["leaks"]]
        cfg = {**CFG, "auto_reap_orphans": True}
        if "busy" in patch:
            leaks[0]["busy"] = True
        if "streak" in patch:
            astate["leak_streak"] = {"5000": patch["streak"]}
        if "families" in patch:
            cfg["reap_families"] = patch["families"]
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: patch.get("established", False))
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: patch.get("still", True))
        assert aegis.reap_leaks(leaks, cfg, astate, NOW) == [], why

    def test_cooldown_and_cap(self, monkeypatch):
        forge, astate = _proven_leak_state(n=5)
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(pid))
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: False)
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        cfg = {**CFG, "auto_reap_orphans": True, "reap_max": 2}
        assert len(aegis.reap_leaks(forge["leaks"], cfg, astate, NOW)[0][1]) > 0 and len(sent) == 2
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW + 100) == [] and len(sent) == 2  # cooldown

    def test_names_user_script_classification(self):
        home = os.path.expanduser("~")
        assert aegis.names_user_script({"cmd": "python " + os.path.join(home, "proj", "loop.py")}) is True
        assert aegis.names_user_script({"cmd": "node " + os.path.join(home, "cron.mjs")}) is True
        assert aegis.names_user_script({"cmd": "python scheduler.py"}) is True   # RELATIVE argv (nohup case)
        assert aegis.names_user_script({"cmd": "node cron.mjs --once"}) is True
        # a script anywhere is spared (safe direction) -- the reaper cannot prove it is not a worker
        assert aegis.names_user_script({"cmd": "python /usr/local/bin/tool.py"}) is True
        # a leaked headless browser under an automation cache path stays the intended auto target
        assert aegis.names_user_script({"cmd": home + "/.cache/puppeteer/chrome-linux/chrome --headless"}) is False
        # splitext, not substring: a version-managed interpreter with NO script arg is NOT exempted
        assert aegis.names_user_script({"cmd": home + "/.pyenv/versions/3.12.0/bin/python -c import time;time.sleep(9)"}) is False
        assert aegis.names_user_script({"cmd": home + "/.rbenv/shims/ruby -e sleep"}) is False
        assert aegis.names_user_script({"cmd": "ssh -i " + home + "/.ssh/id_rsa host"}) is False
        assert aegis.names_user_script({"cmd": "next-server"}) is False

    def test_auto_spares_a_socketless_home_script_worker_but_manual_reaps_it(self, monkeypatch):
        # The audit scenario: `nohup python scheduler.py &`, terminal closed ->
        # PID 1, proven-dead parent, no socket, idle between 15-min runs. A bounded
        # idle streak cannot tell it from a hung leak, so AUTO leaves it for the human.
        # the literal audit scenario: argv carries only the RELATIVE script name
        leak = {"pid": 5000, "ppid": 1, "name": "python3.12", "cmd": "python scheduler.py",
                "exe": "python", "ct": 1000.0, "cpu": 0.0, "rss": 0, "user": "neo",
                "status": "running", "tty": None, "proof": "parent 4000 exited",
                "busy": False, "family": "python"}
        cfg = {**CFG, "auto_reap_orphans": True, "reap_idle_samples": 1}
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: False)   # no socket
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        astate = {"leak_streak": {"5000": 5}}
        r = aegis.reap_leaks([dict(leak)], cfg, astate, NOW, auto=True)
        assert sent == [] and r and r[0][0] == "orphan_workers" and "reap-orphans" in r[0][1]
        assert "_reap" not in astate                        # nothing was signalled
        # The human confirming (manual --reap-orphans) reaps the very same leak.
        astate2 = {"leak_streak": {"5000": 5}}
        r2 = aegis.reap_leaks([dict(leak)], cfg, astate2, NOW, auto=False)
        assert sent == [(5000, signal.SIGTERM)] and r2 and r2[0][0] == "auto_reap"

    def test_swarm_runaway_never_signals_spawner_or_children(self, monkeypatch, no_signals):
        # The "shoot.mjs spawned 24 -> 59 Chrome" incident, auto-reap even ON:
        # a live spawner with live children is a workload. Alert, never kill.
        cfg = {**CFG, "auto_reap_orphans": True, "reap_idle_samples": 1}
        astate = {}
        aegis.observe_table(_swarm_table(24), astate, cfg, 12)
        forge = aegis.observe_table(_swarm_table(59), astate, cfg, 12)
        assert forge["leaks"] == [] and forge["cands"] == []
        r = rows([{"headless": 24}] * 5 + [{"headless": 59}] * 5, key="fam", spawn=forge["record"]["spawn"])
        f = _find(aegis.analyze(r, cfg, NOW), "swarm:headless")
        assert f is not None and "node shoot.mjs (pid 4000)" in f.msg
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW) == []

    def test_spawner_whose_shell_died_is_not_reaped_while_its_children_work(self, monkeypatch, no_signals):
        cfg = {**CFG, "auto_reap_orphans": True, "reap_idle_samples": 1}
        astate = {}
        aegis.observe_table(_swarm_table(10), astate, cfg, 12)
        t = [LAUNCHD(), P(4000, 1, "node", cmd=f"{NODE} /Users/neo/proj/shoot.mjs", exe=NODE, cpu=0.0)]
        t += _swarm_table(10, child_cpu=8.0)[3:]           # children still rendering
        forge = aegis.observe_table(t, astate, cfg, 12)
        assert [L["pid"] for L in forge["leaks"]] == [4000] and forge["leaks"][0]["busy"] is True
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW) == []

    def test_protected_processes_are_never_leaks_even_when_reparented(self, no_signals):
        t1 = [LAUNCHD(), P(600, 1, "Code", exe="/Applications/Visual Studio Code.app/Contents/MacOS/Electron"),
              P(3000, 900, "zsh", exe="/bin/zsh"), P(3100, 900, "brew", exe="/opt/homebrew/bin/brew"),
              P(700, 600, "Code Helper (Renderer)", exe="/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/Contents/MacOS/Code Helper (Renderer)"),
              P(701, 3000, "claude", exe="/Users/neo/.vscode/extensions/anthropic.claude-code-2.1.281-darwin-arm64/resources/native-binary/claude"),
              P(702, 3000, "node", cmd=f"{NODE} /Users/neo/tools/tradingview-mcp/server.js", exe=NODE),
              P(710, 3100, "ollama", cmd="/opt/homebrew/bin/ollama serve", exe="/opt/homebrew/bin/ollama"),
              P(720, 3000, "next-server (v15)", cmd=f"{NODE} /Users/neo/proj/node_modules/next/dist/server/lib/start-server.js", exe=NODE, tty="/dev/ttys001")]
        astate = {}
        aegis.observe_table(t1, astate, CFG, 12)
        t2 = [LAUNCHD()] + [dict(p, ppid=1) for p in t1 if p["pid"] in (700, 701, 702, 710, 720)]
        forge = aegis.observe_table(t2, astate, CFG, 12)
        assert forge["leaks"] == [] and forge["cands"] == []
        assert aegis.reap_leaks(forge["leaks"], {**CFG, "auto_reap_orphans": True}, astate, NOW) == []

    def test_terminate_uses_sigterm_never_sigkill(self, monkeypatch):
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append(sig))
        assert aegis._terminate(424242) is True
        assert sent == [signal.SIGTERM]
        assert "signal.SIGKILL" not in inspect.getsource(aegis._terminate)
        assert "signal.SIGKILL" not in inspect.getsource(aegis.reap_leaks)

    def test_enable_disable_auto_keep_reap_explicit(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sentinel, "CONF", tmp_path / "sentinel.conf")
        monkeypatch.setattr(sentinel, "_purge_nopasswd_ready", lambda: True)
        sentinel.enable_auto()
        assert sentinel._conf()["auto_reap_orphans"] is False
        sentinel.enable_auto(reap=True)
        assert sentinel._conf()["auto_reap_orphans"] is True
        sentinel.enable_auto()                       # re-running without --reap turns it back off
        assert sentinel._conf()["auto_reap_orphans"] is False
        sentinel.enable_auto(aggressive=True, reap=True)
        sentinel.disable_auto()
        c = sentinel._conf()
        assert c["auto_reap_orphans"] is False and c["auto_trim_fleet"] is False and c["auto_purge"] is False


# ── In-service check: LISTEN / unix sockets (the reap safety line) ───────

def _conn(status, family=socket.AF_INET, laddr=("127.0.0.1", 3000)):
    return types.SimpleNamespace(status=status, family=family, laddr=laddr, raddr=())


def _fake_process_table(monkeypatch, conns_by_pid: dict, children=()):
    """psutil.Process stand-in: connections per pid (or an exception to raise),
    `children` as the root's descendants. Records the socket `kind` asked for."""
    kinds = []

    class _P:
        def __init__(self, pid):
            if pid not in conns_by_pid:
                raise psutil.NoSuchProcess(pid)
            self.pid = pid

        def children(self, recursive=False):
            return [_P(c) for c in children]

        def net_connections(self, kind="inet"):
            kinds.append(kind)
            v = conns_by_pid[self.pid]
            if isinstance(v, Exception):
                raise v
            return v

    monkeypatch.setattr(aegis.psutil, "Process", _P)
    return kinds


class TestInService:
    def test_a_listening_dev_server_is_in_service(self, monkeypatch):
        _fake_process_table(monkeypatch, {7: [_conn(psutil.CONN_LISTEN)]})
        assert aegis._in_service(7, "node") is True
        assert aegis._in_service(7, "python") is True
        assert aegis._in_service(7, None) is True          # unknown family: the safe reading

    def test_headless_stays_reapable_while_only_listening(self, monkeypatch):
        # puppeteer's --remote-debugging-port is what a leaked automation
        # browser looks like, not a service anyone uses
        _fake_process_table(monkeypatch, {7: [_conn(psutil.CONN_LISTEN, laddr=("127.0.0.1", 9222))]})
        assert aegis._in_service(7, "headless") is False

    def test_established_counts_for_every_family(self, monkeypatch):
        _fake_process_table(monkeypatch, {7: [_conn(psutil.CONN_ESTABLISHED)]})
        assert aegis._in_service(7, "headless") is True
        assert aegis._in_service(7, "node") is True

    def test_bound_unix_socket_is_a_service_unnamed_is_plumbing(self, monkeypatch):
        unix = getattr(socket, "AF_UNIX", None)
        if unix is None:
            pytest.skip("no AF_UNIX on this platform")
        _fake_process_table(monkeypatch, {7: [_conn(psutil.CONN_NONE, family=unix, laddr="/tmp/app.sock")],
                                          8: [_conn(psutil.CONN_NONE, family=unix, laddr="")]})
        assert aegis._in_service(7, "python") is True
        assert aegis._in_service(7, "headless") is False
        assert aegis._in_service(8, "node") is False        # socketpair IPC, no name

    def test_descendant_sockets_count(self, monkeypatch):
        _fake_process_table(monkeypatch, {7: [], 8: [_conn(psutil.CONN_LISTEN)]}, children=(8,))
        assert aegis._in_service(7, "node") is True

    def test_unknown_is_none_and_every_socket_kind_is_asked_for(self, monkeypatch):
        kinds = _fake_process_table(monkeypatch, {7: psutil.AccessDenied(7), 9: []})
        assert aegis._in_service(7, "node") is None
        assert aegis._in_service(424242, "node") is None    # NoSuchProcess
        assert aegis._in_service(9, "node") is False
        assert kinds and set(kinds) == {"all"}

    def test_reap_spares_a_listening_node_leak_and_still_reaps_a_listening_headless_leak(self, monkeypatch):
        cfg = {**CFG, "auto_reap_orphans": True}
        # A `node server.js` whose shell died: proven leak, idle for 5 samples,
        # LISTEN only -- a backgrounded dev server between two requests.
        t1 = [LAUNCHD(), P(3000, 900, "zsh", exe="/bin/zsh"), P(4100, 3000, "node", cmd=f"{NODE} server.js", exe=NODE)]
        t2 = [LAUNCHD(), P(4100, 1, "node", cmd=f"{NODE} server.js", exe=NODE)]
        astate = {}
        aegis.observe_table(t1, astate, cfg, 12)
        forge = None
        for _ in range(5):
            forge = aegis.observe_table(t2, astate, cfg, 12)
        assert [(L["pid"], L["family"]) for L in forge["leaks"]] == [(4100, "node")]
        assert astate["leak_streak"] == {"4100": 5}
        sent = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        _fake_process_table(monkeypatch, {4100: [_conn(psutil.CONN_LISTEN)]})
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW) == []
        assert sent == [] and "_reap" not in astate
        # The same LISTEN-only picture on a leaked puppeteer Chrome is reapable.
        forge_h, astate_h = _proven_leak_state()
        _fake_process_table(monkeypatch, {5000: [_conn(psutil.CONN_LISTEN, laddr=("127.0.0.1", 9222))]})
        done = aegis.reap_leaks(forge_h["leaks"], cfg, astate_h, NOW)
        assert sent == [(5000, signal.SIGTERM)] and done and done[0][0] == "auto_reap"

    def test_reap_asks_the_check_with_the_candidates_family(self, monkeypatch, no_signals):
        forge, astate = _proven_leak_state()
        asked = []
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: asked.append((pid, fam)) or True)
        assert aegis.reap_leaks(forge["leaks"], {**CFG, "auto_reap_orphans": True}, astate, NOW) == []
        assert asked == [(5000, "headless")]


# ── Config coercion: flags and lists from a hand-edited JSON conf ────────

class TestConfigCoercion:
    @pytest.mark.parametrize("raw", ["false", "False", " off ", "0", "", "no", None, 0, 0.0])
    def test_false_words_are_false(self, raw):
        assert aegis._bool({"auto_reap_orphans": raw}, "auto_reap_orphans") is False

    @pytest.mark.parametrize("raw", [True, "true", "TRUE", "yes", "on", "1", 1])
    def test_true_words_are_true(self, raw):
        assert aegis._bool({"k": raw}, "k") is True

    def test_unrecognised_is_the_default(self):
        assert aegis._bool({"k": "maybe"}, "k") is False
        assert aegis._bool({"k": ["x"]}, "k") is False
        assert aegis._bool({}, "k") is False and aegis._bool({}, "k", default=True) is True

    @pytest.mark.parametrize("raw", ["false", "off", "0", ""])
    def test_string_false_never_enables_auto_reap(self, monkeypatch, no_signals, raw):
        forge, astate = _proven_leak_state()
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: False)
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        cfg = {**CFG, "auto_reap_orphans": raw}
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW) == []
        f = _find(aegis.analyze(rows([[10, 3]] * 10, key="orph"), cfg, NOW), "leaks")
        assert "Auto-reap is OFF" in f.msg

    def test_reap_families_missing_explicit_and_malformed(self):
        assert aegis.reap_families({}) == ["headless", "node", "python"]
        assert aegis.reap_families({"reap_families": None}) == ["headless", "node", "python"]
        assert aegis.reap_families({"reap_families": ["node", 5, None]}) == ["node"]
        assert aegis.reap_families({"reap_families": []}) == []
        for raw in ("node", "headless,node", {"headless": True}, 3, True):
            assert aegis.reap_families({"reap_families": raw}) == [], raw

    @pytest.mark.parametrize("raw", ["headless", {"headless": True}, 3])
    def test_malformed_reap_families_reaps_nothing(self, monkeypatch, no_signals, raw):
        forge, astate = _proven_leak_state()
        monkeypatch.setattr(aegis, "_in_service", lambda pid, fam: False)
        monkeypatch.setattr(aegis, "_still_leaked", lambda pid, ct: True)
        cfg = {**CFG, "auto_reap_orphans": True, "reap_families": raw}
        assert aegis.reap_leaks(forge["leaks"], cfg, astate, NOW) == []

    def test_string_false_never_enables_trim_purge_or_unload(self, monkeypatch):
        ns = types.SimpleNamespace
        vm, sw = ns(percent=97.0), ns(used=12e9)          # critical on both counts
        sessions = [{"pid": 100 + i, "cpu": 0.0, "rss": 0, "start": i} for i in range(8)]
        streaks = {str(s["pid"]): 99 for s in sessions}
        oll = {"gb": 6.0, "models": ["llama3"], "busy": False}

        def boom(*a, **k):
            raise AssertionError("remediation acted on a string 'false'")
        monkeypatch.setattr(sentinel, "_trim_fleet", boom)
        monkeypatch.setattr(sentinel, "_unload_ollama", boom)
        monkeypatch.setattr(sentinel.subprocess, "run", boom)
        monkeypatch.setattr(sentinel, "_notify", lambda t, m: None)
        cfg = {**CFG, "auto_trim_fleet": "false", "auto_purge": "off", "auto_unload_ollama": "0"}
        assert sentinel._remediate(vm, sw, cfg, {}, NOW, sessions, streaks, oll) == []
        # control: a real True does trim, so the coercion is what blocked it above
        called = []
        monkeypatch.setattr(sentinel, "_trim_fleet", lambda *a, **k: called.append(a) or [101, 102])
        done = sentinel._remediate(vm, sw, {**CFG, "auto_trim_fleet": True}, {}, NOW, sessions, streaks, oll)
        assert called and [k for k, _ in done] == ["auto_trim"]


# ── Sentinel integration (hermetic run_sample) ───────────────────────────

def _redirect(monkeypatch, tmp_path):
    for name in ("METRICS", "ASTATE", "SEQ", "CONF", "ALERTS_LOG"):
        monkeypatch.setattr(sentinel, name, tmp_path / name.lower())
    monkeypatch.setattr(sentinel, "MACMON_DIR", tmp_path)


def _hermetic_sample(monkeypatch, tmp_path, swap_used=5.5e9, forge=None):
    """run_sample() with every probe stubbed (no process scan, no ping, no
    ollama, no notifier). Returns the list that collects notifications."""
    _redirect(monkeypatch, tmp_path)
    ns = types.SimpleNamespace
    ps = sentinel.psutil
    monkeypatch.setattr(ps, "process_iter", lambda *a, **k: [])
    monkeypatch.setattr(ps, "cpu_percent", lambda interval=None: 12.0)
    monkeypatch.setattr(ps, "virtual_memory", lambda: ns(percent=80.0))
    monkeypatch.setattr(ps, "swap_memory", lambda: ns(used=swap_used))
    monkeypatch.setattr(ps, "disk_usage", lambda p: ns(free=100e9))
    monkeypatch.setattr(sentinel, "load_average", lambda: (1.0, 1.0, 1.0))
    # The probes take the shared process table (procs=None when standalone).
    monkeypatch.setattr(sentinel, "_ai_fleet", lambda procs=None: {"claude": [0, 0], "codex": [0, 0], "mcp": [0, 0]})
    monkeypatch.setattr(sentinel, "_top_proc", lambda procs=None: ("x", 1.0, 10))
    monkeypatch.setattr(sentinel, "_ping_rtt", lambda: None)
    monkeypatch.setattr(sentinel, "_ollama_status", lambda procs=None: {"gb": 0.0, "models": [], "busy": False})
    monkeypatch.setattr(sentinel, "_vm_status", lambda procs=None: {"gb": 0.0, "owner": ""})
    monkeypatch.setattr(sentinel, "_claude_sessions", lambda procs=None: [])
    forge = forge or {"record": {"ncpu": 12, "fam": {}, "pc": {}, "orph": [0, 0], "topn": [], "toprss": []},
                      "leaks": [], "cands": [], "streaks": {}}
    monkeypatch.setattr(sentinel.aegis, "observe", lambda astate, cfg, procs=None: forge)
    notes = []
    monkeypatch.setattr(sentinel, "_notify", lambda t, m: notes.append((t, m)))
    return notes


class TestSentinelIntegration:
    def test_run_sample_records_forge_fields_fires_branded_alert_and_never_signals(self, tmp_path, monkeypatch, no_signals):
        forge = {"record": {"ncpu": 12, "fam": {"headless": 59}, "pc": {}, "orph": [0, 0], "topn": [], "toprss": [],
                            "spawn": "node shoot.mjs (pid 4242)"}, "leaks": [], "cands": [], "streaks": {}}
        notes = _hermetic_sample(monkeypatch, tmp_path, forge=forge)
        now = int(time.time())
        with open(sentinel.METRICS, "w") as f:
            # flat swap/RAM history matching the sampled values: only the swarm may fire
            for r in rows([{"headless": 24}] * 6, key="fam", start=now - 6 * 60, swap_gb=5.5, ram=80.0):
                f.write(json.dumps(r) + "\n")

        sentinel.run_sample()

        last = sentinel._load_tail(1)[-1]
        assert last["fam"] == {"headless": 59} and last["spawn"] == "node shoot.mjs (pid 4242)" and last["ncpu"] == 12
        assert notes == [("AegisForge: process swarm",
                          "59 headless browser processes (+35 in 5 min), spawned by node shoot.mjs (pid 4242). "
                          "Not auto-killed (live workload) -- stop the spawner yourself if unintended.")]
        astate = json.loads(sentinel.ASTATE.read_text())
        assert astate["swarm:headless"] == now and "_reap" not in astate
        assert "swarm:headless:" in sentinel.ALERTS_LOG.read_text()

    def test_load_tail_reads_only_the_tail_of_a_big_file(self, tmp_path, monkeypatch):
        _redirect(monkeypatch, tmp_path)
        with open(sentinel.METRICS, "w") as f:
            for r in rows([1.0] * 3000, start=NOW, pc={"padding": 1}, topn=[["x" * 200, 1.0, 1]]):
                f.write(json.dumps(r) + "\n")
        assert sentinel.METRICS.stat().st_size > 256 * 1024
        got = sentinel._load_tail(5)
        assert [r["ts"] for r in got] == [NOW + (2995 + i) * 60 for i in range(5)]

    def test_tail_lines_reads_only_the_tail_of_the_alerts_log(self, tmp_path):
        p = tmp_path / "alerts.log"
        with open(p, "w") as f:
            for i in range(5000):
                f.write(f"2026-09-29 00:00:00  k: line {i} {'x' * 30}\n")
        assert p.stat().st_size > 64 * 1024
        got = sentinel._tail_lines(p, 3)
        assert [ln.split("line ")[1].split()[0] for ln in got] == ["4997", "4998", "4999"]
        assert sentinel._tail_lines(tmp_path / "none.log", 3) == [] and sentinel._tail_lines(p, 0) == []
        assert "read_text" not in inspect.getsource(sentinel._tail_alerts)


class TestRunSampleResilience:
    """A bad history row or a psutil hiccup in one stage must not abort the
    sample before ASTATE is persisted: the cooldown of the alert that just
    fired would be lost (it re-fires every minute) and every streak reset."""

    @pytest.mark.parametrize("stage,holder,name", [
        ("anticipation", aegis, "analyze"),
        ("remediation", sentinel, "_remediate"),
        ("auto-reap", aegis, "reap_leaks"),
    ])
    def test_a_failing_stage_never_loses_state_and_is_logged(self, tmp_path, monkeypatch, no_signals, stage, holder, name):
        notes = _hermetic_sample(monkeypatch, tmp_path, swap_used=7e9)   # > swap_used_gb: the absolute alert fires

        def boom(*a, **k):
            raise RuntimeError(f"{stage} exploded")
        monkeypatch.setattr(holder, name, boom)

        sentinel.run_sample()
        assert notes and notes[0][0] == "AegisForge: high swap"
        astate = json.loads(sentinel.ASTATE.read_text())
        assert astate.get("swap")                                    # the cooldown stamp survived
        assert sentinel._load_tail(1)                                # the sample itself was recorded
        log = sentinel.ALERTS_LOG.read_text()
        assert "  swap: " in log
        assert f"  error: {stage} skipped this sample: RuntimeError('{stage} exploded')" in log
        n = len(notes)
        sentinel.run_sample()                                        # next minute: no re-fire
        assert len(notes) == n


class TestSampleCommand:
    """The scheduled job must point at something runnable. The frozen .app has
    no venv, no macmon.py, and its sys.executable IS the app -- so a Resume
    from the app must use the installed launcher or refuse, never register a
    job that fails every minute while reporting success."""

    def test_checkout_uses_its_own_venv(self, tmp_path, monkeypatch):
        venv_py, script = tmp_path / "python", tmp_path / "macmon.py"
        venv_py.write_text("")
        script.write_text("")
        monkeypatch.setattr(sentinel, "VENV_PY", venv_py)
        monkeypatch.setattr(sentinel, "MACMON_PY", script)
        assert sentinel._sample_cmd() == [str(venv_py), str(script), "sentinel", "--sample"]

    def test_installed_launcher_when_the_venv_is_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sentinel, "VENV_PY", tmp_path / "missing")
        monkeypatch.setattr(sentinel, "MACMON_PY", tmp_path / "macmon.py")
        monkeypatch.setattr(sentinel, "find_macmon", lambda: "/usr/local/bin/macmon")
        assert sentinel._sample_cmd() == ["/usr/local/bin/macmon", "sentinel", "--sample"]

    def test_dev_interpreter_fallback_is_never_the_frozen_app(self, tmp_path, monkeypatch):
        script = tmp_path / "macmon.py"
        script.write_text("")
        monkeypatch.setattr(sentinel, "VENV_PY", tmp_path / "missing")
        monkeypatch.setattr(sentinel, "MACMON_PY", script)
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)
        monkeypatch.delattr(sys, "frozen", raising=False)
        assert sentinel._sample_cmd() == [sys.executable, str(script), "sentinel", "--sample"]
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert sentinel._sample_cmd() == []

    def test_frozen_app_without_a_launcher_refuses_to_register_a_dead_job(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sentinel, "VENV_PY", tmp_path / "missing")
        monkeypatch.setattr(sentinel, "MACMON_PY", tmp_path / "missing.py")
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)

        def boom(*a, **k):
            raise AssertionError("the scheduler was touched with nothing runnable")
        monkeypatch.setattr(sentinel.subprocess, "run", boom)
        ok, note = sentinel._schedule_install()
        assert ok is False and "macmon sentinel --resume" in note

    def test_find_macmon_probes_path_then_the_well_known_homes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sentinel.shutil, "which", lambda name: None)
        monkeypatch.setattr(sentinel, "_MACMON_CANDIDATES", (str(tmp_path / "nope"), str(tmp_path / "macmon")))
        assert sentinel.find_macmon() is None
        # The well-known-homes fallback gates on os.access(X_OK) -- a POSIX exec
        # bit. On Windows executability is decided by extension (PATHEXT), so a
        # bare "macmon" is never X_OK there and find_macmon relies on which()
        # instead; those paths (/usr/local/bin ...) don't exist on Windows anyway.
        if os.name != "nt":
            launcher = tmp_path / "macmon"
            launcher.write_text("#!/bin/sh\n")
            launcher.chmod(0o755)
            assert sentinel.find_macmon() == str(launcher)
        monkeypatch.setattr(sentinel.shutil, "which", lambda name: "/on/path/macmon")
        assert sentinel.find_macmon() == "/on/path/macmon"


# ── Branding ─────────────────────────────────────────────────────────────

class TestBranding:
    @pytest.mark.parametrize("token,hexv", [
        ("ground", "#070A0F"), ("surface", "#10171F"), ("text", "#EEF3F8"), ("dim", "#8393A6"),
        ("mint", "#34E5A0"), ("ember", "#FF7A1A"), ("amber", "#FBBF24"), ("sky", "#7DD3FC"),
        ("critical", "#FB7185"), ("ok", "#34D399"),
    ])
    def test_palette_tokens(self, token, hexv):
        assert aegis.C[token] == hexv
        style = aegis.AEGIS_THEME.styles[f"aegis.{token}"]
        color = style.bgcolor if token in ("ground", "surface") else style.color
        assert color.triplet.hex.lower() == hexv.lower()

    def test_semantic_aliases(self):
        s = aegis.AEGIS_THEME.styles
        assert s["aegis.primary"].color.triplet.hex.lower() == "#34e5a0" and s["aegis.primary"].bold
        assert s["aegis.action"].color.triplet.hex.lower() == "#ff7a1a"
        assert s["aegis.warn"].color.triplet.hex.lower() == "#fbbf24"
        assert s["aegis.low"].color.triplet.hex.lower() == "#7dd3fc"

    def test_user_facing_brand_engine_untouched(self):
        assert sentinel.ALERT_TITLE == "AegisForge" == aegis.BRAND
        assert aegis.TAGLINE == "fleet health, forged" and aegis.ENGINE == "macmon"
        assert sentinel.MONITOR_LABEL == "co.soclose.macmon.monitor"     # scheduler identity unchanged
        assert sentinel.NOTIFIER_APP.name == "MacmonSentinel.app"        # bundle path/id unchanged (permission)

    def test_icon_prefers_aegisforge_and_falls_back_to_macmon(self, tmp_path, monkeypatch):
        a, m = tmp_path / "aegisforge.icns", tmp_path / "macmon.icns"
        monkeypatch.setattr(sentinel, "AEGIS_ICNS", a)
        monkeypatch.setattr(sentinel, "ICNS_SRC", m)
        assert sentinel._icon_source() is None
        m.write_bytes(b"icns")
        assert sentinel._icon_source() == m
        a.write_bytes(b"icns")
        assert sentinel._icon_source() == a

    def test_shipped_icon_is_a_real_icns(self):
        icns = sentinel.AEGIS_ICNS
        assert icns.exists() and icns.read_bytes()[:4] == b"icns"

    def test_notifier_brand_marker(self, tmp_path, monkeypatch):
        app = tmp_path / "MacmonSentinel.app"
        monkeypatch.setattr(sentinel, "NOTIFIER_APP", app)
        assert sentinel._notifier_branded() is False
        (app / "Contents/Resources").mkdir(parents=True)
        (app / "Contents/Resources/.brand").write_text("macmon-0")
        assert sentinel._notifier_branded() is False
        (app / "Contents/Resources/.brand").write_text(sentinel.NOTIFIER_BRAND)
        assert sentinel._notifier_branded() is True
