"""Tests for the AegisForge.app menu-bar layer (macmon_core.app_menubar).

Hermetic: the pure logic (severity ranking, title, menu body, the resilient
gather(), the action dispatch) runs with the engine monkeypatched -- no
sampling, no subprocess, no GUI. rumps is optional: the tests that drive a
real rumps Menu skip where rumps is absent (CI); everything else runs with
rumps=None as well.

Regressions locked here (adversarial audit of the first .app build):
  - findings rank by aegis.Finding.level (a `.severity` stand-in hid that
    every real finding ranked "info")
  - the live block is rebuilt in place each tick: no NSMenu growth, no
    'live:N' key shown as a title, identical lines do not collide
  - sentinel.conf is JSON and is read with the sentinel's loader (a TOML
    parse silently ignored the owner's thresholds)
  - frozen (.app) actions run the engine in-process and never spawn
    sys.executable (that is the app itself -- it relaunched AegisForge)
  - every action outcome is reported; nothing is swallowed
  - the dashboard command prefers the installed launcher and is shell-quoted
  - clean is confirmed before it runs; the sample age is shown, STALE past 180 s
"""
import inspect
import shlex
import sys
import threading
import types

import pytest

from macmon_core import app_menubar as mb
from macmon_core import sentinel
from macmon_core.aegis import Finding

NOW = 1_800_000_000.0


def F(level, msg, key="k"):
    """A real engine finding (the old `.severity` stand-in false-passed)."""
    return Finding(key, "title", msg, 900, level)


@pytest.fixture
def notes(monkeypatch):
    """Collect what the app tells the owner; nothing reaches a notifier."""
    out = []
    monkeypatch.setattr(mb, "_notify", lambda t, m: out.append((t, m)))
    return out


# ── severity / title ─────────────────────────────────────────────────────

class TestWorstSeverity:
    def test_empty_is_ok(self):
        assert mb.worst_severity([]) == "ok"

    def test_ranks_by_the_findings_level(self):
        fs = [F("low", "a"), F("critical", "b"), F("medium", "c")]
        assert mb.worst_severity(fs) == "critical"
        assert mb.worst_severity([F("medium", "m"), F("high", "h")]) == "high"
        assert [mb._finding_severity(f) for f in fs] == ["low", "critical", "medium"]

    def test_real_finding_never_ranks_info(self):
        assert mb._finding_severity(Finding("load", "load catastrophe", "x", 900, "critical")) == "critical"
        assert mb._finding_severity(Finding("k", "t", "x", 900)) == "medium"   # the dataclass default

    def test_foreign_spellings_still_rank(self):
        assert mb._finding_severity(types.SimpleNamespace(severity="High")) == "high"
        assert mb._finding_severity(types.SimpleNamespace(sev="low")) == "low"
        assert mb._finding_severity(object()) == "info"


class TestTitleFor:
    def test_healthy_is_icon_only(self):
        assert mb.title_for({"swap_gb": 2.0}, "ok") == ""

    def test_high_shows_swap(self):
        assert "27" in mb.title_for({"swap_gb": 27.4}, "high")

    def test_medium_is_mark_only_no_number(self):
        t = mb.title_for({"swap_gb": 27.4}, "medium")
        assert "27" not in t and t == mb._SEV_MARK["medium"]

    def test_mark_does_not_depend_on_swap(self):
        assert mb.title_for({}, "critical") == mb._SEV_MARK["critical"]
        assert mb.title_for({"swap_gb": None}, "high") == mb._SEV_MARK["high"]
        assert mb.title_for({}, "low") == mb._SEV_MARK["low"]


# ── menu body + sample age ───────────────────────────────────────────────

class TestMenuLines:
    SNAP = {"vitals": {"cpu": 22.7, "ram": 81.1, "swap_gb": 26.4, "load1": 7.5, "disk_free_gb": 61.2},
            "findings": [], "alerts": [], "age_s": 30.0}

    def test_vitals_rendered(self):
        lines = mb.menu_lines(self.SNAP)
        assert lines[0] == "sample 30s ago"
        assert any("CPU: 22.7%" in ln for ln in lines)
        assert any("Swap: 26.4 GB" in ln for ln in lines)
        assert any("All clear" in ln for ln in lines)

    def test_missing_vitals_show_placeholder(self):
        lines = mb.menu_lines({"vitals": {}, "findings": [], "alerts": []})
        assert lines[0] == "sample: none yet"
        assert any(ln == "CPU: --" for ln in lines)
        assert any(ln == "Swap: --" for ln in lines)

    def test_findings_and_alerts_rendered(self):
        snap = {"vitals": {}, "findings": [F("high", "Swap climbing +0.3 GB/min")],
                "alerts": ["2026-09-29 swap: Swap 27 GB"]}
        lines = mb.menu_lines(snap)
        assert any("[high] Swap climbing" in ln for ln in lines)
        assert any("Recent alerts:" in ln for ln in lines)
        assert any("Swap 27 GB" in ln for ln in lines)


class TestSampleAge:
    def test_age_text(self):
        assert mb.age_text(None) == "sample: none yet"
        assert mb.age_text(45) == "sample 45s ago"
        assert mb.age_text(130) == "sample 2m ago"
        assert mb.age_text(180) == "sample 3m ago"                  # STALE is strictly past 180 s
        assert mb.age_text(600) == "sample 10m ago / STALE"

    def test_sample_age(self):
        assert mb.sample_age({"ts": NOW - 30}, NOW) == 30
        assert mb.sample_age({"ts": NOW + 5}, NOW) == 0                # clock skew never goes negative
        assert mb.sample_age({"ts": None}, NOW) is None
        assert mb.sample_age({}, NOW) is None and mb.sample_age(None, NOW) is None


# ── gather (resilient; engine monkeypatched) ─────────────────────────────

def _quiet_engine(monkeypatch, rows):
    monkeypatch.setattr(mb.sentinel, "_load_tail", lambda n=16: rows)
    monkeypatch.setattr(mb.aegis, "trends", lambda r, c, now: {"points": len(r)})
    for det in ("detect_swap_trend", "detect_memory_trend", "detect_load", "detect_leaks"):
        monkeypatch.setattr(mb.aegis, det, lambda t, c: None)
    monkeypatch.setattr(mb.aegis, "detect_swarm", lambda t, c: [])
    monkeypatch.setattr(mb, "_recent_alerts", lambda n: [])
    monkeypatch.setattr(mb.time, "time", lambda: NOW)


class TestGather:
    def test_reads_latest_row_and_findings(self, monkeypatch):
        rows = [{"ts": NOW - 90, "cpu": 10, "ram": 70, "swap_gb": 20, "load1": 5, "disk_free_gb": 80},
                {"ts": NOW - 30, "cpu": 22.7, "ram": 81.1, "swap_gb": 26.4, "load1": 7.5, "disk_free_gb": 61.2}]
        _quiet_engine(monkeypatch, rows)
        monkeypatch.setattr(mb.aegis, "detect_swap_trend", lambda t, c: F("high", "swap up", "swap_trend"))
        snap = mb.gather()
        assert snap["error"] is None
        assert snap["vitals"]["swap_gb"] == 26.4     # latest row
        assert snap["worst"] == "high"               # from Finding.level
        assert len(snap["findings"]) == 1
        assert snap["age_s"] == 30
        assert mb.menu_lines(snap)[0] == "sample 30s ago"

    def test_stale_sample_is_flagged(self, monkeypatch):
        _quiet_engine(monkeypatch, [{"ts": NOW - 600, "cpu": 1, "ram": 1, "swap_gb": 1, "load1": 1, "disk_free_gb": 1}])
        snap = mb.gather()
        assert snap["age_s"] == 600
        assert mb.menu_lines(snap)[0] == "sample 10m ago / STALE"

    def test_never_raises_on_broken_sampler(self, monkeypatch):
        def _boom(n=16):
            raise RuntimeError("metrics unreadable")
        monkeypatch.setattr(mb.sentinel, "_load_tail", _boom)
        snap = mb.gather()
        assert snap["worst"] == "ok"           # safe default
        assert snap["error"] is not None       # but records what went wrong
        assert mb.menu_lines(snap)             # menu still renders from the safe snapshot


# ── config + alerts log ──────────────────────────────────────────────────

class TestConfig:
    def test_reads_the_sentinels_json_conf(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sentinel, "CONF", tmp_path / "sentinel.conf")
        sentinel._write_conf({"swap_critical_gb": 4.5, "auto_reap_orphans": True, "trend_window": 7})
        cfg = mb._cfg()
        assert cfg["swap_critical_gb"] == 4.5 and cfg["auto_reap_orphans"] is True and cfg["trend_window"] == 7
        assert cfg["ram_critical"] == sentinel.DEFAULTS["ram_critical"]     # defaults still underneath
        assert cfg == sentinel._conf()

    def test_not_parsed_as_toml(self):
        # A JSON object is not valid TOML: tomllib raised, the except swallowed
        # it, and the app silently ran on the defaults.
        assert "tomllib" not in inspect.getsource(mb)


class TestRecentAlerts:
    def test_tail_reads_a_big_log(self, tmp_path, monkeypatch):
        log = tmp_path / "sentinel_alerts.log"
        monkeypatch.setattr(sentinel, "ALERTS_LOG", log)
        with open(log, "w") as f:
            for i in range(4000):
                f.write(f"2026-09-29 10:00:00  swap: line {i} {'x' * 40}\n")
        assert log.stat().st_size > 64 * 1024
        got = mb._recent_alerts(3)
        assert [ln.split("line ")[1].split()[0] for ln in got] == ["3997", "3998", "3999"]
        assert all(ln == ln.strip() for ln in got)
        assert "read_text" not in inspect.getsource(mb._recent_alerts)

    def test_missing_log_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sentinel, "ALERTS_LOG", tmp_path / "missing.log")
        assert mb._recent_alerts(3) == []


# ── actions: wiring + confirmation ───────────────────────────────────────

class TestActionWiring:
    def test_clean_purge_pause_resume_args(self, monkeypatch):
        calls = []
        monkeypatch.setattr(mb, "_run_cli", lambda args: calls.append(args))
        monkeypatch.setattr(mb, "_confirm", lambda *a, **k: True)
        mb.clean_caches()
        mb.purge_ram()
        mb.pause_sentinel()
        mb.resume_sentinel()
        assert calls == [["clean", "--all", "-y"], ["purge"],
                         ["sentinel", "--pause"], ["sentinel", "--resume"]]

    def test_clean_is_confirmed_first_and_cancel_runs_nothing(self, monkeypatch):
        calls, asked = [], []
        monkeypatch.setattr(mb, "_run_cli", lambda args: calls.append(args))
        monkeypatch.setattr(mb, "_confirm", lambda title, msg, ok="OK": asked.append((title, ok)) or False)
        mb.clean_caches()
        assert asked == [("Clean caches?", "Clean")] and calls == []

    def test_confirm_is_a_rumps_alert_with_a_cancel_button(self, monkeypatch):
        seen = {}

        def alert(title, message, ok=None, cancel=None, **kw):
            seen.update(title=title, ok=ok, cancel=cancel)
            return seen.pop("answer")
        monkeypatch.setattr(mb, "rumps", types.SimpleNamespace(alert=alert))
        seen["answer"] = 1
        assert mb._confirm("Clean caches?", "msg", ok="Clean") is True
        assert seen["title"] == "Clean caches?" and seen["ok"] == "Clean" and seen["cancel"]
        seen["answer"] = 0
        assert mb._confirm("Clean caches?", "msg", ok="Clean") is False

    def test_confirm_without_rumps_refuses(self, monkeypatch):
        monkeypatch.setattr(mb, "rumps", None)
        assert mb._confirm("x", "y") is False

    def test_run_cli_runs_the_action_on_a_thread(self, monkeypatch):
        done = threading.Event()
        seen = []
        monkeypatch.setattr(mb, "_run_action", lambda args: (seen.append(args), done.set()))
        mb._run_cli(["purge"])
        assert done.wait(5) and seen == [["purge"]]


class TestNotify:
    def test_falls_back_to_the_sentinel_notifier(self, monkeypatch):
        sent = []

        def broken(*a, **k):
            raise RuntimeError("no bundle identifier")
        monkeypatch.setattr(mb, "rumps", types.SimpleNamespace(notification=broken))
        monkeypatch.setattr(sentinel, "_notify", lambda t, m: sent.append((t, m)))
        mb._notify("t", "m")
        assert sent == [("t", "m")]
        monkeypatch.setattr(mb, "rumps", None)
        mb._notify("t2", "m2")
        assert sent[-1] == ("t2", "m2")

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(mb, "rumps", None)

        def boom(*a, **k):
            raise RuntimeError("osascript missing")
        monkeypatch.setattr(sentinel, "_notify", boom)
        mb._notify("t", "m")


# ── actions: the frozen .app runs the engine in-process ──────────────────

class TestFrozenActions:
    """The PyInstaller .app: sys.executable is the app and macmon.py is not in
    the bundle. `[sys.executable, macmon.py, ...]` relaunched AegisForge on
    every click; the actions must call the engine in-process instead."""

    @pytest.fixture
    def frozen(self, monkeypatch, notes):
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        def relaunch(*a, **k):
            raise AssertionError("the frozen app spawned a process (that is how it relaunched itself)")
        monkeypatch.setattr(mb.subprocess, "run", relaunch)
        monkeypatch.setattr(mb.subprocess, "Popen", relaunch)
        return notes

    def test_each_action_calls_the_engine_in_process(self, frozen, monkeypatch):
        from macmon_core import cleaner, processes
        calls = []
        monkeypatch.setattr(cleaner, "run_cleaner", lambda **kw: calls.append(("clean", kw)))
        monkeypatch.setattr(processes, "purge_ram", lambda: calls.append(("purge", {})))
        monkeypatch.setattr(sentinel, "pause", lambda: calls.append(("pause", {})))
        monkeypatch.setattr(sentinel, "resume", lambda: calls.append(("resume", {})))
        for args in (["clean", "--all", "-y"], ["purge"], ["sentinel", "--pause"], ["sentinel", "--resume"]):
            mb._run_action(args)
        assert [c[0] for c in calls] == ["clean", "purge", "pause", "resume"]
        assert calls[0][1] == {"all_clean": True, "force_yes": True}   # the alert was the confirmation
        assert len(frozen) == 4                                         # every outcome reported

    def test_outcome_is_the_clis_last_line(self, frozen, monkeypatch):
        from macmon_core import processes
        monkeypatch.setattr(processes, "purge_ram",
                            lambda: mb.console.print("[red]Purge failed: sudo needs a password[/]"))
        mb._run_action(["purge"])
        assert frozen == [("AegisForge", "macmon purge: Purge failed: sudo needs a password")]

    def test_a_failing_action_is_reported_not_swallowed(self, frozen, monkeypatch):
        def boom():
            raise RuntimeError("launchctl exploded")
        monkeypatch.setattr(sentinel, "resume", boom)
        mb._run_action(["sentinel", "--resume"])
        assert len(frozen) == 1 and "failed" in frozen[0][0] and "launchctl exploded" in frozen[0][1]

    def test_unknown_subcommand_is_reported(self, frozen):
        mb._run_action(["focus"])
        assert len(frozen) == 1 and "unavailable" in frozen[0][0]

    def test_dashboard_needs_the_installed_launcher(self, frozen, monkeypatch):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)
        mb.open_dashboard()
        assert len(frozen) == 1 and "not found" in frozen[0][0]


# ── actions: dev mode runs a subprocess ──────────────────────────────────

class TestDevActions:
    @pytest.fixture(autouse=True)
    def _dev(self, monkeypatch):
        monkeypatch.delattr(sys, "frozen", raising=False)

    def test_prefers_the_installed_launcher(self, monkeypatch, notes):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: "/usr/local/bin/macmon")
        seen = {}

        def run(cmd, **kw):
            seen["cmd"] = cmd
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(mb.subprocess, "run", run)
        mb._run_action(["purge"])
        assert seen["cmd"] == ["/usr/local/bin/macmon", "purge"] and notes == []

    def test_falls_back_to_this_interpreter_on_the_repo_script(self, monkeypatch):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)
        assert mb._cli_command(["purge"]) == [sys.executable, str(mb.REPO / "macmon.py"), "purge"]

    def test_nothing_runnable_is_reported_not_silent(self, monkeypatch, notes, tmp_path):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)
        monkeypatch.setattr(mb, "REPO", tmp_path)

        def boom(*a, **k):
            raise AssertionError("ran something with nothing runnable")
        monkeypatch.setattr(mb.subprocess, "run", boom)
        mb._run_action(["purge"])
        assert len(notes) == 1 and "not found" in notes[0][0]

    def test_failed_subprocess_is_reported_with_its_last_line(self, monkeypatch, notes):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: "/usr/local/bin/macmon")
        monkeypatch.setattr(mb.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(
            returncode=1, stdout="RAM before: 1 GB\nPurge failed: sudo needs a password\n", stderr=""))
        mb._run_action(["purge"])
        assert notes == [("AegisForge: macmon purge failed", "Purge failed: sudo needs a password")]


class TestOpenDashboard:
    @staticmethod
    def _popen(monkeypatch):
        seen = {}
        monkeypatch.setattr(mb.subprocess, "Popen", lambda cmd, *a, **k: seen.setdefault("cmd", cmd))
        return seen

    def test_uses_osascript_and_the_quoted_launcher(self, monkeypatch, notes):
        monkeypatch.setattr(sentinel, "find_macmon", lambda: "/Users/n o/bin/macmon")
        seen = self._popen(monkeypatch)
        mb.open_dashboard()
        cmd = seen["cmd"]
        assert cmd[0] == "osascript" and any("Terminal" in part for part in cmd)
        assert "'/Users/n o/bin/macmon' dashboard" in cmd[-1] and notes == []

    def test_dev_fallback_quotes_the_repo_and_the_interpreter(self, monkeypatch, notes):
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sentinel, "find_macmon", lambda: None)
        seen = self._popen(monkeypatch)
        mb.open_dashboard()
        script = seen["cmd"][-1]
        assert f"cd {shlex.quote(str(mb.REPO))} && {shlex.quote(sys.executable)} macmon.py dashboard" in script
        assert notes == []


# ── the live block against a real rumps Menu ─────────────────────────────

class TestLiveBlock:
    @pytest.fixture
    def menu(self):
        rumps = pytest.importorskip("rumps")
        from rumps.rumps import Menu
        m = Menu()
        m.update([rumps.MenuItem(mb.LIVE_ANCHOR), None, rumps.MenuItem("Quit AegisForge")])
        return m

    @staticmethod
    def _snap(tick):
        return {"vitals": {"cpu": 22.7, "ram": 81.1, "swap_gb": 26.4, "load1": 7.5, "disk_free_gb": 61.2},
                "findings": [F("high", f"finding {i}") for i in range(tick % 3)],
                "alerts": ["2026-09-29 swap: Swap 27 GB"] if tick % 2 else [], "age_s": 30.0}

    def test_refresh_is_stable_across_ticks_and_never_shows_keys(self, menu):
        base = len(menu)
        keys = []
        for tick in range(8):
            snap = self._snap(tick)
            keys = mb.render_live(menu, snap, keys)
            expected = base + len(mb.menu_lines(snap))
            assert len(menu) == expected
            assert menu._menu.numberOfItems() == expected          # the NSMenu itself, not only the dict
            titles = [getattr(v, "title", "") for v in menu.values()]
            assert not any("live:" in t for t in titles)
            assert "CPU: 22.7%" in titles and "sample 30s ago" in titles
            assert titles[-1] == "Quit AegisForge"
            assert titles.index(mb.LIVE_ANCHOR) == len(mb.menu_lines(snap))   # live block sits above the anchor

    def test_identical_lines_do_not_collide(self, menu):
        snap = {"vitals": {}, "findings": [F("high", "same"), F("high", "same")], "alerts": []}
        mb.render_live(menu, snap, [])
        titles = [getattr(v, "title", "") for v in menu.values()]
        assert titles.count("[high] same") == 2
        assert menu._menu.numberOfItems() == len(menu)
