"""Engine efficiency + hardening locks (v1.5.x optimisation pass).

Hermetic and cross-platform: subprocess.run is recorded, psutil is stubbed,
files live under tmp_path. Each class locks one change:

  - run_cmd is never interactive: stdin=/dev/null, sudo is always `sudo -n`,
    no `shell=True` anywhere in the engine
  - dir_size / the cleaner scans walk with ONE lstat per entry and keep the
    former semantics (symlinks neither followed nor counted, hardlinks once)
  - the sentinel sampler reads the process table ONCE per sample and every
    probe derives from that table; an ollama with nothing loaded skips the
    runner probe
  - the scheduler/notifier plumbing goes through _run (stdin closed, bounded)
  - LaunchAgent plists XML-escape their argv ('&' in a path)
  - the ten security checks run concurrently but report in the fixed order
  - a docker container reference that would parse as a flag is refused
"""
import inspect
import os
import plistlib
import re
import subprocess
import time
import types
from pathlib import Path

import psutil
import pytest

from macmon_core import aegis, cleaner, docker_mgr, security, sentinel, utils

ENGINE_DIR = Path(utils.__file__).parent


def _ok(stdout="", stderr="", rc=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


def row(pid, name, cmd="", cpu=0.0, rss=0, ct=1000.0, ppid=1, exe="", user="neo"):
    """One aegis.scan() row."""
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd or exe, "exe": exe, "ct": float(ct),
            "cpu": cpu, "rss": rss, "user": user, "status": "running", "tty": None}


# ── run_cmd: never interactive ───────────────────────────────────────────

class TestRunCmdNeverInteractive:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen = []

        def run(cmd, **kw):
            seen.append((list(cmd), kw))
            return _ok("out")
        monkeypatch.setattr(utils.subprocess, "run", run)
        return seen

    def test_stdin_is_devnull_output_captured_and_bounded(self, calls):
        assert utils.run_cmd(["ls", "-l"], timeout=7) == ("out", "", 0)
        cmd, kw = calls[0]
        assert cmd == ["ls", "-l"]
        assert kw["stdin"] is subprocess.DEVNULL
        assert kw["capture_output"] is True and kw["text"] is True and kw["timeout"] == 7

    @pytest.mark.parametrize("cmd,sudo,expected", [
        (["purge"], True, ["sudo", "-n", "purge"]),                               # sudo=True
        (["-n", "purge"], True, ["sudo", "-n", "purge"]),                         # caller already passed -n
        (["sudo", "launchctl", "bootout", "system/x"], False,
         ["sudo", "-n", "launchctl", "bootout", "system/x"]),                     # explicit sudo argv
        (["sudo", "-n", "pfctl", "-s", "info"], False, ["sudo", "-n", "pfctl", "-s", "info"]),
        (["sudo", "-n", "pfctl"], True, ["sudo", "-n", "pfctl"]),                  # never a double sudo
        (["lsof", "-ti", "tcp:3000"], False, ["lsof", "-ti", "tcp:3000"]),        # untouched otherwise
    ])
    def test_every_sudo_invocation_is_non_interactive(self, calls, cmd, sudo, expected):
        utils.run_cmd(cmd, sudo=sudo)
        assert calls[0][0] == expected

    def test_sudo_argv_does_not_mutate_the_caller_list(self):
        cmd = ["sudo", "launchctl", "list"]
        assert utils._sudo_argv(cmd, False) == ["sudo", "-n", "launchctl", "list"]
        assert cmd == ["sudo", "launchctl", "list"]

    def test_no_shell_true_anywhere_in_the_engine(self):
        offenders = [p.name for p in ENGINE_DIR.glob("*.py") if "shell=True" in p.read_text(encoding="utf-8")]
        assert offenders == []

    def test_run_cmd_error_codes(self, monkeypatch):
        def timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        monkeypatch.setattr(utils.subprocess, "run", timeout)
        assert utils.run_cmd(["x"]) == ("", "Command timed out", -1)

        def missing(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        monkeypatch.setattr(utils.subprocess, "run", missing)
        assert utils.run_cmd(["pfctl"], sudo=True) == ("", "Command not found: sudo", -2)


# ── admin_run: the user-initiated privileged path (native auth dialog) ────
class TestAdminRun:
    @pytest.fixture
    def rec(self, monkeypatch):
        from macmon_core import platform_compat
        monkeypatch.setattr(platform_compat, "IS_MAC", True)   # force the osascript path
        seen = []
        monkeypatch.setattr(utils.subprocess, "run",
                            lambda cmd, **kw: (seen.append((list(cmd), kw)), _ok("done"))[1])
        return seen

    def test_builds_a_single_authorized_osascript(self, rec):
        out, err, rc = utils.admin_run(["/usr/sbin/purge"])
        cmd, kw = rec[0]
        assert cmd[0] == "osascript" and cmd[1] == "-e"
        assert cmd[2] == 'do shell script "/usr/sbin/purge" with administrator privileges'
        assert kw["stdin"] is subprocess.DEVNULL and (out, rc) == ("done", 0)

    def test_multiple_commands_share_one_authorization(self, rec):
        fw = "/usr/libexec/ApplicationFirewall/socketfilterfw"
        utils.admin_run([[fw, "--add", "/Applications/X.app"], [fw, "--blockapp", "/Applications/X.app"]])
        script = rec[0][0][2]
        assert script.count("do shell script") == 1 and " && " in script   # one dialog, both commands

    def test_arguments_are_shell_quoted_no_injection(self, rec):
        evil = "/tmp/a b; rm -rf ~"
        utils.admin_run([["/bin/echo", evil]])
        script = rec[0][0][2]
        # the dangerous value is a single quoted token, never bare shell syntax
        assert "'/tmp/a b; rm -rf ~'" in script
        assert "; rm -rf ~ with" not in script

    def test_off_mac_falls_back_to_sudo_dash_n(self, monkeypatch):
        from macmon_core import platform_compat
        monkeypatch.setattr(platform_compat, "IS_MAC", False)
        seen = []
        monkeypatch.setattr(utils.subprocess, "run",
                            lambda cmd, **kw: (seen.append(list(cmd)), _ok("x"))[1])
        utils.admin_run([["pfctl", "-E"]])
        assert seen[0] == ["sudo", "-n", "pfctl", "-E"]   # no native dialog off macOS

    def test_user_cancel_is_reported(self, monkeypatch):
        from macmon_core import platform_compat
        monkeypatch.setattr(platform_compat, "IS_MAC", True)
        monkeypatch.setattr(utils.subprocess, "run",
                            lambda cmd, **kw: _ok("", "User canceled. (-128)", 1))
        out, err, rc = utils.admin_run(["/usr/sbin/purge"])
        assert rc == 1 and "cancel" in err.lower()


# ── dir_size + cleaner scans: one lstat per entry, same semantics ────────

def _legacy_dir_size(path: Path) -> int:
    """The former rglob-based implementation (3 stats per entry), kept here as
    the reference the walker must agree with."""
    total = 0
    seen = set()
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    st = entry.stat()
                    if st.st_nlink > 1:
                        key = (st.st_dev, st.st_ino)
                        if key in seen:
                            continue
                        seen.add(key)
                    total += st.st_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total


def _tree(tmp_path: Path) -> tuple[Path, int]:
    """a/1.txt (10 B), a/b/2.bin (100 B), a/b/3 = hardlink of 1.txt (counted
    once), a/link -> b (dir symlink: not followed), a/flink -> 1.txt (not
    counted), a/broken -> missing. Expected total: 110 B."""
    a = tmp_path / "a"
    (a / "b").mkdir(parents=True)
    (a / "1.txt").write_bytes(b"x" * 10)
    (a / "b" / "2.bin").write_bytes(b"y" * 100)
    expected = 110
    try:
        os.link(a / "1.txt", a / "b" / "3")
    except OSError:
        pass                                                    # FS without hardlinks: still 110
    try:
        os.symlink(a / "b", a / "link", target_is_directory=True)
        os.symlink(a / "1.txt", a / "flink")
        os.symlink(a / "missing", a / "broken")
    except (OSError, NotImplementedError):
        pass                                                    # no symlink privilege (Windows): still 110
    return a, expected


class TestDirSizeWalker:
    def test_counts_regular_files_once_and_never_follows_symlinks(self, tmp_path):
        a, expected = _tree(tmp_path)
        assert utils.dir_size(a) == expected
        assert utils.dir_size(a) == _legacy_dir_size(a)

    def test_one_lstat_per_entry_no_stat_or_is_file(self, tmp_path, monkeypatch):
        a, expected = _tree(tmp_path)
        lstats, stats = [], []
        real_lstat, real_stat = os.lstat, os.stat
        root = str(a)
        monkeypatch.setattr(os, "lstat", lambda p, *x, **k: (lstats.append(p), real_lstat(p, *x, **k))[1])
        monkeypatch.setattr(os, "stat", lambda p, *x, **k: (stats.append(p), real_stat(p, *x, **k))[1])
        assert utils.dir_size(a) == expected
        # one lstat per file (ours) + one per directory (os.walk's own islink
        # guard before descending); never a following stat() / is_file()
        assert len([p for p in lstats if str(p).startswith(root)]) == sum(len(files) + len(dirs) for _, dirs, files in os.walk(a))
        assert [p for p in stats if str(p).startswith(root)] == []

    def test_missing_or_file_path_is_zero(self, tmp_path):
        assert utils.dir_size(tmp_path / "nope") == 0
        f = tmp_path / "f"
        f.write_bytes(b"z" * 5)
        assert utils.dir_size(f) == 0


class TestCleanerScansWalker:
    def test_scan_old_files_keeps_the_guards(self, tmp_path):
        d = tmp_path / "logs"
        (d / "sub").mkdir(parents=True)
        old = time.time() - 30 * 86400
        for name in ("old.log", "sub/old2.log", ".hidden.log", "live.lock", "live.pid", "s.sock"):
            p = d / name
            p.write_bytes(b"a" * 7)
            os.utime(p, (old, old))
        (d / "fresh.log").write_bytes(b"b" * 3)
        try:
            os.symlink(d / "old.log", d / "link.log")
        except (OSError, NotImplementedError):
            pass
        size, count, paths = cleaner._scan_old_files(d, time.time() - 7 * 86400)
        assert sorted(Path(p).relative_to(d).as_posix() for p in paths) == ["old.log", "sub/old2.log"]
        assert (size, count) == (14, 2)

    def test_scan_dir_all_only_regular_files(self, tmp_path):
        d = tmp_path / "crash"
        (d / "sub").mkdir(parents=True)
        (d / "a.crash").write_bytes(b"1" * 4)
        (d / "sub" / "b.crash").write_bytes(b"2" * 6)
        try:
            os.symlink(d / "a.crash", d / "c.crash")
            os.symlink(d / "sub", d / "subl", target_is_directory=True)
        except (OSError, NotImplementedError):
            pass
        size, count, paths = cleaner._scan_dir_all(d)
        assert sorted(Path(p).relative_to(d).as_posix() for p in paths) == ["a.crash", "sub/b.crash"]
        assert (size, count) == (10, 2)

    def test_unreadable_root_is_empty_not_an_error(self, tmp_path):
        assert cleaner._scan_dir_all(tmp_path / "missing") == (0, 0, [])
        assert cleaner._scan_old_files(tmp_path / "missing", time.time()) == (0, 0, [])

    def test_protected_temp_names(self):
        assert cleaner._is_protected_temp_file(Path("/tmp/.X11-unix"))
        assert cleaner._is_protected_temp_file(Path("/tmp/app.LOCK"))
        assert not cleaner._is_protected_temp_file(Path("/tmp/report.txt"))


# ── sentinel sampler: one process-table pass ─────────────────────────────

CLAUDE = "/Users/neo/.vscode/extensions/anthropic.claude-code-2.1.281-darwin-arm64/resources/native-binary/claude"
MB = 1024 * 1024

TABLE = [
    row(1, "launchd", exe="/sbin/launchd", user="root", ppid=0),
    row(100, "claude", cmd=f"{CLAUDE} --resume", cpu=0.2, rss=300 * MB, ct=5000.0),
    row(101, "claude", cmd=f"{CLAUDE}", cpu=12.0, rss=200 * MB, ct=6000.0),
    row(102, "codex", cmd="/Applications/ChatGPT.app/Contents/openai.chatgpt codex serve", rss=50 * MB),
    row(103, "node", cmd="/usr/local/bin/node /Users/neo/tools/tradingview-mcp/server.js", rss=70 * MB),
    row(104, "com.apple.Virtualization.VirtualMachine",
        cmd="/System/Library/com.apple.Virtualization.VirtualMachine --vm", rss=int(3.5e9)),
    row(105, "qemu-system-aarch64", cmd="qemu com.docker.virtualization", rss=int(1.0e9)),
    row(106, "ollama", cmd="/opt/homebrew/bin/ollama runner --model x", cpu=1.0, rss=10 * MB),
    row(107, "Google Chrome Helper (Renderer) extra long name", cmd="chrome --type=renderer", cpu=48.0, rss=900 * MB),
]


class TestSamplerProbesShareOneTable:
    def test_ai_fleet_from_table(self):
        assert sentinel._ai_fleet(TABLE) == {"claude": [2, 500], "codex": [1, 50], "mcp": [1, 70]}

    def test_top_proc_from_table(self, monkeypatch):
        monkeypatch.setattr(sentinel.psutil, "cpu_count", lambda *a, **k: 4)
        assert sentinel._top_proc(TABLE) == ("Google Chrome Helper (Re", 12.0, 900)

    def test_vm_status_from_table(self):
        assert sentinel._vm_status(TABLE) == {"gb": 4.5, "owner": "com.apple.Virtualization"}

    def test_claude_sessions_from_table(self):
        assert sentinel._claude_sessions(TABLE) == [
            {"pid": 100, "cpu": 0.2, "rss": 300 * MB, "start": 5000.0},
            {"pid": 101, "cpu": 12.0, "rss": 200 * MB, "start": 6000.0},
        ]

    def test_ollama_runner_pids_from_table(self):
        assert sentinel._ollama_runner_pids(TABLE) == [106]

    def test_standalone_call_scans_itself(self, monkeypatch):
        monkeypatch.setattr(sentinel.aegis, "scan", lambda: TABLE)
        assert sentinel._ai_fleet() == sentinel._ai_fleet(TABLE)
        assert sentinel._claude_sessions()[0]["pid"] == 100

    def test_observe_uses_the_given_table_without_rescanning(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("psutil.process_iter must not run")
        monkeypatch.setattr(psutil, "process_iter", boom)
        forge = aegis.observe({}, dict(sentinel.DEFAULTS), TABLE)
        assert forge["record"]["fam"] == {"node": 1}
        assert forge["record"]["topn"][0][0] == "Google Chrome Helper (Re"

    def test_ollama_with_nothing_loaded_skips_the_runner_probe(self, monkeypatch):
        monkeypatch.setattr(sentinel.subprocess, "run", lambda cmd, **kw: _ok("NAME  ID  SIZE  PROCESSOR  UNTIL\n"))
        monkeypatch.setattr(sentinel, "_ollama_runner_pids", lambda procs=None: (_ for _ in ()).throw(AssertionError("runner probe ran")))
        monkeypatch.setattr(sentinel.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("slept")))
        assert sentinel._ollama_status(TABLE) == {"gb": 0.0, "models": [], "busy": False}

    def test_ollama_with_a_model_probes_the_runners_from_the_table(self, monkeypatch):
        monkeypatch.setattr(sentinel.subprocess, "run",
                            lambda cmd, **kw: _ok("NAME  ID  SIZE  PROCESSOR  UNTIL\nllama3:8b  abc  4.7 GB  100% GPU  4 min\n"))
        probed = []

        class Runner:
            def __init__(self, pid):
                probed.append(pid)
                self.n = 0

            def cpu_percent(self, interval=None):
                self.n += 1
                return 0.0 if self.n == 1 else 42.0
        monkeypatch.setattr(sentinel.psutil, "Process", Runner)
        monkeypatch.setattr(sentinel.time, "sleep", lambda s: None)
        out = sentinel._ollama_status(TABLE)
        assert out == {"gb": 4.7, "models": ["llama3:8b"], "busy": True}
        assert probed == [106]

    def test_run_sample_scans_the_process_table_exactly_once(self, tmp_path, monkeypatch):
        for name in ("METRICS", "ASTATE", "SEQ", "CONF", "ALERTS_LOG"):
            monkeypatch.setattr(sentinel, name, tmp_path / name.lower())
        monkeypatch.setattr(sentinel, "MACMON_DIR", tmp_path)
        ns = types.SimpleNamespace
        ps = sentinel.psutil
        iters, scans = [], []
        monkeypatch.setattr(ps, "process_iter", lambda *a, **k: iters.append(a) or [])
        monkeypatch.setattr(ps, "cpu_percent", lambda interval=None: 12.0)
        monkeypatch.setattr(ps, "cpu_count", lambda *a, **k: 4)
        monkeypatch.setattr(ps, "virtual_memory", lambda: ns(percent=60.0))
        monkeypatch.setattr(ps, "swap_memory", lambda: ns(used=1e9))
        monkeypatch.setattr(ps, "disk_usage", lambda p: ns(free=100e9))
        monkeypatch.setattr(sentinel, "load_average", lambda: (1.0, 1.0, 1.0))
        monkeypatch.setattr(sentinel, "_ping_rtt", lambda: None)
        monkeypatch.setattr(sentinel, "_notify", lambda t, m: None)
        monkeypatch.setattr(sentinel.subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError(cmd[0])))
        monkeypatch.setattr(sentinel.aegis, "scan", lambda: scans.append(1) or [dict(r) for r in TABLE])

        sentinel.run_sample()

        assert len(scans) == 1                      # the shared table
        assert len(iters) == 1                      # the cpu-counter priming pass, nothing else
        rec = sentinel._load_tail(1)[-1]
        assert rec["claude"] == [2, 500] and rec["codex"] == [1, 50] and rec["mcp"] == [1, 70]
        assert rec["top"] == ["Google Chrome Helper (Re", 12.0, 900]
        assert rec["vm_gb"] == 4.5 and rec["ollama_gb"] == 0.0
        assert rec["fam"] == {"node": 1} and rec["ncpu"] == 4
        # the idle streaks were built from the SAME table (0.5 s window): 100 idle, 101 busy
        import json
        astate = json.loads(sentinel.ASTATE.read_text())
        assert astate["idle_streak"] == {"100": 1, "101": 0}

    def test_prime_pass_touches_cpu_percent_only(self, monkeypatch):
        seen = []
        monkeypatch.setattr(sentinel.psutil, "process_iter", lambda attrs=None, **k: seen.append(attrs) or [])
        sentinel._prime_cpu_counters()
        assert seen == [["cpu_percent"]]


# ── scheduler / notifier subprocess hygiene ──────────────────────────────

class TestSentinelSubprocessHygiene:
    def test_run_closes_stdin_and_bounds_time(self, monkeypatch):
        seen = []
        monkeypatch.setattr(sentinel.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw)) or _ok())
        sentinel._run(["launchctl", "list"])
        cmd, kw = seen[0]
        assert cmd == ["launchctl", "list"]
        assert kw["stdin"] is subprocess.DEVNULL and kw["timeout"] == 15 and kw["capture_output"] is True
        sentinel._run(["crontab", "-"], input="* * * * * x\n", timeout=3)
        cmd, kw = seen[1]
        assert kw["input"] == "* * * * * x\n" and "stdin" not in kw and kw["timeout"] == 3

    def test_plumbing_never_calls_subprocess_run_directly(self):
        for fn in (sentinel._scheduler_has, sentinel._schedule_install, sentinel._schedule_remove,
                   sentinel._build_notifier, sentinel._notify, sentinel._ping_rtt, sentinel._ollama_status,
                   sentinel._unload_ollama, sentinel._remediate, sentinel._purge_nopasswd_ready):
            assert "subprocess.run(" not in inspect.getsource(fn), fn.__name__

    def test_scheduler_query_timeout_reads_as_not_registered(self, monkeypatch):
        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        monkeypatch.setattr(sentinel.subprocess, "run", hang)
        assert sentinel._scheduler_has(sentinel.MONITOR_LABEL) is False

    def test_scheduler_install_timeout_is_a_clean_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sentinel, "IS_MAC", False)
        monkeypatch.setattr(sentinel, "IS_WINDOWS", True)
        monkeypatch.setattr(sentinel, "_sample_cmd", lambda: ["python.exe", "macmon.py", "sentinel", "--sample"])

        def hang(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        monkeypatch.setattr(sentinel.subprocess, "run", hang)
        ok, note = sentinel._schedule_install()
        assert ok is False and "timed out" in note
        sentinel._schedule_remove()                 # must not raise either


# ── LaunchAgent plists are well-formed for any path ──────────────────────

class TestPlistEscaping:
    def test_sentinel_plist_round_trips_special_characters(self):
        args = ["/Users/neo/Dev & Co/.venv/bin/python", "/Users/neo/<repo>/macmon.py", 'a"b', "sentinel", "--sample"]
        data = plistlib.loads(sentinel._plist("co.soclose.macmon.monitor", args, 60).encode())
        assert data["ProgramArguments"] == args
        assert data["Label"] == "co.soclose.macmon.monitor" and data["StartInterval"] == 60

    def test_autoclean_plist_round_trips_special_characters(self, tmp_path, monkeypatch):
        home = tmp_path / "Dev & Co"
        home.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        monkeypatch.setattr(cleaner.sys, "executable", str(home / "py<3>" / "python"))
        monkeypatch.setattr(cleaner, "IS_MAC", True)
        calls = []
        monkeypatch.setattr(cleaner, "run_cmd", lambda cmd, **kw: (calls.append(cmd), ("", "", 0))[1])
        monkeypatch.setattr(cleaner, "console", types.SimpleNamespace(print=lambda *a, **k: None))
        cleaner._setup_schedule()
        plist = home / "Library/LaunchAgents/com.macmon.autoclean.plist"
        with open(plist, "rb") as f:
            data = plistlib.load(f)
        assert data["ProgramArguments"][0] == str(home / "py<3>" / "python")
        assert data["StandardOutPath"] == f"{home}/.macmon/autoclean.log"
        assert calls[-1] == ["launchctl", "load", str(plist)]


# ── security checks: concurrent, reported in the fixed order ─────────────

class TestSecurityChecksConcurrent:
    STUBS = {
        "_check_firewall": {"name": "macOS Firewall", "status": "fail", "detail": "DISABLED"},
        "_check_sip": {"name": "System Integrity Protection", "status": "pass", "detail": "Enabled"},
        "_check_gatekeeper": {"name": "Gatekeeper", "status": "pass", "detail": "Enabled"},
        "_check_filevault": {"name": "FileVault Encryption", "status": "pass", "detail": "Enabled"},
        "_find_suspicious_connections": [],
        "_find_remote_tools": ["PID 9: TeamViewer (10.0 MB)"],
        "_find_suspicious_processes": [],
        "_find_suspicious_launch_items": [],
        "_check_sharing": {"name": "Sharing Services", "status": "warn", "detail": "Active: Screen Sharing"},
        "_check_ssh_security": {"name": "SSH Security", "status": "pass", "detail": "SSH not running"},
    }
    NARRATION = ["Checking firewall...", "Checking System Integrity Protection...", "Checking Gatekeeper...",
                 "Checking FileVault encryption...", "Scanning network connections...",
                 "Scanning for remote access tools...", "Scanning for suspicious processes...",
                 "Scanning startup items...", "Checking sharing services...", "Checking SSH..."]

    def _install(self, monkeypatch, delay=0.0, raise_in=None):
        started = []
        for name, value in self.STUBS.items():
            def probe(name=name, value=value):
                started.append(name)
                if name == raise_in:
                    raise RuntimeError(name)
                time.sleep(delay)
                return value
            monkeypatch.setattr(security, name, probe)
        return started

    def test_findings_order_content_and_score_match_the_sequential_run(self, monkeypatch):
        self._install(monkeypatch)
        said = []
        score, findings = security._security_checks(said.append)
        assert said == self.NARRATION
        assert [f["name"] for f in findings] == [
            "macOS Firewall", "System Integrity Protection", "Gatekeeper", "FileVault Encryption",
            "Suspicious Connections", "Remote Access Tools", "Suspicious Processes",
            "Suspicious Startup Items", "Sharing Services", "SSH Security"]
        assert findings[5] == {"name": "Remote Access Tools", "status": "warn",
                               "detail": "1 remote tool(s) running", "items": ["PID 9: TeamViewer (10.0 MB)"]}
        assert findings[4] == {"name": "Suspicious Connections", "status": "pass", "detail": "No suspicious connections"}
        assert score == 100 - 15 - 5 - 5             # firewall fail, remote tool, sharing warn

    def test_probes_run_concurrently(self, monkeypatch):
        started = self._install(monkeypatch, delay=0.15)
        t0 = time.perf_counter()
        security._security_checks()
        elapsed = time.perf_counter() - t0
        assert sorted(started) == sorted(self.STUBS)
        assert elapsed < 1.0, f"sequential would be ~1.5 s, took {elapsed:.2f}s"   # measured: 0.72 s -> ~0.25 s live

    def test_a_failing_probe_still_raises(self, monkeypatch):
        self._install(monkeypatch, raise_in="_check_sip")
        with pytest.raises(RuntimeError, match="_check_sip"):
            security._security_checks()

    def test_no_progress_callback_is_fine(self, monkeypatch):
        self._install(monkeypatch)
        assert security._security_checks()[0] == 75


# ── docker: a container reference is never a flag ────────────────────────

class TestDockerContainerRef:
    @pytest.mark.parametrize("ref", ["web", "proj-web-1", "a1b2c3d4e5f6", "my.svc_2", "A"])
    def test_accepts_docker_names_and_ids(self, ref):
        assert docker_mgr._valid_container_ref(ref) is True

    @pytest.mark.parametrize("ref", ["", "-f", "--help", "a b", "x;rm -rf /", ".hidden", "_x", "a\n", "é"])
    def test_rejects_flags_and_junk(self, ref):
        assert docker_mgr._valid_container_ref(ref) is False

    @pytest.mark.parametrize("fn", [docker_mgr._docker_restart, docker_mgr._docker_logs])
    def test_refused_before_docker_runs(self, monkeypatch, fn):
        monkeypatch.setattr(docker_mgr, "run_cmd", lambda *a, **k: (_ for _ in ()).throw(AssertionError("docker ran")))
        out = []
        monkeypatch.setattr(docker_mgr, "console", types.SimpleNamespace(print=lambda *a, **k: out.append(a[0])))
        fn("--help")
        assert out and "Invalid container" in out[0]

    def test_valid_name_reaches_docker_unchanged(self, monkeypatch):
        calls = []
        monkeypatch.setattr(docker_mgr, "run_cmd", lambda cmd, **kw: (calls.append(cmd), ("", "", 0))[1])
        monkeypatch.setattr(docker_mgr, "console", types.SimpleNamespace(print=lambda *a, **k: None))
        docker_mgr._docker_restart("proj-web-1")
        assert calls == [["docker", "restart", "proj-web-1"]]


# ── every engine subprocess.run closes stdin or feeds it ─────────────────

class TestEngineSubprocessCallsAreNonInteractive:
    # Deliberately interactive (inherit the TTY): the editor, `docker stats`,
    # the sudo visudo/install pair, and the CLI re-entry wrapper.
    ALLOWED = {
        ("config.py", "subprocess.run(shlex.split(editor)"),
        ("docker_mgr.py", '["docker", "stats"'),
        ("sentinel.py", '["sudo", "visudo"'),
        ("sentinel.py", '["sudo", "install"'),
        ("sentinel.py", "[py, str(MACMON_PY), *args]"),
        ("sentinel.py", "subprocess.run(cmd, capture_output=True, text=text, timeout=timeout, **kw)"),  # _run itself
    }

    def test_every_call_closes_or_feeds_stdin(self):
        bad = []
        for p in sorted(ENGINE_DIR.glob("*.py")):
            if p.name.startswith("app_"):
                continue
            src = p.read_text(encoding="utf-8")
            for m in re.finditer(r"subprocess\.run\(", src):
                # the call spans to the matching close paren; 400 chars is enough for every call here
                chunk = src[m.start():m.start() + 400]
                depth, end = 0, None
                for i, ch in enumerate(chunk):
                    depth += ch == "("
                    depth -= ch == ")"
                    if depth == 0 and i > len("subprocess.run"):
                        end = i
                        break
                call = chunk[:end + 1] if end else chunk
                if "stdin=subprocess.DEVNULL" in call or "input=" in call:
                    continue
                if any(p.name == f and marker in call for f, marker in self.ALLOWED):
                    continue
                bad.append((p.name, call.splitlines()[0]))
        assert bad == []
