"""Tests for the AegisForge local dashboard server (macmon_core.app_webui).

Hermetic: the engine is mocked, the server binds an ephemeral loopback port and
is shut down in the fixture. No browser, no real sampling, no external network.
"""
import http.client
import inspect
import json
import re
import socket
import sys
import types
import urllib.error
import urllib.request

import pytest

from macmon_core import app_api, app_webui, app_menubar


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

    def test_every_api_route_is_a_read(self):
        # The route table only ever calls a *_dict builder / status_json: no
        # handler name suggests a mutation, and none takes a body.
        src = inspect.getsource(app_webui._Handler._api)
        for verb in ("kill", "clean_execute", "prune", "pause", "resume", "write", "quarantine", "purge"):
            assert verb not in src, verb
        assert "self.rfile" not in inspect.getsource(app_webui._Handler)

    def test_mutations_live_only_on_the_bridge(self):
        # The page's actions go through window.pywebview.api.* (js_api), never a
        # fetch() to a mutating URL: every fetch in app.js targets /api/<read>.
        js = (app_webui.WEBUI_DIR / "app.js").read_text()
        for m in re.finditer(r'fetch\(([^)]*)\)', js):
            assert "/api/" in m.group(1) or "path" in m.group(1), m.group(0)
        assert 'method: "POST"' not in js and "method:'POST'" not in js
        for name in ("process_guard", "clean_scan", "clean_execute", "docker_prune_dangling",
                     "sentinel_set_auto", "quarantine", "purge_ram", "reveal"):
            assert f'api("{name}"' in js, name
        assert 'api(verb === "pause" ? "sentinel_pause" : "sentinel_resume")' in js
        assert "api(`${verb}_process`" in js          # kill / suspend / resume share one funnel
        assert "window.pywebview.api" in js
        # and every mutating control the page builds is disabled without the bridge
        assert js.count("disabled: !bridge.ready") >= 4

    def test_page_is_honest_without_the_bridge(self):
        # Opened in a plain browser tab there is no js_api: the page says the
        # actions live in the native window, it does not pretend to have them.
        js = (app_webui.WEBUI_DIR / "app.js").read_text()
        assert "read-only tab" in js.lower()
        assert "JSON.parse(r)" in js   # WKWebView returns js_api results as JSON strings

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

    def test_no_html_string_sink_anywhere_in_the_script(self):
        # Every node is built with h() (text via textContent / createTextNode):
        # there is no HTML-string sink at all, so escaping cannot be forgotten.
        js = (app_webui.WEBUI_DIR / "app.js").read_text()
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "srcdoc", "eval("):
            assert sink not in js, sink
        assert "createTextNode" in js and "textContent" in js

    def test_inline_script_free_page_with_a_csp(self):
        # index.html carries no inline script; the CSP allows scripts from
        # 'self' only, so a stray string could never execute even if it were
        # injected as markup.
        html = app_webui.page_html()
        assert re.search(r"<script(?![^>]*\bsrc=)", html) is None
        assert 'src="/app.js"' in html
        assert "script-src 'self' 'unsafe-eval'" in app_webui._CSP   # 'unsafe-eval': pywebview's bridge needs it
        assert "default-src 'self'" in app_webui._CSP and "unsafe-inline" not in app_webui._CSP.split("style-src")[0]
        assert "http" not in app_webui._CSP                          # never a remote source

    def test_severity_class_is_whitelisted_server_side(self):
        # the worst indicator becomes a CSS class; it goes through _severity too
        assert app_webui._severity("critical\"><img") == "info"
        assert "worst" in inspect.getsource(app_webui.status_dict)
        assert "_severity(snap.get(\"worst\"" in inspect.getsource(app_webui.status_dict)


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
            seen["title"], seen["url"], seen["kw"] = title, url, kw

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
        # the ONE mutation path: the js_api bridge, handed to the window only
        assert isinstance(fake_webview["kw"]["js_api"], app_api.Api)
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


# ── the multi-section page + its static assets ───────────────────────────

SECTIONS = ("overview", "processes", "clean", "security", "docker", "disk", "sentinel")


class TestPage:
    def test_has_every_section_and_nav_item(self):
        html = app_webui.page_html()
        for s in SECTIONS:
            assert f'id="view-{s}"' in html, s
            assert f'data-view="{s}"' in html, s
        assert "AegisForge" in html and "fleet health, forged" in html

    def test_funnel_scaffolding_is_present(self):
        html = app_webui.page_html()
        assert 'id="modal"' in html and 'id="modal-ack-box"' in html   # confirm + acknowledgement
        assert 'id="clean-stepper"' in html                             # scan -> review -> confirm -> done
        assert 'id="toasts"' in html
        # the static mutating buttons are marked so the page disables them without the bridge
        assert html.count("data-needs-bridge") >= 3

    def test_static_assets_served_with_types_and_headers(self, server):
        _, port = server
        for path, ctype in (("/", "text/html"), ("/app.css", "text/css"), ("/app.js", "text/javascript"),
                            ("/mark.svg", "image/svg+xml")):
            status, body, headers = _raw_get(port, path, "127.0.0.1")
            assert status == 200 and body, path
            assert headers["Content-Type"].startswith(ctype), path
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["Content-Security-Policy"] == app_webui._CSP
            assert headers["Cache-Control"] == "no-store"

    def test_only_the_whitelisted_files_are_served(self, server):
        # no directory traversal, no arbitrary file under assets/
        _, port = server
        for path in ("/../macmon.py", "/assets/webui/app.js", "/app.js/../../pyproject.toml", "/index.htm"):
            status, _, _ = _raw_get(port, path, "127.0.0.1")
            assert status == 404, path

    def test_static_is_host_gated_too(self, server):
        _, port = server
        for path in ("/", "/app.js"):
            status, body, _ = _raw_get(port, path, "evil.example.com")
            assert status == 403 and "AegisForge" not in body

    def test_brand_mark_is_the_kit_svg(self):
        svg = (app_webui.WEBUI_DIR / "mark.svg").read_text()
        assert "#34E5A0" in svg and "#FF7A1A" in svg   # mint shield + ember coal


# ── the read endpoints (engine mocked; JSON; GET-only; Host-gated) ────────

class TestReadEndpoints:
    @pytest.fixture
    def quiet(self, monkeypatch):
        from macmon_core import disk, docker_mgr, health, processes, security, sentinel
        monkeypatch.setattr(sentinel, "_load_tail", lambda n=16: [
            {"ts": 1.0, "cpu": 5, "ram": 50, "swap_gb": 1, "load1": 2, "disk_free_gb": 100},
            {"ts": 61.0, "cpu": 7, "ram": 52, "swap_gb": 1.2, "load1": 2.5, "disk_free_gb": 99}])
        monkeypatch.setattr(sentinel, "_load", lambda n=120: [{}] * 7)
        monkeypatch.setattr(sentinel, "_scheduler_has", lambda label: True)
        monkeypatch.setattr(sentinel, "_purge_nopasswd_ready", lambda: False)
        monkeypatch.setattr(sentinel, "_conf", lambda: dict(sentinel.DEFAULTS))
        monkeypatch.setattr(health, "_run_all_checks", lambda: [
            {"name": "RAM Usage", "status": "pass", "detail": "40%"},
            {"name": "Swap Usage", "status": "warn", "detail": "60%"}])
        monkeypatch.setattr(processes, "collect_processes", lambda filter_cat=None, sort_by="cpu": [
            {"pid": 1, "ppid": 0, "name": "launchd", "cpu": 0.1, "ram": 10, "status": "running",
             "created": 1.0, "user": "root", "category": "system"},
            {"pid": 4242, "ppid": 1, "name": "node", "cpu": 12.5, "ram": 2048, "status": "running",
             "created": 100.0, "user": "neo", "category": "node"},
            {"pid": 4243, "ppid": 1, "name": "<img src=x onerror=alert(1)>", "cpu": 1.0, "ram": 1,
             "status": "running", "created": 100.0, "user": "neo", "category": "other"}])
        monkeypatch.setattr(security, "_security_checks", lambda progress=None: (85, [
            {"name": "macOS Firewall", "status": "fail", "detail": "off", "fix_hint": "turn it on"},
            {"name": "SIP", "status": "pass", "detail": "enabled"}]))
        monkeypatch.setattr(docker_mgr, "_docker_available", lambda: True)
        monkeypatch.setattr(docker_mgr, "_overview_data", lambda: {"disk_usage": [], "running": [{"name": "neobot"}],
                                                                   "stopped": [], "dangling_images": 3, "volumes": 2})
        monkeypatch.setattr(docker_mgr, "_containers_data", lambda: [{"name": "neobot", "state": "running"}])
        monkeypatch.setattr(docker_mgr, "_images_data", lambda: [{"repository": "<none>", "tag": "<none>"}])
        monkeypatch.setattr(docker_mgr, "_volumes_data", lambda: [{"name": "v1", "in_use": True}])
        monkeypatch.setattr(disk, "_disk_entries", lambda base: [
            {"path": str(base / "a"), "name": "a", "size": 3000, "count": 2, "mtime": 1.0},
            {"path": str(base / "b"), "name": "b", "size": 1000, "count": 1, "mtime": 1.0}])
        monkeypatch.setattr(disk, "_scan_big_files", lambda base, mb, ft=None, older=None: [
            {"path": str(base / "big.iso"), "size": 5 * 10**9, "atime": 1.0, "mtime": 1.0, "emoji": "", "category": "disk_image"}])
        monkeypatch.setattr(app_menubar, "gather", lambda: {
            "vitals": {"cpu": 7.0}, "findings": [], "alerts": [], "worst": "ok", "age_s": 5, "error": None})
        srv, port = app_webui.serve(0)
        yield port
        srv.shutdown()
        srv.server_close()

    def test_history(self, quiet):
        d = json.loads(_get(quiet, "api/history?n=60")[1])
        assert [r["cpu"] for r in d["rows"]] == [5, 7]
        assert set(d["rows"][0]) == set(app_webui._HISTORY_KEYS)

    def test_health(self, quiet):
        d = json.loads(_get(quiet, "api/health")[1])
        assert d["score"] == 75 and len(d["checks"]) == 2      # (100 + 50) / 2

    def test_processes_flag_the_protected_and_pass_names_verbatim(self, quiet):
        d = json.loads(_get(quiet, "api/processes?sort=ram")[1])
        by = {p["pid"]: p for p in d["processes"]}
        assert by[1]["protected"] is True                       # launchd / PID 1: never a target
        assert by[4242]["protected"] is False and by[4242]["ram_label"] == "2.0 KB"
        assert by[4243]["name"] == "<img src=x onerror=alert(1)>"  # data; the page builds text nodes only
        assert d["sort"] == "ram" and d["total"] == 3

    def test_processes_sort_is_whitelisted(self, quiet):
        d = json.loads(_get(quiet, "api/processes?sort=__import__")[1])
        assert d["sort"] == "cpu"

    def test_security(self, quiet):
        d = json.loads(_get(quiet, "api/security")[1])
        assert d["score"] == 85 and d["findings"][0]["status"] == "fail"

    def test_docker(self, quiet):
        d = json.loads(_get(quiet, "api/docker")[1])
        assert d["available"] is True and d["overview"]["dangling_images"] == 3
        assert d["containers"][0]["name"] == "neobot" and d["volumes"][0]["in_use"] is True

    def test_docker_absent(self, quiet, monkeypatch):
        from macmon_core import docker_mgr
        monkeypatch.setattr(docker_mgr, "_docker_available", lambda: False)
        d = json.loads(_get(quiet, "api/docker")[1])
        assert d["available"] is False and d["containers"] == []

    def test_disk(self, quiet, tmp_path):
        d = json.loads(_get(quiet, "api/disk?path=" + str(tmp_path))[1])
        assert d["total"] == 4000 and d["entries"][0]["pct"] == 75.0 and d["entries"][0]["size_label"] == "2.9 KB"

    def test_disk_refuses_a_non_directory(self, quiet, tmp_path):
        d = json.loads(_get(quiet, "api/disk?path=" + str(tmp_path / "nope"))[1])
        assert "error" in d and d["entries"] == []

    def test_bigfiles(self, quiet, tmp_path):
        d = json.loads(_get(quiet, f"api/bigfiles?path={tmp_path}&min=1GB")[1])
        assert d["min_bytes"] == 1024 ** 3 and d["files"][0]["category"] == "disk_image"
        assert d["files"][0]["size_label"] == "4.7 GB"

    def test_bigfiles_bad_size_is_an_error_not_a_500(self, quiet, tmp_path):
        d = json.loads(_get(quiet, f"api/bigfiles?path={tmp_path}&min=lots")[1])
        assert "invalid size" in d["error"]

    def test_sentinel(self, quiet):
        d = json.loads(_get(quiet, "api/sentinel")[1])
        assert d["sampler_active"] is True and d["samples"] == 7
        assert d["auto"] == {k: False for k in app_api.AUTO_KEYS}   # every level defaults OFF
        assert set(d["auto_labels"]) == set(app_api.AUTO_KEYS)
        assert len(d["detectors"]) == 5 and d["thresholds"]["swap_critical_gb"] == 8.0
        assert {"swap", "ram", "load", "leaks", "points"} <= set(d["trends"])

    @pytest.mark.parametrize("path", ["api/history", "api/health", "api/processes", "api/security",
                                      "api/docker", "api/disk", "api/bigfiles", "api/sentinel"])
    def test_every_read_is_host_gated(self, quiet, path):
        status, body, _ = _raw_get(quiet, "/" + path, "evil.example.com:%d" % quiet)
        assert status == 403 and "{" not in body

    @pytest.mark.parametrize("path", ["api/history", "api/processes", "api/sentinel", "api/docker"])
    def test_every_read_refuses_post(self, quiet, path):
        c = http.client.HTTPConnection("127.0.0.1", quiet, timeout=3)
        try:
            c.request("POST", "/" + path, body=b"{}")
            assert c.getresponse().status == 501
        finally:
            c.close()

    def test_engine_failure_is_a_json_500_not_a_hang(self, quiet, monkeypatch):
        from macmon_core import health

        def boom():
            raise RuntimeError("brew exploded")
        monkeypatch.setattr(health, "_run_all_checks", boom)
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(quiet, "api/health")
        assert e.value.code == 500 and "brew exploded" in json.loads(e.value.read())["error"]

    def test_unknown_api_is_404(self, quiet):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(quiet, "api/kill")
        assert e.value.code == 404


# ── audit findings: cross-site gate + security OS-gate + disk guards ──────
class TestCrossSiteGate:
    @pytest.mark.parametrize("v,ok", [("", True), ("same-origin", True), ("none", True), (None, True),
                                      ("cross-site", False), ("same-site", False), ("CROSS-SITE", False)])
    def test_fetch_site_gate(self, v, ok):
        assert app_webui.fetch_site_allowed(v) is ok

    def test_cross_site_request_is_403_even_with_loopback_host(self, server):
        # An <img>/fetch from another origin gets a loopback Host filled by the
        # browser (Host gate passes), but is labelled Sec-Fetch-Site: cross-site.
        _, port = server
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            c.putrequest("GET", "/api/status", skip_host=True)
            c.putheader("Host", "127.0.0.1:%d" % port)
            c.putheader("Sec-Fetch-Site", "cross-site")
            c.endheaders()
            r = c.getresponse()
            assert r.status == 403 and "vitals" not in r.read().decode()
        finally:
            c.close()

    def test_same_origin_request_still_200(self, server):
        _, port = server
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            c.putrequest("GET", "/api/status", skip_host=True)
            c.putheader("Host", "127.0.0.1:%d" % port)
            c.putheader("Sec-Fetch-Site", "same-origin")     # our own page's fetch
            c.endheaders()
            r = c.getresponse()
            assert r.status == 200 and "vitals" in r.read().decode()
        finally:
            c.close()


class TestSecurityOsGate:
    def test_security_dict_is_gated_off_macos(self, monkeypatch):
        from macmon_core import platform_compat
        monkeypatch.setattr(platform_compat, "require_os", lambda os_name: "macmon security requires macOS")
        d = app_webui.security_dict()
        assert d["score"] is None and d["findings"] == [] and "macOS" in d["error"]

    def test_disk_walkers_are_concurrency_bounded(self):
        # /api/disk and /api/bigfiles share a bounded semaphore so a burst of
        # requests cannot spawn unbounded full-disk walks.
        assert isinstance(app_webui._DISK_SEM, type(app_webui.threading.BoundedSemaphore(1)))


class TestBigFilesSkipsMountPoints:
    def test_scan_skips_the_dangerous_absolute_paths(self, monkeypatch):
        from macmon_core import disk
        dirs = ["home", "proc", "dev", "Volumes", "System"]   # yielded object; pruned in place

        def fake_walk(base):
            yield "/", dirs, []                               # only the top level of "/"
        monkeypatch.setattr(disk.os, "walk", fake_walk)
        disk._scan_big_files(disk.Path("/"), 1)
        # /proc, /dev, /Volumes are pruned; /System stays (only /System/Volumes is a mount)
        assert "home" in dirs and "System" in dirs
        assert "proc" not in dirs and "dev" not in dirs and "Volumes" not in dirs
