"""Tests for macmon_core.platform_compat.

These run on macOS, Ubuntu and Windows CI. OS-specific branches are always
exercised by monkeypatching IS_MAC / IS_WINDOWS / IS_LINUX / OS_NAME on the
module -- never by asserting the host platform's behavior unconditionally.
"""
import os
from pathlib import Path

import psutil

import macmon_core.platform_compat as pc


def _force(monkeypatch, *, mac=False, windows=False, linux=False, os_name=None):
    monkeypatch.setattr(pc, "IS_MAC", mac)
    monkeypatch.setattr(pc, "IS_WINDOWS", windows)
    monkeypatch.setattr(pc, "IS_LINUX", linux)
    if os_name is not None:
        monkeypatch.setattr(pc, "OS_NAME", os_name)


# -- require_os -----------------------------------------------------------

class TestRequireOs:
    def test_supported_returns_none(self, monkeypatch):
        monkeypatch.setattr(pc, "OS_NAME", "macOS")
        assert pc.require_os("macOS") is None
        assert pc.require_os("Windows", "macOS") is None

    def test_unsupported_returns_message(self, monkeypatch):
        monkeypatch.setattr(pc, "OS_NAME", "TestOS")
        msg = pc.require_os("macOS")
        assert isinstance(msg, str)
        assert "requires" in msg
        assert "TestOS" in msg

    def test_multiple_supported_named_in_message(self, monkeypatch):
        monkeypatch.setattr(pc, "OS_NAME", "Linux")
        msg = pc.require_os("macOS", "Windows")
        assert "macOS or Windows" in msg
        assert "Linux" in msg

    def test_real_host_os_is_supported_by_itself(self):
        # Whatever OS CI runs on, requiring that OS must pass.
        assert pc.require_os(pc.OS_NAME) is None


# -- load_average ---------------------------------------------------------

class TestLoadAverage:
    def test_real_host_shape(self):
        la = pc.load_average()
        assert isinstance(la, tuple)
        assert len(la) == 3
        assert all(isinstance(v, float) for v in la)

    def test_fallback_when_getloadavg_unavailable(self, monkeypatch):
        # Simulate a platform with no os.getloadavg and a cold psutil sampler
        # (psutil reports all-zero until warmed up). raising=False because
        # os.getloadavg does not exist on Windows in the first place.
        def _raise(*a, **k):
            raise AttributeError("no getloadavg")

        monkeypatch.setattr(os, "getloadavg", _raise, raising=False)
        monkeypatch.setattr(psutil, "getloadavg", lambda: (0.0, 0.0, 0.0),
                            raising=False)
        # Keep the last-resort estimate hermetic and deterministic.
        monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: 50.0)
        monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 4)

        la = pc.load_average()
        assert isinstance(la, tuple)
        assert len(la) == 3
        assert all(isinstance(v, float) for v in la)
        # 50% of 4 logical CPUs -> 2.0 estimated load
        assert la == (2.0, 2.0, 2.0)

    def test_fallback_everything_fails_returns_zeros(self, monkeypatch):
        def _raise(*a, **k):
            raise AttributeError("no getloadavg")

        def _boom(*a, **k):
            raise RuntimeError("psutil broken")

        monkeypatch.setattr(os, "getloadavg", _raise, raising=False)
        monkeypatch.setattr(psutil, "getloadavg", _boom, raising=False)
        monkeypatch.setattr(psutil, "cpu_percent", _boom)
        assert pc.load_average() == (0.0, 0.0, 0.0)


# -- platform directories -------------------------------------------------

class TestDirs:
    def test_each_returns_list_of_paths_on_real_host(self):
        for fn in (pc.cache_dirs, pc.temp_dirs, pc.log_dirs):
            out = fn()
            assert isinstance(out, list)
            assert all(isinstance(p, Path) for p in out)
        # temp_dirs filters on existence, and every OS has a temp dir
        assert len(pc.temp_dirs()) >= 1
        assert all(p.exists() for p in pc.temp_dirs())

    def test_cache_dirs_windows_inetcache_only(self, monkeypatch, tmp_path):
        # Locks the fixed double-count bug: LOCALAPPDATA\\Temp must NOT be in
        # cache_dirs (temp_dirs already covers it), and LOCALAPPDATA itself is
        # user data, not cache.
        _force(monkeypatch, windows=True)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        out = pc.cache_dirs()
        assert len(out) == 1
        assert out[0].name == "INetCache"
        assert out[0] == tmp_path / "Microsoft/Windows/INetCache"
        # The fix: cache_dirs must not return a bare Temp dir (temp_dirs covers
        # it, with a 3-day guard). Check no RETURNED entry is a Temp dir -- not
        # the string "Temp" anywhere in the path, since the fake LOCALAPPDATA
        # (pytest tmp_path) itself lives under ...\AppData\Local\Temp on Windows.
        assert not any(p.name == "Temp" for p in out)

    def test_cache_dirs_windows_without_localappdata(self, monkeypatch):
        _force(monkeypatch, windows=True)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        out = pc.cache_dirs()
        assert len(out) == 1
        assert out[0].name == "INetCache"

    def test_cache_dirs_mac(self, monkeypatch):
        _force(monkeypatch, mac=True)
        out = pc.cache_dirs()
        assert out == [Path.home() / "Library/Caches"]

    def test_cache_dirs_linux_xdg(self, monkeypatch, tmp_path):
        _force(monkeypatch, linux=True)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert pc.cache_dirs() == [tmp_path]

    def test_log_dirs_mac(self, monkeypatch):
        _force(monkeypatch, mac=True)
        assert pc.log_dirs() == [Path.home() / "Library/Logs"]

    def test_log_dirs_windows_empty(self, monkeypatch):
        _force(monkeypatch, windows=True)
        assert pc.log_dirs() == []

    def test_log_dirs_linux(self, monkeypatch):
        _force(monkeypatch, linux=True)
        assert pc.log_dirs() == [Path.home() / ".local/state/log"]

    def test_temp_dirs_filters_nonexistent_and_deduplicates(
        self, monkeypatch, tmp_path
    ):
        # Neutral platform (no mac/linux extras): only gettempdir remains.
        _force(monkeypatch)
        fake = tmp_path / "tmp"
        fake.mkdir()
        monkeypatch.setattr(pc.tempfile, "gettempdir", lambda: str(fake))
        assert pc.temp_dirs() == [fake]

        # A non-existent temp dir is filtered out entirely.
        monkeypatch.setattr(
            pc.tempfile, "gettempdir", lambda: str(tmp_path / "missing")
        )
        assert pc.temp_dirs() == []


# -- _escape_ps -----------------------------------------------------------

class TestEscapePs:
    def test_single_quote_is_doubled(self):
        assert pc._escape_ps("x'y") == "x''y"
        assert pc._escape_ps("lancez 'macmon sentinel'") == \
            "lancez ''macmon sentinel''"

    def test_no_quote_unchanged(self):
        assert pc._escape_ps("plain") == "plain"


# -- dns_flush_cmds -------------------------------------------------------

class TestDnsFlushCmds:
    def test_macos(self, monkeypatch):
        _force(monkeypatch, mac=True)
        cmds = pc.dns_flush_cmds()
        assert ["dscacheutil", "-flushcache"] in cmds
        assert any(c[0] == "killall" for c in cmds)

    def test_windows(self, monkeypatch):
        _force(monkeypatch, windows=True)
        assert pc.dns_flush_cmds() == [["ipconfig", "/flushdns"]]

    def test_linux(self, monkeypatch):
        _force(monkeypatch, linux=True)
        cmds = pc.dns_flush_cmds()
        assert cmds == [["resolvectl", "flush-caches"]]
