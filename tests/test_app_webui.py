"""Tests for the AegisForge local dashboard server (macmon_core.app_webui).

Hermetic: the engine is mocked, the server binds an ephemeral loopback port and
is shut down in the fixture. No browser, no real sampling, no external network.
"""
import http.client
import json
import re
import socket
import sys
import types
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


# ── audit: the security core of the web layer ────────────────────────────
#
# loopback-only bind (above) + Host-checked + read-only + XSS-safe. These lock
# the adversarial-audit fixes: a DNS-rebinding page must not read telemetry, a
# NaN must not freeze the page, and no severity/text/alert reaches the DOM raw.

def _raw_get(port, path, host):
    """A GET with an explicit Host header (urllib always sends the loopback one)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        c.putrequest("GET", path, skip_host=True)
        c.putheader("Host", host)
        c.endheaders()
        r = c.getresponse()
        return r.status, r.read().decode(), dict(r.getheaders())
    finally:
        c.close()


class TestHostGate:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "127.0.0.1:9137",
                                      "localhost:1"])
    def test_host_allowed_accepts_loopback_names(self, host):
        assert app_webui.host_allowed(host) is True

    @pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:9137", "",
                                      None, "127.0.0.1.evil.com", "localhost.evil.com",
                                      "[::1]:9137", "0.0.0.0", "10.8.0.2:9137"])
    def test_host_allowed_refuses_anything_else(self, host):
        assert app_webui.host_allowed(host) is False

    def test_foreign_host_is_403_on_the_api(self, server):
        # DNS rebinding: the attacker's page fetches http://evil.example.com:<port>
        # after flipping that name to 127.0.0.1 -- the browser sends ITS Host.
        _, port = server
        status, body, _ = _raw_get(port, "/api/status", "evil.example.com:%d" % port)
        assert status == 403
        assert "vitals" not in body  # not a byte of telemetry leaves

    def test_foreign_host_is_403_on_the_page_too(self, server):
        _, port = server
        status, body, _ = _raw_get(port, "/", "evil.example.com")
        assert status == 403 and "AegisForge" not in body

    def test_loopback_host_still_200(self, server):
        _, port = server
        for host in ("127.0.0.1:%d" % port, "localhost:%d" % port, "localhost"):
            status, body, _ = _raw_get(port, "/api/status", host)
            assert status == 200 and json.loads(body)["vitals"]["ram"] == 50.0

    def test_urllib_default_host_is_loopback(self, server):
        # what the page itself does: fetch("/api/status") against the loopback origin
        _, port = server
        status, _ = _get(port, "api/status")
        assert status == 200

    def test_nosniff_on_every_response(self, server):
        _, port = server
        for path, host in (("/", "127.0.0.1"), ("/api/status", "127.0.0.1"),
                           ("/nope", "127.0.0.1"), ("/api/status", "evil.example.com")):
            _, _, headers = _raw_get(port, path, host)
            assert headers.get("X-Content-Type-Options") == "nosniff", path
            assert headers.get("Cache-Control") == "no-store", path


class TestReadOnly:
    def test_no_mutating_handler_exists(self):
        # The window is a read-only dashboard: the server must never grow a
        # POST/PUT/DELETE/PATCH (a loopback mutation is what rebinding would drive).
        for verb in ("POST", "PUT", "DELETE", "PATCH"):
            assert not hasattr(app_webui._Handler, "do_" + verb), verb

    def test_post_is_refused(self, server):
        _, port = server
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            c.request("POST", "/api/status", body=b"{}", headers={"Content-Type": "application/json"})
            assert c.getresponse().status == 501
        finally:
            c.close()

    def test_footer_is_honest_about_where_actions_live(self):
        # The window app has no menu bar; the copy must not claim one.
        page = app_webui._PAGE
        assert "read-only view" in page
        assert "macmon" in page and "clean / purge / sentinel --pause" in page
        assert "menu bar" not in page

    def test_per_connection_timeout(self):
        assert app_webui._Handler.timeout == 10


class TestNaNVital:
    def test_nan_still_yields_valid_json_flagged_stale(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": float("nan"), "ram": 50.0, "swap_gb": 1.0, "load1": 2.0,
                       "disk_free_gb": 100.0},
            "findings": [], "alerts": [], "worst": "ok", "age_s": 5, "error": None,
        })
        body = app_webui.status_json().decode()
        d = json.loads(body)             # strict: a bare NaN token would raise here
        assert "NaN" not in body
        assert d["stale"] is True and d["age_label"] == "engine error"
        assert d["error"] and d["vitals"] == {} and d["findings"] == []

    def test_infinity_too(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": 1.0, "ram": float("inf"), "swap_gb": 1.0, "load1": 2.0,
                       "disk_free_gb": 100.0},
            "findings": [], "alerts": [], "worst": "ok", "age_s": 5, "error": None,
        })
        d = json.loads(app_webui.status_json())
        assert d["stale"] is True and d["age_label"] == "engine error"

    def test_served_over_http(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": float("nan")}, "findings": [], "alerts": [], "worst": "ok",
            "age_s": 5, "error": None,
        })
        srv, port = app_webui.serve(0)
        try:
            status, body = _get(port, "api/status")
            assert status == 200 and json.loads(body)["stale"] is True
        finally:
            srv.shutdown()
            srv.server_close()

    def test_healthy_payload_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": 10.0}, "findings": [], "alerts": [], "worst": "ok",
            "age_s": 5, "error": None,
        })
        d = json.loads(app_webui.status_json())
        assert d["stale"] is False and d["vitals"]["cpu"] == 10.0


class TestSeverityWhitelist:
    @pytest.mark.parametrize("sev", ["critical", "high", "medium", "low", "info", "ok"])
    def test_known_levels_pass(self, sev):
        assert app_webui._severity(sev) == sev

    @pytest.mark.parametrize("raw", ['<script>', 'high" onmouseover="alert(1)', "HIGH",
                                     "", None, "x" * 500, 'critical"><img src=x>'])
    def test_anything_else_is_info_or_normalized(self, raw):
        out = app_webui._severity(raw)
        assert out in app_webui._SEVERITIES
        assert out == ("high" if raw == "HIGH" else "info")

    def test_status_dict_applies_it(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {}, "findings": [_F('high"><script>alert(1)</script>', "t"), _F("Critical", "u")],
            "alerts": [], "worst": "ok", "age_s": 5, "error": None,
        })
        sevs = [f["severity"] for f in app_webui.status_dict()["findings"]]
        assert sevs == ["info", "critical"]


class TestXssSinks:
    PAYLOAD_TEXT = "<img src=x onerror=alert(1)>"
    PAYLOAD_ALERT = "</script><script>alert(2)</script>"

    @pytest.fixture
    def hostile(self, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": 1.0}, "findings": [_F("high", self.PAYLOAD_TEXT)],
            "alerts": [self.PAYLOAD_ALERT], "worst": "high", "age_s": 5, "error": None,
        })
        srv, port = app_webui.serve(0)
        yield port
        srv.shutdown()
        srv.server_close()

    def test_payloads_round_trip_verbatim_as_data(self, hostile):
        # The server does not mangle data: escaping is the page's job, at the sink.
        _, body = _get(hostile, "api/status")
        d = json.loads(body)
        assert d["findings"][0]["text"] == self.PAYLOAD_TEXT
        assert d["alerts"][0] == self.PAYLOAD_ALERT
        assert d["findings"][0]["severity"] == "high"

    def test_json_is_never_served_as_html(self, hostile):
        status, body, headers = _raw_get(hostile, "/api/status", "127.0.0.1")
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_escape_html_is_applied_at_every_sink(self):
        page = app_webui._PAGE
        # every interpolation of engine-provided data goes through escapeHtml
        assert "${escapeHtml(f.text)}" in page
        assert "${escapeHtml(x)}" in page
        assert page.count("${escapeHtml(f.severity)}") == 2   # CSS class + label
        # ... and none reaches a template raw
        for raw in ("${f.text}", "${f.severity}", "${x}", "${d.age_label}"):
            assert raw not in page, raw
        # the age badge goes through textContent, not innerHTML
        assert "a.textContent=d.age_label" in page

    def test_escape_html_covers_the_html_metacharacters(self):
        page = app_webui._PAGE
        m = re.search(r"function escapeHtml\(s\)\{return String\(s\)\.replace\((/\[[^\]]+\]/g)", page)
        assert m, "escapeHtml must be a replace over a character class"
        for ch in "&<>\"":
            assert ch in m.group(1), ch

    def test_no_other_innerhtml_data_sink(self):
        # innerHTML assignments in the page are either a clearing "" or a literal
        # (no ${...} of engine data): the data path is insertAdjacentHTML + escapeHtml.
        page = app_webui._PAGE
        for assign in re.findall(r"innerHTML=(.*?);", page):
            assert "${" not in assign, assign


class TestSharedServer:
    @pytest.fixture(autouse=True)
    def _clean(self):
        app_webui.stop_server()
        yield
        app_webui.stop_server()

    def test_ensure_server_is_idempotent(self):
        a = app_webui.ensure_server(0)
        b = app_webui.ensure_server(0)
        assert a is b and a[1] > 0
        assert _get(a[1], "api/status")[0] == 200

    def test_menubar_ensure_web_uses_the_same_cache(self):
        srv, port = app_webui.ensure_server(0)
        assert app_menubar._ensure_web() == port
        assert app_webui._shared is not None and app_webui._shared[0] is srv

    def test_stop_server_releases_the_port(self):
        _, port = app_webui.ensure_server(0)
        app_webui.stop_server()
        assert app_webui._shared is None
        with pytest.raises((ConnectionRefusedError, OSError)):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
        app_webui.stop_server()  # idempotent

    def test_ensure_web_reports_none_when_bind_fails(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no ports")
        monkeypatch.setattr(app_webui, "serve", boom)
        assert app_menubar._ensure_web() is None
        assert app_webui._shared is None


class TestRunWindow:
    @pytest.fixture(autouse=True)
    def _clean(self):
        app_webui.stop_server()
        yield
        app_webui.stop_server()

    @pytest.fixture
    def fake_webview(self, monkeypatch):
        seen = {}

        def create_window(title, url, **kw):
            seen["title"], seen["url"] = title, url

        def start():
            # while the window is "open", the shared server is the one it points at
            port = int(seen["url"].rsplit(":", 1)[1].rstrip("/"))
            seen["port"] = port
            seen["shared_while_open"] = app_webui._shared
            seen["status_while_open"] = _get(port, "api/status")[0]
        monkeypatch.setitem(sys.modules, "webview",
                            types.SimpleNamespace(create_window=create_window, start=start))
        return seen

    def test_uses_the_shared_server_and_tears_it_down(self, fake_webview, monkeypatch):
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {}, "findings": [], "alerts": [], "worst": "ok", "age_s": 5, "error": None})
        assert app_webui.run_window(port_pref=0) is True
        assert fake_webview["title"] == "AegisForge"
        assert fake_webview["url"].startswith("http://127.0.0.1:")
        assert fake_webview["status_while_open"] == 200
        assert fake_webview["shared_while_open"] is not None
        # closed -> shutdown + server_close: nothing bound, cache cleared
        assert app_webui._shared is None
        with pytest.raises((ConnectionRefusedError, OSError)):
            socket.create_connection(("127.0.0.1", fake_webview["port"]), timeout=1).close()

    def test_tears_down_even_if_webview_raises(self, fake_webview, monkeypatch):
        def start():
            raise RuntimeError("no display")
        sys.modules["webview"].start = start
        with pytest.raises(RuntimeError):
            app_webui.run_window(port_pref=0)
        assert app_webui._shared is None

    def test_without_pywebview_binds_nothing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "webview", None)  # import -> ImportError
        assert app_webui.run_window(port_pref=0) is False
        assert app_webui._shared is None
