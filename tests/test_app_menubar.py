"""Tests for the AegisForge.app menu-bar layer (macmon_core.app_menubar).

Hermetic: rumps is not installed in CI, so the module imports with rumps=None
and we test the pure logic (severity ranking, title, menu body, the resilient
gather()) with the engine monkeypatched. No GUI, no sampling, no subprocess.
"""
from macmon_core import app_menubar as mb


class _F:
    """Minimal Finding stand-in (severity + msg, like aegis.Finding)."""
    def __init__(self, severity, msg):
        self.severity = severity
        self.msg = msg


# ── worst_severity ───────────────────────────────────────────────────────

class TestWorstSeverity:
    def test_empty_is_ok(self):
        assert mb.worst_severity([]) == "ok"

    def test_picks_most_severe(self):
        fs = [_F("low", "a"), _F("critical", "b"), _F("medium", "c")]
        assert mb.worst_severity(fs) == "critical"

    def test_high_over_medium(self):
        assert mb.worst_severity([_F("medium", "m"), _F("high", "h")]) == "high"


# ── title_for ────────────────────────────────────────────────────────────

class TestTitleFor:
    def test_healthy_is_icon_only(self):
        assert mb.title_for({"swap_gb": 2.0}, "ok") == ""

    def test_high_shows_swap(self):
        t = mb.title_for({"swap_gb": 27.4}, "high")
        assert "27" in t

    def test_medium_is_mark_only_no_number(self):
        t = mb.title_for({"swap_gb": 27.4}, "medium")
        assert "27" not in t  # only a mark, no scary number until high/critical


# ── menu_lines ───────────────────────────────────────────────────────────

class TestMenuLines:
    def test_vitals_rendered(self):
        snap = {"vitals": {"cpu": 22.7, "ram": 81.1, "swap_gb": 26.4,
                           "load1": 7.5, "disk_free_gb": 61.2},
                "findings": [], "alerts": []}
        lines = mb.menu_lines(snap)
        assert any("CPU: 22.7%" in ln for ln in lines)
        assert any("Swap: 26.4 GB" in ln for ln in lines)
        assert any("All clear" in ln for ln in lines)

    def test_missing_vitals_show_placeholder(self):
        lines = mb.menu_lines({"vitals": {}, "findings": [], "alerts": []})
        assert any(ln == "CPU: --" for ln in lines)
        assert any(ln == "Swap: --" for ln in lines)

    def test_findings_and_alerts_rendered(self):
        snap = {"vitals": {}, "findings": [_F("high", "Swap climbing +0.3 GB/min")],
                "alerts": ["2026-09-29 swap: Swap 27 GB"]}
        lines = mb.menu_lines(snap)
        assert any("[high] Swap climbing" in ln for ln in lines)
        assert any("Recent alerts:" in ln for ln in lines)
        assert any("Swap 27 GB" in ln for ln in lines)


# ── gather (resilient; engine monkeypatched) ─────────────────────────────

class TestGather:
    def test_reads_latest_row_and_findings(self, monkeypatch):
        rows = [{"cpu": 10, "ram": 70, "swap_gb": 20, "load1": 5, "disk_free_gb": 80},
                {"cpu": 22.7, "ram": 81.1, "swap_gb": 26.4, "load1": 7.5, "disk_free_gb": 61.2}]
        monkeypatch.setattr(mb.sentinel, "_load_tail", lambda n=16: rows)
        monkeypatch.setattr(mb.aegis, "trends", lambda r, c, now: {"points": len(r)})
        monkeypatch.setattr(mb.aegis, "detect_swap_trend", lambda t, c: _F("high", "swap up"))
        monkeypatch.setattr(mb.aegis, "detect_memory_trend", lambda t, c: None)
        monkeypatch.setattr(mb.aegis, "detect_load", lambda t, c: None)
        monkeypatch.setattr(mb.aegis, "detect_swarm", lambda t, c: [])
        monkeypatch.setattr(mb.aegis, "detect_leaks", lambda t, c: None)
        monkeypatch.setattr(mb, "_recent_alerts", lambda n: [])
        snap = mb.gather()
        assert snap["error"] is None
        assert snap["vitals"]["swap_gb"] == 26.4     # latest row
        assert snap["worst"] == "high"
        assert len(snap["findings"]) == 1

    def test_never_raises_on_broken_sampler(self, monkeypatch):
        def _boom(n=16):
            raise RuntimeError("metrics unreadable")
        monkeypatch.setattr(mb.sentinel, "_load_tail", _boom)
        snap = mb.gather()
        assert snap["worst"] == "ok"           # safe default
        assert snap["error"] is not None       # but records what went wrong
        # menu still renders from the safe snapshot
        assert mb.menu_lines(snap)


# ── actions wire to the right macmon subcommands (never auto-invoked) ─────

class TestActions:
    def test_clean_purge_pause_resume_args(self, monkeypatch):
        calls = []
        monkeypatch.setattr(mb, "_run_cli", lambda args: calls.append(args))
        mb.clean_caches()
        mb.purge_ram()
        mb.pause_sentinel()
        mb.resume_sentinel()
        assert calls == [["clean", "--all", "-y"], ["purge"],
                         ["sentinel", "--pause"], ["sentinel", "--resume"]]

    def test_open_dashboard_uses_osascript(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(mb.subprocess, "Popen",
                            lambda cmd, *a, **k: seen.setdefault("cmd", cmd))
        mb.open_dashboard()
        assert seen["cmd"][0] == "osascript"
        assert any("Terminal" in part for part in seen["cmd"])
