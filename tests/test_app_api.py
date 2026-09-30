"""Tests for the AegisForge.app js_api bridge (macmon_core.app_api.Api).

Hermetic: psutil.Process is a fake, every mutating engine function is a
recorder, the console is captured. What is locked here is the security model
of the app's ACTION path:

  - nothing mutates at import, construction or ping
  - a protected process (PID <= 1, the parent shell, Finder/loginwindow/...)
    is refused on kill / suspend / resume / quarantine, whatever the page sends
  - a recycled PID (create_time mismatch) is refused
  - a process the sweep-style guard names (IDE, agent, LLM ...) needs the
    explicit override the acknowledgement checkbox sets
  - clean is preview-first: execute without a scan is refused, execute only
    touches the ticked categories of THAT scan, Trash-first, never permanent,
    and a preview runs once
  - the docker prune is dangling-only (`docker image prune -f`, never -a)
  - only the four opt-in remediation keys can be flipped
  - every method returns a plain dict (JSON-serializable: WKWebView
    stringifies it, the page parses it)
"""
import inspect
import json
import os
import types
from pathlib import Path

import psutil
import pytest

from macmon_core import aegis, app_api, cleaner, docker_mgr, processes, security, sentinel

HIGH_PID = 999_999
CT = 1_700_000_000.0


class FakeProc:
    """Enough psutil.Process surface for app_api + processes.kill/suspend/resume."""
    table: dict = {}

    def __init__(self, pid, name="node", create_time=CT, ppid=1, cmdline=None, exe="/usr/local/bin/node",
                 username="me", status=psutil.STATUS_RUNNING):
        self.pid, self._name, self._ct, self._ppid = pid, name, create_time, ppid
        self._cmdline, self._exe, self._user, self._status = cmdline or [name], exe, username, status
        self.signals = []

    def name(self): return self._name
    def create_time(self): return self._ct
    def ppid(self): return self._ppid
    def cmdline(self): return self._cmdline
    def exe(self): return self._exe
    def username(self): return self._user
    def status(self): return self._status
    def children(self, recursive=False): return []
    def net_connections(self, kind="all"): return []
    def connections(self, kind="all"): return []
    def is_running(self): return True

    class _One:
        def __enter__(self): return None
        def __exit__(self, *a): return False

    def oneshot(self): return self._One()
    def terminate(self): self.signals.append("SIGTERM")
    def kill(self): self.signals.append("SIGKILL")
    def suspend(self): self.signals.append("SIGSTOP")
    def resume(self): self.signals.append("SIGCONT")


@pytest.fixture
def table(monkeypatch):
    """Install a fake process table; returns a dict pid -> FakeProc to fill."""
    procs = {}

    def Process(pid):
        if pid not in procs:
            raise psutil.NoSuchProcess(pid)
        return procs[pid]
    monkeypatch.setattr(psutil, "Process", Process)
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter([]))
    monkeypatch.setattr(aegis, "_ME", "me")
    return procs


@pytest.fixture
def api():
    return app_api.Api()


@pytest.fixture
def no_mutation(monkeypatch):
    """Every mutating engine entry point explodes: a test using this proves
    the call under test never reached one."""
    def boom(*a, **k):
        raise AssertionError("a mutating engine function was called")
    for mod, name in ((processes, "kill_process"), (processes, "suspend_process"), (processes, "resume_process"),
                      (processes, "purge_ram"), (cleaner, "_execute_clean"), (cleaner, "_clean_paths"),
                      (cleaner, "_trash_or_rm"), (docker_mgr, "_docker_prune_dangling"), (docker_mgr, "_docker_prune"),
                      (sentinel, "pause"), (sentinel, "resume"), (sentinel, "_write_conf"),
                      (security, "_quarantine_process"), (app_api.subprocess, "Popen")):
        monkeypatch.setattr(mod, name, boom)


# ── nothing runs on its own ──────────────────────────────────────────────

class TestNothingAutomatic:
    def test_construction_and_ping_mutate_nothing(self, no_mutation):
        a = app_api.Api()
        r = a.ping()
        assert r["ok"] is True and r["brand"] == "AegisForge"

    def test_module_has_no_timer_or_import_time_side_effect(self):
        src = inspect.getsource(app_api)
        for bad in ("threading.Timer", "Thread(", "schedule", "atexit"):
            assert bad not in src, bad

    def test_every_public_method_returns_a_json_dict(self, table, api, monkeypatch, tmp_path):
        # WKWebView returns js_api results as JSON strings: each must serialize.
        table[HIGH_PID] = FakeProc(HIGH_PID)
        monkeypatch.setattr(processes, "kill_process", lambda *a, **k: None)
        monkeypatch.setattr(processes, "suspend_process", lambda *a, **k: None)
        monkeypatch.setattr(processes, "resume_process", lambda *a, **k: None)
        monkeypatch.setattr(processes, "purge_ram", lambda: None)
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [])
        monkeypatch.setattr(docker_mgr, "_docker_available", lambda: False)
        monkeypatch.setattr(security, "_quarantine_process", lambda *a, **k: None)
        monkeypatch.setattr(sentinel, "pause", lambda: None)
        monkeypatch.setattr(sentinel, "resume", lambda: None)
        monkeypatch.setattr(sentinel, "_write_conf", lambda u: None)
        monkeypatch.setattr(sentinel, "_conf", lambda: dict(sentinel.DEFAULTS))
        monkeypatch.setattr(sentinel, "_purge_nopasswd_ready", lambda: False)
        calls = {
            "ping": (), "process_guard": (HIGH_PID, CT), "kill_process": (HIGH_PID, CT), "suspend_process": (HIGH_PID, CT),
            "resume_process": (HIGH_PID, CT), "purge_ram": (), "clean_scan": (), "clean_execute": ([0],),
            "docker_prune_dangling": (), "quarantine": (HIGH_PID, CT), "sentinel_pause": (), "sentinel_resume": (),
            "sentinel_set_auto": ("auto_purge", False), "reveal": (str(tmp_path / "nope"),),
        }
        public = [n for n, f in inspect.getmembers(app_api.Api, inspect.isfunction) if not n.startswith("_")]
        assert set(public) == set(calls), "every public method must be listed here"
        for name, args in calls.items():
            r = getattr(api, name)(*args)
            assert isinstance(r, dict) and "ok" in r, name
            json.dumps(r, allow_nan=False)


# ── process signals: the guardrails ──────────────────────────────────────

class TestSignalGuards:
    @pytest.mark.parametrize("pid", [0, 1, -5, "abc", None])
    def test_pid_zero_one_or_garbage_is_refused_before_psutil(self, api, no_mutation, monkeypatch, pid):
        def never(pid):
            raise AssertionError("psutil.Process consulted for a refused pid")
        monkeypatch.setattr(psutil, "Process", never)
        for verb in ("kill_process", "suspend_process", "resume_process", "quarantine"):
            r = getattr(api, verb)(pid, CT)
            assert r["ok"] is False and r["refused"] == "protected", (verb, pid)

    @pytest.mark.parametrize("name", ["Finder", "loginwindow", "WindowServer", "kernel_task", "launchd", "Dock"])
    def test_protected_names_are_refused_with_no_signal(self, table, api, no_mutation, name):
        table[HIGH_PID] = FakeProc(HIGH_PID, name)
        for r in (api.kill_process(HIGH_PID, CT, True), api.suspend_process(HIGH_PID, CT, True),
                  api.resume_process(HIGH_PID, CT), api.quarantine(HIGH_PID, CT)):
            assert r["ok"] is False and r["refused"] == "protected" and name in r["detail"]
        assert table[HIGH_PID].signals == []
        g = api.process_guard(HIGH_PID, CT)
        assert g["ok"] is False and g["refused"] == "protected"

    def test_the_parent_shell_is_refused(self, table, api, no_mutation):
        ppid = os.getppid()
        table[ppid] = FakeProc(ppid, "zsh")
        r = api.kill_process(ppid, CT, True)
        assert r["ok"] is False and r["refused"] == "protected"
        assert table[ppid].signals == []

    def test_recycled_pid_is_refused(self, table, api, no_mutation):
        # the page listed create_time CT; the PID now belongs to a newer process
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", create_time=CT + 3600)
        r = api.kill_process(HIGH_PID, CT)
        assert r["ok"] is False and r["refused"] == "recycled" and "refresh" in r["detail"]
        assert table[HIGH_PID].signals == []

    def test_gone_pid_is_reported(self, table, api, no_mutation):
        r = api.kill_process(HIGH_PID, CT)
        assert r["ok"] is False and r["refused"] == "gone"

    @pytest.mark.parametrize("name,cmd,reason", [
        ("Code Helper (Renderer)", ["code"], "ide"),
        ("claude", ["claude"], "llm"),
        ("node", ["node", os.path.expanduser("~/.claude/hooks/x.js")], "protected (~/.claude)"),
        ("python3", ["python3", "-m", "macmon_core.sentinel"], "protected (macmon)"),
    ])
    def test_guarded_process_needs_the_acknowledged_override(self, table, api, monkeypatch, name, cmd, reason):
        table[HIGH_PID] = FakeProc(HIGH_PID, name, cmdline=cmd)
        killed = []
        monkeypatch.setattr(processes, "kill_process", lambda target, force_yes=False, **k: killed.append((target, force_yes)))
        g = api.process_guard(HIGH_PID, CT)
        assert g["ok"] is True and g["guard"] == reason and g["protected"] is False
        r = api.kill_process(HIGH_PID, CT)                 # no override: refused, nothing signalled
        assert r["ok"] is False and r["refused"] == "guarded" and r["guard"] == reason
        assert killed == []
        r = api.kill_process(HIGH_PID, CT, override=True)  # acknowledged: proceeds through the engine
        assert killed == [(str(HIGH_PID), True)] and r["override"] is True

    def test_ordinary_process_goes_through_the_engine_kill_path(self, table, api, monkeypatch):
        # reuse, not reimplementation: processes.kill_process (its own guard +
        # SIGTERM + log_action), with force_yes because the modal confirmed.
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", cmdline=["node", "server.js"])
        seen = {}

        def kill_process(target, category=None, force_yes=False):
            seen.update(target=target, force_yes=force_yes)
            processes.console.print(f"[green]Terminated node (PID {target})[/]")
        monkeypatch.setattr(processes, "kill_process", kill_process)
        r = api.kill_process(HIGH_PID, CT)
        assert seen == {"target": str(HIGH_PID), "force_yes": True}
        assert r["ok"] is True and r["verb"] == "kill" and "Terminated node" in r["detail"]

    def test_real_engine_kill_is_sigterm_never_sigkill(self, table, api):
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", cmdline=["node", "server.js"])
        r = api.kill_process(HIGH_PID, CT)
        assert r["ok"] is True and table[HIGH_PID].signals == ["SIGTERM"]

    def test_suspend_and_resume_signals(self, table, api):
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", cmdline=["node", "server.js"])
        assert api.suspend_process(HIGH_PID, CT)["ok"] is True
        assert api.resume_process(HIGH_PID, CT)["ok"] is True
        assert table[HIGH_PID].signals == ["SIGSTOP", "SIGCONT"]

    def test_engine_error_is_reported_not_ok(self, table, api, monkeypatch):
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", cmdline=["node", "server.js"])
        monkeypatch.setattr(processes, "kill_process",
                            lambda *a, **k: processes.console.print("[red]Error: access denied[/]"))
        r = api.kill_process(HIGH_PID, CT)
        assert r["ok"] is False and "access denied" in r["detail"]

    def test_process_guard_reports_in_service(self, table, api, monkeypatch):
        table[HIGH_PID] = FakeProc(HIGH_PID, "node", cmdline=["node", "server.js"])
        monkeypatch.setattr(aegis, "_in_service", lambda pid, family=None: True)
        g = api.process_guard(HIGH_PID, CT)
        assert g["in_service"] is True and g["guard"] is None and g["name"] == "node"


# ── clean: preview -> execute, Trash-first ───────────────────────────────

SCAN = [
    {"name": "User Logs (old)", "size": 3000, "count": 3, "paths": ["/l/a", "/l/b", "/l/c"]},
    {"name": "Crash Reports", "size": 0, "count": 0, "paths": []},
    {"name": "User Caches (top 20)", "size": 9000, "count": 2, "paths": ["/c/x", "/c/y"]},
]


class TestCleanFunnel:
    def test_execute_without_a_preview_is_refused(self, api, no_mutation):
        r = api.clean_execute([0, 1, 2])
        assert r["ok"] is False and r["refused"] == "no-scan"

    def test_scan_previews_and_touches_nothing(self, api, no_mutation, monkeypatch):
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [dict(r) for r in SCAN])
        r = api.clean_scan()
        assert r["ok"] is True and r["total"] == 12000 and r["total_label"] == "11.7 KB"
        assert [c["name"] for c in r["categories"]] == ["User Caches (top 20)", "User Logs (old)"]  # biggest first, empties dropped
        assert r["categories"][0]["id"] == 2 and r["categories"][0]["count"] == 2

    def test_execute_cleans_only_the_ticked_categories_trash_first(self, api, monkeypatch):
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [dict(r) for r in SCAN])
        seen = {}

        def execute(results, permanent=False):
            seen["names"] = [r["name"] for r in results]
            seen["permanent"] = permanent
            return 2999
        monkeypatch.setattr(cleaner, "_execute_clean", execute)
        api.clean_scan()
        r = api.clean_execute([0, "1", 99, None])
        assert seen == {"names": ["User Logs (old)"], "permanent": False}   # id 1 is empty, 99 unknown
        assert r["ok"] is True and r["freed"] == 2999 and r["freed_label"] == "2.9 KB"
        assert r["cleaned"] == ["User Logs (old)"]
        # a preview runs once: the next execute needs a fresh scan
        assert api.clean_execute([0])["refused"] == "no-scan"

    def test_empty_selection_is_refused(self, api, no_mutation, monkeypatch):
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [dict(r) for r in SCAN])
        api.clean_scan()
        assert api.clean_execute([])["refused"] == "empty"
        assert api.clean_execute([1])["refused"] == "empty"     # the empty category

    def test_stale_preview_is_refused(self, api, no_mutation, monkeypatch):
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [dict(r) for r in SCAN])
        api.clean_scan()
        api._scan_at -= app_api.SCAN_TTL_S + 1
        assert api.clean_execute([0])["refused"] == "stale"

    def test_permanent_is_never_passed(self):
        src = inspect.getsource(app_api.Api.clean_execute)
        assert "permanent=False" in src and "permanent=True" not in src

    def test_real_execute_goes_through_send2trash_and_skips_on_refusal(self, api, monkeypatch, tmp_path):
        # The engine's own path: cleaner._execute_clean -> _clean_paths -> _trash_or_rm.
        keep = tmp_path / "keep.log"
        keep.write_bytes(b"x" * 100)
        gone = tmp_path / "gone.log"
        gone.write_bytes(b"y" * 50)
        trashed = []

        def send2trash(p):
            if p.endswith("keep.log"):
                raise OSError("Trash unavailable")
            trashed.append(p)
            Path(p).unlink()
        monkeypatch.setattr(cleaner, "send2trash", send2trash)
        monkeypatch.setattr(cleaner, "get_db", lambda: types.SimpleNamespace(execute=lambda *a: None, commit=lambda: None, close=lambda: None))
        monkeypatch.setattr(cleaner, "_scan_all", lambda progress=None: [
            {"name": "Logs", "size": 150, "count": 2, "paths": [str(keep), str(gone)]}])
        api.clean_scan()
        r = api.clean_execute([0])
        assert trashed == [str(gone)] and not gone.exists()
        assert keep.exists()                     # refused by Trash -> skipped, never rm'd
        assert r["ok"] is True and r["freed"] == 50 and r["skipped"] == 1
        assert any("Skipped" in n for n in r["notes"])


# ── docker: dangling-only prune ──────────────────────────────────────────

class TestDockerPrune:
    def test_refused_when_docker_is_absent(self, api, no_mutation, monkeypatch):
        monkeypatch.setattr(docker_mgr, "_docker_available", lambda: False)
        r = api.docker_prune_dangling()
        assert r["ok"] is False and r["refused"] == "no-docker"

    def test_calls_the_engines_guarded_prune_only(self, api, monkeypatch):
        monkeypatch.setattr(docker_mgr, "_docker_available", lambda: True)
        seen = {}
        monkeypatch.setattr(docker_mgr, "_docker_prune_dangling",
                            lambda force_yes=False: seen.update(force_yes=force_yes) or {"ok": True, "removed": 2, "reclaimed": "1.2GB"})
        r = api.docker_prune_dangling()
        assert seen == {"force_yes": True} and r["removed"] == 2 and r["ok"] is True

    def test_engine_prune_is_image_prune_without_dash_a(self, monkeypatch):
        cmds = []

        def run_cmd(cmd, sudo=False, timeout=30):
            cmds.append(cmd)
            if cmd[:2] == ["docker", "images"]:
                return "sha1\nsha2\n", "", 0
            return "Total reclaimed space: 1.2GB\n", "", 0
        monkeypatch.setattr(docker_mgr, "run_cmd", run_cmd)
        with docker_mgr.console.capture():
            r = docker_mgr._docker_prune_dangling(force_yes=True)
        assert r == {"ok": True, "removed": 2, "reclaimed": "1.2GB", "detail": "2 dangling image(s) removed, 1.2GB reclaimed"}
        assert cmds[-1] == ["docker", "image", "prune", "-f"]
        assert all("-a" not in c and "volume" not in c and "system" not in c for c in cmds)

    def test_engine_prune_with_nothing_dangling_runs_no_prune(self, monkeypatch):
        cmds = []
        monkeypatch.setattr(docker_mgr, "run_cmd", lambda cmd, **k: (cmds.append(cmd), ("", "", 0))[1])
        with docker_mgr.console.capture():
            r = docker_mgr._docker_prune_dangling(force_yes=True)
        assert r["ok"] is True and r["removed"] == 0
        assert cmds == [["docker", "images", "-f", "dangling=true", "-q"]]

    def test_engine_prune_is_confirmed_on_the_cli(self, monkeypatch):
        monkeypatch.setattr(docker_mgr, "run_cmd", lambda cmd, **k: ("sha1\n", "", 0))
        monkeypatch.setattr(docker_mgr, "confirm_action", lambda *a, **k: False)
        with docker_mgr.console.capture():
            r = docker_mgr._docker_prune_dangling()
        assert r["ok"] is False and r["detail"] == "cancelled"


# ── sentinel: pause / resume / opt-in toggles ────────────────────────────

class TestSentinel:
    def test_pause_and_resume_call_the_engine(self, api, monkeypatch):
        calls = []
        monkeypatch.setattr(sentinel, "pause", lambda: calls.append("pause"))
        monkeypatch.setattr(sentinel, "resume", lambda: calls.append("resume"))
        assert api.sentinel_pause()["ok"] is True
        assert api.sentinel_resume()["ok"] is True
        assert calls == ["pause", "resume"]

    def test_resume_failure_is_reported(self, api, monkeypatch):
        monkeypatch.setattr(sentinel, "resume", lambda: sentinel.console.print("Resume failed: launchctl exploded"))
        r = api.sentinel_resume()
        assert r["ok"] is False and "launchctl exploded" in r["detail"]

    @pytest.mark.parametrize("key", ["swap_critical_gb", "ram_pct", "reap_families", "", None, "__class__"])
    def test_only_the_four_opt_in_keys_can_be_flipped(self, api, no_mutation, key):
        r = api.sentinel_set_auto(key, True)
        assert r["ok"] is False and r["refused"] == "key"

    @pytest.mark.parametrize("key", app_api.AUTO_KEYS)
    def test_toggle_writes_the_sentinels_conf(self, api, monkeypatch, key, tmp_path):
        monkeypatch.setattr(sentinel, "CONF", tmp_path / "sentinel.conf")
        monkeypatch.setattr(sentinel, "_purge_nopasswd_ready", lambda: False)
        r = api.sentinel_set_auto(key, True)
        assert r["ok"] is True and r["on"] is True and r["auto"][key] is True
        assert json.loads((tmp_path / "sentinel.conf").read_text()) == {key: True}
        assert sum(r["auto"].values()) == 1            # the other levels stayed OFF
        r = api.sentinel_set_auto(key, "false")         # a JS string reaches us honestly
        assert r["on"] is False and json.loads((tmp_path / "sentinel.conf").read_text()) == {key: False}

    def test_auto_purge_reports_the_sudoers_gap(self, api, monkeypatch, tmp_path):
        monkeypatch.setattr(sentinel, "CONF", tmp_path / "sentinel.conf")
        monkeypatch.setattr(sentinel, "_purge_nopasswd_ready", lambda: False)
        assert api.sentinel_set_auto("auto_purge", True)["purge_ready"] is False

    def test_defaults_are_all_off(self):
        assert all(sentinel.DEFAULTS[k] is False for k in app_api.AUTO_KEYS)


# ── security: quarantine ─────────────────────────────────────────────────

class TestQuarantine:
    def test_ordinary_process_reaches_the_engine_with_force_yes(self, table, api, monkeypatch):
        table[HIGH_PID] = FakeProc(HIGH_PID, "evil", cmdline=["evil"])
        seen = {}

        def q(target, force_yes=False):
            seen.update(target=target, force_yes=force_yes)
            security.console.print("[green]Killed evil (PID 999999)[/]")
        monkeypatch.setattr(security, "_quarantine_process", q)
        r = api.quarantine(HIGH_PID, CT)
        assert seen == {"target": str(HIGH_PID), "force_yes": True} and r["ok"] is True

    def test_recycled_pid_is_refused(self, table, api, no_mutation):
        table[HIGH_PID] = FakeProc(HIGH_PID, "evil", create_time=CT + 99)
        assert api.quarantine(HIGH_PID, CT)["refused"] == "recycled"


# ── misc ─────────────────────────────────────────────────────────────────

class TestMisc:
    def test_reveal_refuses_a_missing_path_without_spawning(self, api, no_mutation, tmp_path):
        r = api.reveal(str(tmp_path / "missing"))
        assert r["ok"] is False

    def test_reveal_opens_finder_on_an_existing_path(self, api, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(app_api.subprocess, "Popen", lambda cmd: seen.append(cmd))
        monkeypatch.setattr(app_api.sys, "platform", "darwin")
        assert api.reveal(str(tmp_path))["ok"] is True
        assert seen == [["open", "-R", str(tmp_path)]]

    def test_purge_reports_the_engines_last_line(self, api, monkeypatch):
        monkeypatch.setattr(processes, "purge_ram", lambda: processes.console.print("[red]Purge failed: sudo needs a password[/]"))
        r = api.purge_ram()
        assert r["ok"] is False and "sudo needs a password" in r["detail"]

    def test_engine_call_captures_and_serializes(self):
        with app_api.engine_call() as cap:
            app_api.console.print("[bold]hello[/] world")
        assert app_api._lines(cap.get()) == ["hello world"]
        assert app_api._ENGINE_LOCK.acquire(blocking=False)   # released after the block
        app_api._ENGINE_LOCK.release()
