"""Tests for the AegisForge local dashboard server (macmon_core.app_webui).

Hermetic: the engine is mocked, the server binds an ephemeral loopback port and
is shut down in the fixture. No browser, no real sampling, no external network.
"""
import json
import urllib.error
import urllib.request

import pytest

from macmon_core import app_webui, app_menubar


class _F:
    def __init__(self, level, msg):
        self.level = level
        self.msg = msg


# ── status_dict + age label (pure) ───────────────────────────────────────

class TestStatusDict:
    def test_serializes_findings_and_age(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": 22.7, "ram": 81.1, "swap_gb": 26.4, "load1": 7.5, "disk_free_gb": 61.2},
            "findings": [_F("high", "Swap climbing +0.3 GB/min")],
            "alerts": ["2026-09-29 swap: 27 GB"], "worst": "high", "age_s": 45, "error": None,
        })
        d = app_webui.status_dict()
        assert d["vitals"]["swap_gb"] == 26.4
        assert d["findings"] == [{"severity": "high", "text": "Swap climbing +0.3 GB/min"}]
        assert d["worst"] == "high" and d["stale"] is False
        assert "45s ago" in d["age_label"]
        json.dumps(d)  # must be JSON-serializable

    def test_stale_past_threshold(self):
        label, stale = app_webui._age_label(400)
        assert stale is True and "STALE" in label

    def test_no_sample(self):
        label, stale = app_webui._age_label(None)
        assert stale is True and "no sample" in label


# ── the server (real loopback, engine mocked) ────────────────────────────

@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(app_menubar, "gather", lambda: {
        "vitals": {"cpu": 10.0, "ram": 50.0, "swap_gb": 1.0, "load1": 2.0, "disk_free_gb": 100.0},
        "findings": [], "alerts": [], "worst": "ok", "age_s": 5, "error": None,
    })
    srv, port = app_webui.serve(0)  # ephemeral
    yield srv, port
    srv.shutdown()


def _get(port, path):
    with urllib.request.urlopen(app_webui.url_for(port) + path, timeout=3) as r:
        return r.status, r.read().decode()


class TestServer:
    def test_binds_loopback_only(self, server):
        srv, _ = server
        assert srv.server_address[0] == "127.0.0.1"   # never 0.0.0.0

    def test_root_serves_branded_html(self, server):
        _, port = server
        status, body = _get(port, "")
        assert status == 200 and "AegisForge" in body and "<html" in body.lower()

    def test_api_status_is_json(self, server):
        _, port = server
        status, body = _get(port, "api/status")
        assert status == 200
        d = json.loads(body)
        assert d["vitals"]["ram"] == 50.0 and d["findings"] == [] and d["worst"] == "ok"

    def test_unknown_path_404(self, server):
        _, port = server
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(port, "secret")
        assert e.value.code == 404


class TestPortFallback:
    def test_falls_back_to_free_port_when_taken(self):
        # occupy a port, then ask serve() for it -> it must not raise, it picks
        # a different (free) port instead.
        busy, taken_port = app_webui.serve(0)
        try:
            srv2, port2 = app_webui.serve(taken_port)
            try:
                assert port2 != taken_port and port2 > 0
            finally:
                srv2.shutdown()
        finally:
            busy.shutdown()
