"""Unit tests for the pure parsing/logic helpers in the system modules.

Hermetic: no real network, no real subprocess, no live system. OS-specific
branches are driven by monkeypatching the platform flags, so these pass on
macOS, Linux and Windows alike.
"""
import time

import pytest

from macmon_core import security, gc as gcmod, uninstaller, health


# ── security._parse_lsof_line ────────────────────────────────────────────

class TestParseLsofLine:
    def test_simple_ipv4_listen(self):
        line = "rapportd  728  neo   8u  IPv4  0x1234  0t0  TCP  *:49152  (LISTEN)"
        process, pid, user, name_col, state = security._parse_lsof_line(line)
        assert process == "rapportd"
        assert pid == "728"
        assert user == "neo"
        assert name_col == "*:49152"
        assert state == "(LISTEN)"

    def test_command_name_with_spaces(self):
        # +c 0 can emit spaced command names; the parser anchors on the TYPE
        # token so the fields do not shift.
        line = "Google Chrome  1009  neo  40u  IPv4  0xabcd  0t0  TCP  127.0.0.1:64003  (LISTEN)"
        process, pid, user, name_col, state = security._parse_lsof_line(line)
        assert process == "Google Chrome"
        assert pid == "1009"
        assert name_col == "127.0.0.1:64003"
        assert state == "(LISTEN)"

    def test_ipv6_line(self):
        line = "sshd  500  root  3u  IPv6  0xdead  0t0  TCP  *:22  (LISTEN)"
        out = security._parse_lsof_line(line)
        assert out is not None
        assert out[0] == "sshd"
        assert out[3] == "*:22"

    def test_non_network_line_returns_none(self):
        assert security._parse_lsof_line("some header or garbage line") is None
        assert security._parse_lsof_line("") is None


# ── security._suspicious_port_hit ────────────────────────────────────────

class TestSuspiciousPortHit:
    def test_outbound_to_suspicious_remote_port(self):
        hit = security._suspicious_port_hit("192.168.1.5:54321->1.2.3.4:4444", "")
        assert hit is not None and hit[0] == 4444

    def test_outbound_to_benign_remote_port(self):
        assert security._suspicious_port_hit("192.168.1.5:54321->1.2.3.4:443", "") is None

    def test_local_listen_on_suspicious_port(self):
        hit = security._suspicious_port_hit("*:31337", "LISTEN")
        assert hit is not None and hit[0] == 31337

    def test_local_ephemeral_not_listening_is_ignored(self):
        # A local ephemeral port that is not a LISTEN and not an outbound
        # remote port must never be flagged.
        assert security._suspicious_port_hit("127.0.0.1:4444", "") is None

    def test_listen_on_benign_port(self):
        assert security._suspicious_port_hit("*:8080", "LISTEN") is None


# ── security._valid_ip (pf rule injection guard) ─────────────────────────

class TestValidIp:
    @pytest.mark.parametrize("ip", ["1.2.3.4", "10.0.0.0/8", "::1", "2001:db8::/32", "255.255.255.255"])
    def test_accepts_valid(self, ip):
        assert security._valid_ip(ip) is True

    @pytest.mark.parametrize("bad", ["not-an-ip", "1.2.3.4; block drop all", "any", "", "999.1.1.1", "1.2.3"])
    def test_rejects_invalid(self, bad):
        assert security._valid_ip(bad) is False


# ── security._rule_ips (exact-token pf rule parsing) ─────────────────────

class TestRuleIps:
    def test_extracts_from_and_to(self):
        line = "block drop from 1.2.3.4 to any"
        assert security._rule_ips(line) == {"1.2.3.4"}

    def test_both_directions(self):
        line = "block drop from 10.0.0.1 to 10.0.0.2"
        assert security._rule_ips(line) == {"10.0.0.1", "10.0.0.2"}

    def test_any_is_excluded(self):
        assert security._rule_ips("block drop from any to any") == set()

    def test_exact_token_no_substring_match(self):
        # The whole point: blocking 1.2.3.45 must not later match 1.2.3.4.
        ips = security._rule_ips("block drop from 1.2.3.45 to any")
        assert "1.2.3.45" in ips
        assert "1.2.3.4" not in ips


# ── gc._pip_cache_dirs (per-OS) ──────────────────────────────────────────

class TestPipCacheDirs:
    def test_macos(self, monkeypatch):
        monkeypatch.setattr("macmon_core.platform_compat.IS_MAC", True)
        monkeypatch.setattr("macmon_core.platform_compat.IS_WINDOWS", False)
        out = gcmod._pip_cache_dirs()
        assert len(out) == 1 and out[0].parts[-2:] == ("Library", "Caches") or "pip" in out[0].name

    def test_windows(self, monkeypatch):
        monkeypatch.setattr("macmon_core.platform_compat.IS_MAC", False)
        monkeypatch.setattr("macmon_core.platform_compat.IS_WINDOWS", True)
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\x\AppData\Local")
        out = gcmod._pip_cache_dirs()
        assert len(out) == 1
        assert "pip" in str(out[0]).lower() and "Cache" in str(out[0])

    def test_linux(self, monkeypatch):
        monkeypatch.setattr("macmon_core.platform_compat.IS_MAC", False)
        monkeypatch.setattr("macmon_core.platform_compat.IS_WINDOWS", False)
        out = gcmod._pip_cache_dirs()
        assert len(out) == 1 and out[0].name == "pip"


# ── gc._latest_mtime ─────────────────────────────────────────────────────

class TestLatestMtime:
    def test_no_existing_paths_returns_zero(self, tmp_path):
        assert gcmod._latest_mtime([tmp_path / "nope", tmp_path / "also-nope"]) == 0.0

    def test_returns_max_mtime(self, tmp_path):
        old = tmp_path / "old.txt"
        new = tmp_path / "new.txt"
        old.write_text("a")
        new.write_text("b")
        past = time.time() - 10000
        import os
        os.utime(old, (past, past))
        latest = gcmod._latest_mtime([old, new])
        assert latest == pytest.approx(new.stat().st_mtime)
        assert latest > old.stat().st_mtime

    def test_mixed_existing_and_missing(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("x")
        latest = gcmod._latest_mtime([tmp_path / "ghost", real])
        assert latest == pytest.approx(real.stat().st_mtime)


# ── uninstaller._trash_or_rm (permanent must not over-report success) ─────

class TestTrashOrRmPermanent:
    def test_permanent_delete_of_real_file_succeeds(self, tmp_path):
        f = tmp_path / "gone.txt"
        f.write_text("x")
        assert uninstaller._trash_or_rm(f, permanent=True) is True
        assert not f.exists()

    def test_permanent_delete_returns_false_when_removal_fails(self, tmp_path, monkeypatch):
        # rmtree(ignore_errors=True) swallows failures; the function must still
        # report False (not True) when the path survives, or the caller counts
        # phantom freed bytes.
        d = tmp_path / "stuck"
        d.mkdir()
        monkeypatch.setattr(uninstaller.shutil, "rmtree", lambda *a, **k: None)
        assert uninstaller._trash_or_rm(d, permanent=True) is False
        assert d.exists()


# ── health._check_battery (no fabricated all-clear off-macOS) ─────────────

class TestCheckBattery:
    def test_returns_none_off_mac_even_with_a_battery(self, monkeypatch):
        # A Windows/Linux laptop has a real battery, but cycle count / capacity
        # come from macOS system_profiler only. Off-mac the check must abstain
        # rather than report a fabricated "100% / 0 cycles" pass.
        monkeypatch.setattr(health, "IS_MAC", False)
        monkeypatch.setattr(health.psutil, "sensors_battery", lambda: object())
        assert health._check_battery() is None

    def test_returns_none_when_no_battery(self, monkeypatch):
        monkeypatch.setattr(health.psutil, "sensors_battery", lambda: None)
        assert health._check_battery() is None
