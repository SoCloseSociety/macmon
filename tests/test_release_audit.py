"""Regression tests for the v1.3.0 pre-release audit.

Every kill path refuses the protected set (macmon's parent shell, Finder,
loginwindow); `sweep` spares live/listening servers; the CLI parses a
negative nice value and reports its version; the SSH check ignores commented
defaults; docker --json / --logs, ports on psutil, persistent startup
--disable, and the small polish items. Hermetic: psutil is stubbed, every
subprocess goes through a recorded run_cmd, console output is captured.
"""
import io
import json
import os
import sys
import types
from pathlib import Path

import psutil
import pytest
from rich.console import Console
from typer.testing import CliRunner

import macmon
from macmon_core import (
    aegis,
    config as config_mod,
    dashboard,
    docker_mgr,
    platform_compat,
    processes,
    security,
    sentinel,
    startup,
    uninstaller,
)

HIGH_PID = 999_999      # never a live PID on any test runner
HIGH_PID_2 = 999_998

# The three refusals every kill path must honour: macmon's own parent shell
# (by PID) and two system-critical names (by name, on a safe high PID).
PROTECTED_CASES = [
    pytest.param(os.getppid(), "node", id="parent-shell-pid"),
    pytest.param(HIGH_PID, "Finder", id="Finder"),
    pytest.param(HIGH_PID, "loginwindow", id="loginwindow"),
]


def _capture(monkeypatch, mod):
    buf = io.StringIO()
    monkeypatch.setattr(mod, "console", Console(file=buf, width=200, force_terminal=False, color_system=None))
    return buf


class FakeProc:
    """Stand-in for psutil.Process: enough surface for the kill paths and
    aegis._in_service (children + net_connections). terminate/kill record."""

    def __init__(self, pid, name, ppid=1, exe="", cmdline=None, conns=None,
                 username=None, status=psutil.STATUS_RUNNING, killed=None):
        self.pid = pid
        self._name = name
        self._status = status
        self._conns = conns or []
        self.killed = killed if killed is not None else []
        self.info = {
            "pid": pid, "ppid": ppid, "name": name, "cpu_percent": 1.0,
            "memory_info": types.SimpleNamespace(rss=50 * 1024 * 1024),
            "status": status, "create_time": 1_000_000.0,
            "cmdline": cmdline or [name], "exe": exe, "username": username,
        }

    def name(self):
        return self._name

    def status(self):
        return self._status

    def exe(self):
        return self.info["exe"]

    def terminal(self):
        return None

    def memory_info(self):
        return self.info["memory_info"]

    def children(self, recursive=False):
        return []

    def net_connections(self, kind="inet"):
        return self._conns

    def terminate(self):
        self.killed.append(("terminate", self.pid))

    def kill(self):
        self.killed.append(("kill", self.pid))


def _listen_conn(port=3000):
    return types.SimpleNamespace(status=psutil.CONN_LISTEN, family=2,
                                 laddr=types.SimpleNamespace(ip="127.0.0.1", port=port), pid=None)


def _install_process_table(monkeypatch, procs):
    """psutil.process_iter yields these; psutil.Process(pid) resolves to them."""
    by_pid = {p.pid: p for p in procs}

    def process_iter(attrs=None, ad_value=None):
        return list(procs)

    def process(pid):
        if pid not in by_pid:
            raise psutil.NoSuchProcess(pid)
        return by_pid[pid]

    monkeypatch.setattr(psutil, "process_iter", process_iter)
    monkeypatch.setattr(psutil, "Process", process)
    return by_pid


# ── 1. sweep spares live/listening servers ───────────────────────────────

class TestSweepSparesLiveServers:
    NODE_EXE = "/Users/neo/.nvm/versions/node/v20.0.0/bin/node"

    def _orphan(self, pid, conns, killed):
        return FakeProc(pid, "node", ppid=1, exe=self.NODE_EXE, conns=conns, killed=killed,
                        cmdline=["node", "/Users/neo/proj/server.js"])

    def test_listening_orphan_is_spared_and_reported(self, monkeypatch):
        killed = []
        listening = self._orphan(HIGH_PID, [_listen_conn(3000)], killed)
        idle = self._orphan(HIGH_PID_2, [], killed)
        _install_process_table(monkeypatch, [listening, idle])
        monkeypatch.setattr(processes, "log_action", lambda *a, **k: None)
        buf = _capture(monkeypatch, processes)

        n = processes._kill_orphans(force_yes=True)

        assert n == 1
        assert killed == [("terminate", HIGH_PID_2)]           # the idle one only
        assert "1 live/listening server spared" in buf.getvalue()

    def test_never_touch_candidate_is_spared(self, monkeypatch):
        killed = []
        # Same shape as a leaked orphan, but its cmdline is an MCP server: a
        # live agent session by AegisForge's judgment -> never signalled.
        mcp = FakeProc(HIGH_PID, "node", ppid=1, exe=self.NODE_EXE, killed=killed,
                       cmdline=["node", "/Users/neo/tools/mcp-server/index.js"])
        _install_process_table(monkeypatch, [mcp])
        monkeypatch.setattr(processes, "log_action", lambda *a, **k: None)
        buf = _capture(monkeypatch, processes)

        assert processes._kill_orphans(force_yes=True) == 0
        assert killed == []
        assert "No orphan dev processes found" in buf.getvalue()
        assert "1 live/listening server spared" in buf.getvalue()

    def test_unknown_service_state_is_spared(self, monkeypatch):
        """_in_service returns None (AccessDenied) -> skip, never kill."""
        killed = []
        cand = self._orphan(HIGH_PID, [], killed)
        _install_process_table(monkeypatch, [cand])
        monkeypatch.setattr(aegis, "_in_service", lambda pid, family=None: None)
        monkeypatch.setattr(processes, "log_action", lambda *a, **k: None)
        _capture(monkeypatch, processes)

        assert processes._kill_orphans(force_yes=True) == 0
        assert killed == []


# ── 2. every kill path honours _is_protected_target ──────────────────────

class TestKillCategoryPath:
    @pytest.mark.parametrize("pid,name", PROTECTED_CASES)
    def test_refuses_protected_but_kills_the_rest(self, monkeypatch, pid, name):
        killed = []
        protected = FakeProc(pid, name, killed=killed)
        victim = FakeProc(HIGH_PID_2, "node", killed=killed)
        _install_process_table(monkeypatch, [protected, victim])
        # Force both into the category so only the protected guard can spare one.
        monkeypatch.setattr(processes, "categorize_process", lambda n: "node")
        monkeypatch.setattr(processes, "log_action", lambda *a, **k: None)
        _capture(monkeypatch, processes)

        processes.kill_process(category="node", force_yes=True)

        assert killed == [("terminate", HIGH_PID_2)]

    def test_category_without_target_is_accepted_by_the_cli(self, monkeypatch):
        calls = []
        monkeypatch.setattr("macmon_core.processes.kill_process",
                            lambda target=None, category=None, force_yes=False: calls.append((target, category, force_yes)))
        r = CliRunner().invoke(macmon.app, ["kill", "--category", "node", "-y"])
        assert r.exit_code == 0, r.output
        assert calls == [(None, "node", True)]

    def test_neither_target_nor_category_is_an_error_not_a_scan(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("process table must not be scanned")
        monkeypatch.setattr(psutil, "process_iter", boom)
        buf = _capture(monkeypatch, processes)
        processes.kill_process()
        assert "--category" in buf.getvalue()


class TestDashboardKillPath:
    @pytest.mark.parametrize("pid,name", PROTECTED_CASES)
    def test_refuses_protected(self, monkeypatch, pid, name):
        def boom(_pid):
            raise AssertionError("psutil.Process must not be built for a protected target")
        monkeypatch.setattr(psutil, "Process", boom)
        monkeypatch.setattr(dashboard, "_top_procs", [{"pid": pid, "name": name}])
        monkeypatch.setattr(dashboard, "_kill_pending", {"pid": None, "time": 0})

        dashboard._action_kill_process(None, 0)   # first press: would arm the confirm
        dashboard._action_kill_process(None, 0)   # second press: would kill

        assert dashboard._action_status["msg"].startswith("Refusing: protected process")

    def test_refuses_its_own_python(self, monkeypatch):
        """SIGTERMing the dashboard's own interpreter left the TTY in cbreak."""
        monkeypatch.setattr(psutil, "Process", lambda pid: (_ for _ in ()).throw(AssertionError("no")))
        monkeypatch.setattr(dashboard, "_top_procs", [{"pid": os.getpid(), "name": "python3"}])
        monkeypatch.setattr(dashboard, "_kill_pending", {"pid": None, "time": 0})
        dashboard._action_kill_process(None, 0)
        dashboard._action_kill_process(None, 0)
        assert "Refusing" in dashboard._action_status["msg"]

    def test_ordinary_process_still_needs_two_presses_then_terminates(self, monkeypatch):
        killed = []
        _install_process_table(monkeypatch, [FakeProc(HIGH_PID, "node", killed=killed)])
        monkeypatch.setattr(dashboard, "_top_procs", [{"pid": HIGH_PID, "name": "node"}])
        monkeypatch.setattr(dashboard, "_kill_pending", {"pid": None, "time": 0})
        dashboard._action_kill_process(None, 0)
        assert killed == []
        dashboard._action_kill_process(None, 0)
        assert killed == [("terminate", HIGH_PID)]


class TestQuarantineKillPath:
    @pytest.mark.parametrize("pid,name", PROTECTED_CASES)
    def test_name_branch_refuses_protected(self, monkeypatch, pid, name):
        _install_process_table(monkeypatch, [FakeProc(pid, name)])
        monkeypatch.setattr(security, "_quarantine_one", lambda p: (_ for _ in ()).throw(AssertionError("quarantined a protected process")))
        buf = _capture(monkeypatch, security)
        security._quarantine_process(name)
        assert "not found" in buf.getvalue()

    def test_pid_branch_refuses_parent_shell(self, monkeypatch):
        monkeypatch.setattr(security, "_quarantine_one", lambda p: (_ for _ in ()).throw(AssertionError("quarantined the parent shell")))
        buf = _capture(monkeypatch, security)
        security._quarantine_process(str(os.getppid()))
        assert "Refusing to quarantine protected process" in buf.getvalue()

    def test_pid_branch_refuses_by_name(self, monkeypatch):
        _install_process_table(monkeypatch, [FakeProc(HIGH_PID, "Finder")])
        monkeypatch.setattr(security, "_quarantine_one", lambda p: (_ for _ in ()).throw(AssertionError("quarantined Finder")))
        buf = _capture(monkeypatch, security)
        security._quarantine_process(str(HIGH_PID))
        assert "Refusing to quarantine protected process Finder" in buf.getvalue()

    def test_ordinary_process_reaches_quarantine(self, monkeypatch):
        seen = []
        _install_process_table(monkeypatch, [FakeProc(HIGH_PID, "evil")])
        monkeypatch.setattr(security, "_quarantine_one", lambda p: seen.append(p.pid))
        _capture(monkeypatch, security)
        security._quarantine_process("evil")
        assert seen == [HIGH_PID]


class TestUninstallerKillPath:
    @pytest.mark.parametrize("pid,name", PROTECTED_CASES)
    def test_refuses_protected(self, monkeypatch, pid, name):
        killed = []
        _install_process_table(monkeypatch, [FakeProc(pid, name, killed=killed)])
        monkeypatch.setattr(uninstaller, "_get_bundle_executable", lambda app: "")
        monkeypatch.setattr(uninstaller.time, "sleep", lambda s: None)
        buf = _capture(monkeypatch, uninstaller)
        uninstaller._kill_app_processes(name)
        assert killed == []
        assert "Terminated" not in buf.getvalue()

    def test_ordinary_app_is_terminated_then_killed(self, monkeypatch):
        killed = []
        _install_process_table(monkeypatch, [FakeProc(HIGH_PID, "Spotify", killed=killed)])
        monkeypatch.setattr(uninstaller, "_get_bundle_executable", lambda app: "")
        monkeypatch.setattr(uninstaller.time, "sleep", lambda s: None)
        _capture(monkeypatch, uninstaller)
        uninstaller._kill_app_processes("Spotify")
        assert killed == [("terminate", HIGH_PID), ("kill", HIGH_PID)]


# ── 3. negative nice values parse ────────────────────────────────────────

class TestNiceParsesNegative:
    @pytest.mark.parametrize("value", ["-5", "-20", "10"])
    def test_value_reaches_renice(self, monkeypatch, value):
        calls = []
        monkeypatch.setattr("macmon_core.processes.renice_process", lambda t, v: calls.append((t, v)))
        r = CliRunner().invoke(macmon.app, ["nice", "node", value])
        assert r.exit_code == 0, r.output
        assert calls == [("node", int(value))]


# ── 4. SSH check: commented defaults are not findings ────────────────────

STOCK_SSHD_CONFIG = """\
#	$OpenBSD: sshd_config,v 1.104 2021/07/02 05:11:21 dtucker Exp $
# The strategy used for options in the default sshd_config shipped with
# OpenSSH is to specify options with their default value where possible.
#PermitRootLogin prohibit-password
#PasswordAuthentication yes
#PermitEmptyPasswords no
Subsystem	sftp	/usr/libexec/sftp-server
"""


class TestSshConfigCheck:
    def test_stock_commented_config_is_clean(self):
        assert security._sshd_config_issues(STOCK_SSHD_CONFIG) == []

    def test_active_directives_are_flagged(self):
        content = STOCK_SSHD_CONFIG + "PasswordAuthentication yes\nPermitRootLogin yes\n"
        issues = security._sshd_config_issues(content)
        assert any("Password auth" in i for i in issues)
        assert any("Root login" in i for i in issues)

    def test_indented_and_mixed_case_still_count(self):
        assert security._sshd_config_issues("   passwordauthentication YES\n")
        assert security._sshd_config_issues("PasswordAuthentication no\n") == []

    def test_check_passes_on_a_stock_mac(self, tmp_path, monkeypatch):
        cfg = tmp_path / "sshd_config"
        cfg.write_text(STOCK_SSHD_CONFIG)
        monkeypatch.setattr(security, "SSHD_CONFIG", cfg)
        monkeypatch.setattr(security, "_service_active", lambda label, port: False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert security._check_ssh_security()["status"] == "pass"
        cfg.write_text(STOCK_SSHD_CONFIG + "PasswordAuthentication yes\n")
        r = security._check_ssh_security()
        assert r["status"] == "warn" and "Password auth" in r["detail"]


# ── 5. version metadata ──────────────────────────────────────────────────

class TestVersion:
    def test_version_flag(self):
        r = CliRunner().invoke(macmon.app, ["--version"])
        assert r.exit_code == 0, r.output
        assert r.output.strip() == f"macmon {macmon.__version__}"

    def test_version_matches_pyproject(self):
        import tomllib
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            assert tomllib.load(f)["project"]["version"] == macmon.__version__ == "1.3.0"


# ── 6. dashboard --refresh is honoured ───────────────────────────────────

class TestDashboardRefresh:
    def test_explicit_flag_beats_config(self):
        assert dashboard._effective_refresh(7, {"dashboard": {"refresh_seconds": 2}}) == 7

    def test_config_when_no_flag(self):
        assert dashboard._effective_refresh(None, {"dashboard": {"refresh_seconds": 4}}) == 4
        assert dashboard._effective_refresh(None, {}) == 2

    def test_cli_passes_none_when_omitted_and_the_value_when_given(self, monkeypatch):
        seen = []
        monkeypatch.setattr("macmon_core.dashboard.run_dashboard", lambda refresh=None: seen.append(refresh))
        assert CliRunner().invoke(macmon.app, ["dashboard"]).exit_code == 0
        assert CliRunner().invoke(macmon.app, ["dashboard", "--refresh", "9"]).exit_code == 0
        assert seen == [None, 9]


# ── 7/8. docker --json and --logs stderr ─────────────────────────────────

CONTAINER_LINE = "abc123def456\tweb\tnginx:1.27\tUp 2 hours\t0.0.0.0:8080->80/tcp\t12MB\trunning"


def _fake_docker(monkeypatch, calls):
    def run_cmd(cmd, sudo=False, timeout=30):
        calls.append(cmd)
        if cmd[:2] == ["docker", "info"]:
            return "", "", 0
        if cmd[:3] == ["docker", "system", "df"]:
            return "Images\t3\t1\t900MB\t600MB (66%)\n", "", 0
        if cmd[:2] == ["docker", "ps"] and "status=exited" in cmd:
            return "", "", 0
        if cmd[:2] == ["docker", "ps"] and "{{.Mounts}}" in cmd[-1]:
            return "data\n", "", 0
        if cmd[:2] == ["docker", "ps"]:
            return CONTAINER_LINE + "\n", "", 0
        if cmd[:2] == ["docker", "images"] and "dangling=true" in cmd:
            return "sha1\nsha2\n", "", 0
        if cmd[:2] == ["docker", "images"]:
            return "nginx\t1.27\tabc\t50MB\t2 weeks ago\n", "", 0
        if cmd[:3] == ["docker", "volume", "ls"] and "-q" in cmd:
            return "data\ncache\n", "", 0
        if cmd[:3] == ["docker", "volume", "ls"]:
            return "data\tlocal\t/var/lib/docker/volumes/data\ncache\tlocal\t/x\n", "", 0
        if cmd[:3] == ["docker", "network", "ls"]:
            return "n1\tbridge\tbridge\tlocal\n", "", 0
        if cmd[:2] == ["docker", "logs"]:
            return "stdout line\n", "stderr line\n", 0
        return "", "", 1
    monkeypatch.setattr(docker_mgr, "run_cmd", run_cmd)


class TestDockerJson:
    def test_overview_json_is_parseable_and_only_json(self, monkeypatch):
        calls = []
        _fake_docker(monkeypatch, calls)
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr.run_docker(json_out=True)
        data = json.loads(buf.getvalue())
        assert data["running"][0]["name"] == "web"
        assert data["running"][0]["state"] == "running"
        assert data["stopped"] == []
        assert data["dangling_images"] == 2 and data["volumes"] == 2
        assert data["disk_usage"][0]["type"] == "Images"

    def test_containers_json(self, monkeypatch):
        _fake_docker(monkeypatch, [])
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr.run_docker(containers=True, json_out=True)
        rows = json.loads(buf.getvalue())
        assert rows == [{"id": "abc123def456", "name": "web", "image": "nginx:1.27", "status": "Up 2 hours",
                         "ports": "0.0.0.0:8080->80/tcp", "size": "12MB", "state": "running"}]

    @pytest.mark.parametrize("flag,key", [("images", "repository"), ("volumes", "name"), ("networks", "name")])
    def test_listings_json(self, monkeypatch, flag, key):
        _fake_docker(monkeypatch, [])
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr.run_docker(**{flag: True, "json_out": True})
        rows = json.loads(buf.getvalue())
        assert rows and key in rows[0]

    def test_volumes_json_marks_in_use(self, monkeypatch):
        _fake_docker(monkeypatch, [])
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr.run_docker(volumes=True, json_out=True)
        rows = {r["name"]: r["in_use"] for r in json.loads(buf.getvalue())}
        assert rows == {"data": True, "cache": False}

    def test_human_output_unchanged(self, monkeypatch):
        _fake_docker(monkeypatch, [])
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr.run_docker(containers=True)
        assert "All Containers" in buf.getvalue() and "web" in buf.getvalue()


class TestDockerLogsStderr:
    def test_stderr_is_shown(self, monkeypatch):
        _fake_docker(monkeypatch, [])
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr._docker_logs("web")
        out = buf.getvalue()
        assert "stdout line" in out and "stderr line" in out

    def test_stderr_only_is_not_reported_as_no_logs(self, monkeypatch):
        monkeypatch.setattr(docker_mgr, "run_cmd", lambda cmd, sudo=False, timeout=30: ("", "only stderr\n", 0))
        buf = _capture(monkeypatch, docker_mgr)
        docker_mgr._docker_logs("api")
        assert "only stderr" in buf.getvalue() and "No logs" not in buf.getvalue()


# ── 9. ports: psutil first, lsof fallback, honest when neither works ──────

def _inet_conn(port, pid):
    return types.SimpleNamespace(status=psutil.CONN_LISTEN, family=2, pid=pid,
                                 laddr=types.SimpleNamespace(ip="0.0.0.0", port=port))


class TestPortsPsutilFirst:
    @pytest.fixture(autouse=True)
    def _cfg(self, monkeypatch):
        monkeypatch.setattr(processes, "load_config", lambda: {"dev_ports": {"watch": [3000, 5173]}})
        monkeypatch.setattr(processes, "log_action", lambda *a, **k: None)

    def test_psutil_path_never_shells_out(self, monkeypatch):
        monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": [_inet_conn(3000, 4242), _inet_conn(3000, 4242), _inet_conn(9999, 1)])
        _install_process_table(monkeypatch, [FakeProc(4242, "node")])
        monkeypatch.setattr(processes, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("lsof must not run")))
        buf = _capture(monkeypatch, processes)
        processes.manage_ports()
        out = buf.getvalue()
        assert "4242" in out and "node" in out and "9999" not in out
        assert processes._port_holders([3000, 5173]) == {3000: [4242]}   # deduplicated

    def test_access_denied_falls_back_to_lsof(self, monkeypatch):
        def denied(kind="inet"):
            raise psutil.AccessDenied(pid=None)
        monkeypatch.setattr(psutil, "net_connections", denied)
        _install_process_table(monkeypatch, [FakeProc(4242, "node")])
        calls = []

        def run_cmd(cmd, sudo=False, timeout=30):
            calls.append(cmd)
            return ("4242\n", "", 0) if cmd[-1] == "tcp:3000" else ("", "", 1)
        monkeypatch.setattr(processes, "run_cmd", run_cmd)
        buf = _capture(monkeypatch, processes)
        processes.manage_ports()
        assert ["lsof", "-ti", "tcp:3000"] in calls
        assert "4242" in buf.getvalue()

    def test_no_lsof_says_so_instead_of_an_empty_table(self, monkeypatch):
        def denied(kind="inet"):
            raise psutil.AccessDenied(pid=None)
        monkeypatch.setattr(psutil, "net_connections", denied)
        monkeypatch.setattr(processes, "run_cmd", lambda cmd, sudo=False, timeout=30: ("", "Command not found: lsof", -2))
        buf = _capture(monkeypatch, processes)
        processes.manage_ports()
        assert "lsof not found" in buf.getvalue()
        assert "Port Usage" not in buf.getvalue()

    def test_free_port_uses_the_same_source(self, monkeypatch):
        killed = []
        monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": [_inet_conn(5173, 4242)])
        _install_process_table(monkeypatch, [FakeProc(4242, "node", killed=killed)])
        monkeypatch.setattr(processes, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("lsof must not run")))
        buf = _capture(monkeypatch, processes)
        processes.manage_ports(free_port=5173, force_yes=True)
        assert killed == [("terminate", 4242)]
        assert "Freed port 5173" in buf.getvalue()
        processes.manage_ports(free_port=3000, force_yes=True)
        assert "Port 3000 is not in use" in buf.getvalue()


# ── 10. startup --disable persists across logins ─────────────────────────

class TestStartupPersistentDisable:
    @pytest.fixture
    def calls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(startup, "run_cmd", lambda cmd, sudo=False, timeout=30: (calls.append(cmd), ("", "", 0))[1])
        monkeypatch.setattr(startup, "_get_uid", lambda: 501)
        monkeypatch.setattr(startup, "_plist_label", lambda plist: Path(plist).stem)
        monkeypatch.setattr(startup, "log_action", lambda *a, **k: None)
        _capture(monkeypatch, startup)
        return calls

    def test_disable_agent_boots_out_and_records_the_override(self, monkeypatch, calls):
        monkeypatch.setattr(startup, "_find_plist", lambda label: "/Users/neo/Library/LaunchAgents/com.foo.plist")
        startup._disable_item("com.foo")
        assert ["launchctl", "bootout", "gui/501/com.foo"] in calls
        assert ["launchctl", "disable", "gui/501/com.foo"] in calls

    def test_enable_agent_clears_the_override_before_bootstrap(self, monkeypatch, calls):
        plist = "/Users/neo/Library/LaunchAgents/com.foo.plist"
        monkeypatch.setattr(startup, "_find_plist", lambda label: plist)
        startup._enable_item("com.foo")
        assert calls.index(["launchctl", "enable", "gui/501/com.foo"]) < calls.index(["launchctl", "bootstrap", "gui/501", plist])

    def test_daemon_uses_sudo_and_the_system_domain(self, monkeypatch, calls):
        monkeypatch.setattr(startup, "_find_plist", lambda label: "/Library/LaunchDaemons/com.bar.plist")
        startup._disable_item("com.bar", force_yes=True)
        assert ["sudo", "launchctl", "bootout", "system/com.bar"] in calls
        assert ["sudo", "launchctl", "disable", "system/com.bar"] in calls

    def test_disable_succeeds_when_only_the_override_is_needed(self, monkeypatch):
        """Item not currently loaded (bootout fails) but the override sticks."""
        def run_cmd(cmd, sudo=False, timeout=30):
            return ("", "Could not find service", 113) if cmd[1] == "bootout" else ("", "", 0)
        monkeypatch.setattr(startup, "run_cmd", run_cmd)
        monkeypatch.setattr(startup, "_get_uid", lambda: 501)
        monkeypatch.setattr(startup, "_plist_label", lambda plist: "com.foo")
        monkeypatch.setattr(startup, "_find_plist", lambda label: "/Users/neo/Library/LaunchAgents/com.foo.plist")
        monkeypatch.setattr(startup, "log_action", lambda *a, **k: None)
        buf = _capture(monkeypatch, startup)
        startup._disable_item("com.foo")
        assert "Disabled com.foo" in buf.getvalue()


# ── 12. polish ───────────────────────────────────────────────────────────

class TestQuitRestartOffMac:
    @pytest.mark.parametrize("fn", [processes.quit_app, processes.restart_app])
    def test_degrades_with_the_standard_notice(self, monkeypatch, fn):
        monkeypatch.setattr(platform_compat, "OS_NAME", "Linux")
        monkeypatch.setattr(processes, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("osascript must not run")))
        monkeypatch.setattr(processes.time, "sleep", lambda s: None)
        buf = _capture(monkeypatch, processes)
        fn("Safari")
        assert "requires macOS" in buf.getvalue()


class TestConfigSetListValue:
    @pytest.fixture
    def cfg_path(self, tmp_path, monkeypatch):
        p = tmp_path / "config.toml"
        p.write_text(config_mod.DEFAULT_CONFIG)
        monkeypatch.setattr(config_mod, "CONFIG_PATH", p)
        monkeypatch.setattr(config_mod, "ensure_dirs", lambda: None)
        monkeypatch.setattr(config_mod, "log_action", lambda *a, **k: None)
        _capture(monkeypatch, config_mod)
        return p

    def test_array_stays_an_array(self, cfg_path):
        config_mod.set_config("watch", "[3000, 5173]")
        assert config_mod.load_config()["dev_ports"]["watch"] == [3000, 5173]

    def test_string_array(self, cfg_path):
        config_mod.set_config("essential_apps", '["code", "kitty"]')
        assert config_mod.load_config()["focus_mode"]["essential_apps"] == ["code", "kitty"]

    def test_scalars_and_strings_unchanged(self, cfg_path):
        config_mod.set_config("refresh_seconds", "5")
        config_mod.set_config("style", "growl")
        config_mod.set_config("safe_delete", "false")
        cfg = config_mod.load_config()
        assert cfg["dashboard"]["refresh_seconds"] == 5
        assert cfg["notifications"]["style"] == "growl"
        assert cfg["cleaner"]["safe_delete"] is False

    def test_malformed_array_is_quoted_not_written_raw(self, cfg_path):
        config_mod.set_config("style", "[not toml")
        assert config_mod.load_config()["notifications"]["style"] == "[not toml"


class TestSentinelPrefersPythonw:
    def test_swaps_in_pythonw_when_present(self, tmp_path):
        py = tmp_path / "python.exe"
        py.write_text("")
        cmd = [str(py), "macmon.py", "sentinel", "--sample"]
        assert sentinel._prefer_pythonw(cmd) == cmd                     # no pythonw yet
        (tmp_path / "pythonw.exe").write_text("")
        assert sentinel._prefer_pythonw(cmd) == [str(tmp_path / "pythonw.exe"), "macmon.py", "sentinel", "--sample"]
        assert sentinel._prefer_pythonw(["/usr/local/bin/macmon", "sentinel"]) == ["/usr/local/bin/macmon", "sentinel"]
        assert sentinel._prefer_pythonw([]) == []

    def test_schtasks_job_uses_pythonw(self, tmp_path, monkeypatch):
        py, pyw = tmp_path / "python.exe", tmp_path / "pythonw.exe"
        py.write_text("")
        pyw.write_text("")
        monkeypatch.setattr(sentinel, "IS_MAC", False)
        monkeypatch.setattr(sentinel, "IS_WINDOWS", True)
        monkeypatch.setattr(sentinel, "_sample_cmd", lambda: [str(py), "macmon.py", "sentinel", "--sample"])
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            return types.SimpleNamespace(returncode=0, stderr="", stdout="")
        monkeypatch.setattr(sentinel.subprocess, "run", run)
        ok, _ = sentinel._schedule_install()
        assert ok
        tr = seen[0][seen[0].index("/tr") + 1]
        assert str(pyw) in tr and "python.exe" not in tr.replace("pythonw.exe", "")


class TestAegisHomeProtectedIsOsAgnostic:
    def test_prefixes_use_the_native_separator(self):
        home = os.path.expanduser("~").lower()
        for pref in aegis._HOME_PROTECTED:
            assert pref.startswith(home) and pref.endswith(os.sep)
        assert any(pref.endswith(os.sep + ".claude" + os.sep) for pref in aegis._HOME_PROTECTED)

    @pytest.mark.parametrize("pref", ["/users/neo/.claude/", "c:\\users\\neo\\.claude\\"])
    def test_reason_names_the_directory_on_both_layouts(self, pref):
        assert aegis._home_protected_name(pref) == ".claude"

    @pytest.mark.skipif(sys.platform == "win32", reason="posix layout")
    def test_never_touch_reason_is_unchanged_on_posix(self, monkeypatch):
        monkeypatch.setattr(aegis, "_ME", "neo")
        monkeypatch.setattr(aegis, "_HOME_PROTECTED", ("/users/neo/.claude/",))
        p = {"pid": HIGH_PID, "ppid": 1, "name": "node", "cmd": "node /Users/neo/.claude/hooks/notify.js",
             "exe": "/Users/neo/.nvm/versions/node/v20/bin/node", "user": "neo"}
        assert aegis.never_touch(p) == "protected (~/.claude)"
