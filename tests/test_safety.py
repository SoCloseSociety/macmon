"""Safety-critical regression tests for macmon.

Each test locks in an audited-and-fixed bug so it cannot silently return:

1. Dashboard ESC-sequence draining -- arrow/End/Delete keys must NOT fire
   destructive action shortcuts (the final byte of "ESC [ F" etc. used to
   leak into the key poll and trigger e.g. purge/sweep/clean).
2. _trash_or_rm must NEVER escalate a failed Trash move to permanent
   deletion -- a Trash failure means "skip", not "rm -rf".
3. The duplicates keeper must always keep at least one copy per group,
   even when --keep-in matches no file in the group.
4. macmon.py must fail fast with a clear message on Python < 3.11.

All tests are hermetic: no real Trash, no subprocess, no network; the
filesystem is only touched under tmp_path. Cross-platform (macOS, Linux,
Windows): OS-specific branches are forced via monkeypatch.
"""

import sys
import types
from collections import deque
from pathlib import Path

import pytest

# Safety net in case conftest.py's sys.path setup changes: make
# `from modules.X import ...` work from the repo root regardless.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from modules import cleaner, dashboard, duplicates, gc as gc_mod  # noqa: E402
from modules.duplicates import _keep_indices  # noqa: E402


# ── 1. Dashboard ESC-sequence draining (HIGH: keys firing actions) ──────


class _FakeStdin:
    """stdin stand-in: read(1) pops one char from a queue."""

    def __init__(self, chars):
        self.queue = deque(chars)

    def read(self, n):
        assert n == 1, "dashboard key readers must read one char at a time"
        if not self.queue:
            raise AssertionError(
                "read() called on empty queue -- would block forever on a real TTY"
            )
        return self.queue.popleft()


def _fake_unix_tty(monkeypatch, chars):
    """Force dashboard onto the Unix code path with a scripted stdin.

    Returns the fake stdin so tests can feed more keys / inspect the queue.
    """
    fake_stdin = _FakeStdin(chars)

    class _FakeSelectModule:
        @staticmethod
        def select(rlist, wlist, xlist, timeout=None):
            # stdin is "readable" exactly while the scripted queue is non-empty
            return (list(rlist), [], []) if fake_stdin.queue else ([], [], [])

    monkeypatch.setattr(dashboard, "msvcrt", None)  # never take the Windows path
    monkeypatch.setattr(dashboard, "select", _FakeSelectModule)
    monkeypatch.setattr(dashboard, "sys", types.SimpleNamespace(stdin=fake_stdin))
    return fake_stdin


def test_nonblocking_end_key_is_ignored_and_fully_drained(monkeypatch):
    """Bug: End key (ESC [ F) left '[' and 'F' in the buffer; 'F' could then
    be read as a key and fire an action shortcut. The fix drains the whole
    ESC sequence and returns None."""
    fake_stdin = _fake_unix_tty(monkeypatch, ["\x1b", "[", "F"])

    assert dashboard._get_key_nonblocking() is None
    assert not fake_stdin.queue, "ESC sequence must be fully drained"

    # A following normal key must be read cleanly, not a leftover '[' / 'F'
    fake_stdin.queue.extend("q")
    assert dashboard._get_key_nonblocking() == "q"


def test_nonblocking_plain_key_passes_through(monkeypatch):
    """Guard: the ESC-drain fix must not swallow normal single-key input."""
    _fake_unix_tty(monkeypatch, ["q"])
    assert dashboard._get_key_nonblocking() == "q"


def test_nonblocking_no_input_returns_none(monkeypatch):
    """Guard: with nothing readable, the poll returns None (no block)."""
    _fake_unix_tty(monkeypatch, [])
    assert dashboard._get_key_nonblocking() is None


def test_read_one_key_end_key_returns_sentinel_and_drains(monkeypatch):
    """Bug: _read_one_key left ESC-sequence tail bytes buffered, so they
    leaked into the next nonblocking poll as fake shortcuts. The fix drains
    and returns "" (the no-key sentinel)."""
    fake_stdin = _fake_unix_tty(monkeypatch, ["\x1b", "[", "F"])

    assert dashboard._read_one_key() == ""
    assert not fake_stdin.queue, "ESC sequence must be fully drained"


def test_read_one_key_plain_key(monkeypatch):
    """Guard: _read_one_key still returns a normal key unchanged."""
    _fake_unix_tty(monkeypatch, ["x"])
    assert dashboard._read_one_key() == "x"


def test_windows_extended_prefix_is_drained_and_ignored(monkeypatch):
    """Bug (Windows twin of the ESC bug): arrows/Del/F-keys arrive as a
    prefix byte + scan code; the scan code overlaps ASCII shortcuts
    (Down -> 'P' = purge, Del -> 'S' = sweep). Both bytes must be consumed
    and the key ignored."""
    calls = deque([b"\xe0", b"P"])  # Down arrow: prefix + scan code 'P'

    fake_msvcrt = types.SimpleNamespace(
        kbhit=lambda: bool(calls),
        getch=lambda: calls.popleft(),
    )
    monkeypatch.setattr(dashboard, "msvcrt", fake_msvcrt)

    assert dashboard._get_key_nonblocking() is None
    assert not calls, "prefix AND scan code must both be consumed"
    # Nothing left buffered -> next poll sees no key at all
    assert dashboard._get_key_nonblocking() is None


# ── 2. _trash_or_rm never escalates a Trash failure to deletion ─────────

# cleaner, gc and duplicates each have their own _trash_or_rm with the same
# safety contract; test all three so none can regress independently.
_TRASH_MODULES = [cleaner, gc_mod, duplicates]


def _raise_trash_error(path):
    raise Exception("simulated: Trash unavailable")


@pytest.mark.parametrize(
    "mod", _TRASH_MODULES, ids=[m.__name__ for m in _TRASH_MODULES]
)
def test_trash_failure_is_skip_not_permanent_delete(monkeypatch, tmp_path, mod):
    """Bug: when send2trash failed, the file used to be escalated to a
    permanent unlink/rmtree. Fix: a Trash failure returns False and the
    file MUST still exist."""
    victim = tmp_path / "precious.txt"
    victim.write_text("do not lose me")

    monkeypatch.setattr(mod, "send2trash", _raise_trash_error)

    assert mod._trash_or_rm(victim, permanent=False) is False
    assert victim.exists(), "Trash failure must never fall back to deletion"


@pytest.mark.parametrize(
    "mod", _TRASH_MODULES, ids=[m.__name__ for m in _TRASH_MODULES]
)
def test_trash_missing_is_skip_not_permanent_delete(monkeypatch, tmp_path, mod):
    """Bug variant: with send2trash not installed at all (None), the file
    must be skipped, not deleted."""
    victim = tmp_path / "precious2.txt"
    victim.write_text("still precious")

    monkeypatch.setattr(mod, "send2trash", None)

    assert mod._trash_or_rm(victim, permanent=False) is False
    assert victim.exists()


@pytest.mark.parametrize(
    "mod", _TRASH_MODULES, ids=[m.__name__ for m in _TRASH_MODULES]
)
def test_permanent_true_actually_deletes(tmp_path, mod):
    """Guard: with explicit permanent=True the file is really removed and
    True is returned (send2trash never involved)."""
    throwaway = tmp_path / "throwaway.txt"
    throwaway.write_text("ok to delete")

    assert mod._trash_or_rm(throwaway, permanent=True) is True
    assert not throwaway.exists()


# ── 3. Duplicates keeper always keeps at least one copy ─────────────────


def _group(tmp_path, n=3):
    """Build a duplicate group in run_dupes shape: dicts with a 'path' key,
    sorted oldest-first (index 0 = oldest, index n-1 = newest)."""
    files = []
    for i in range(n):
        p = tmp_path / f"copy{i}.txt"
        p.write_text("same content")
        files.append({"path": p})
    return files


def test_keep_indices_default_never_deletes_all(tmp_path):
    """Bug: with no keep mode selected, an empty keep-set would have deleted
    every copy in the group. Fix: the newest copy is force-kept."""
    files = _group(tmp_path)
    keeps = _keep_indices(files, auto_keep_newest=False, auto_keep_oldest=False, keep_in=None)
    assert len(keeps) >= 1, "a duplicate group must never lose all copies"
    assert keeps == {len(files) - 1}, "the force-kept copy is the newest"


def test_keep_indices_nonmatching_keep_in_force_keeps_newest(tmp_path):
    """Bug: --keep-in pointing at a directory containing NONE of the group's
    files matched nothing, so every copy would be deleted. Fix: force-keep
    the newest copy."""
    files = _group(tmp_path)
    elsewhere = str((tmp_path / "elsewhere").resolve())  # matches no file
    keeps = _keep_indices(files, auto_keep_newest=False, auto_keep_oldest=False, keep_in=elsewhere)
    assert len(keeps) >= 1, "non-matching --keep-in must not delete the whole group"
    assert keeps == {len(files) - 1}, "the force-kept copy is the newest"


def test_keep_indices_matching_keep_in_keeps_those_files(tmp_path):
    """Guard: --keep-in that DOES match keeps the matching copies (and only
    those), leaving the rest deletable."""
    inside = tmp_path / "keepme"
    inside.mkdir()
    kept_file = inside / "copy_kept.txt"
    kept_file.write_text("same content")

    files = _group(tmp_path, n=2)
    files.insert(1, {"path": kept_file})  # group of 3, one under keep-in

    keeps = _keep_indices(
        files,
        auto_keep_newest=False,
        auto_keep_oldest=False,
        keep_in=str(inside.resolve()),
    )
    assert keeps == {1}


def test_keep_indices_newest_and_oldest_modes(tmp_path):
    """Guard: auto-keep-oldest keeps index 0, auto-keep-newest keeps the
    last index -- and at least one copy survives in every mode."""
    files = _group(tmp_path, n=4)
    assert _keep_indices(files, True, False, None) == {len(files) - 1}
    assert _keep_indices(files, False, True, None) == {0}
    both = _keep_indices(files, True, True, None)
    assert {0, len(files) - 1} == both


def test_keep_indices_single_file_group_is_kept(tmp_path):
    """Edge guard: a 1-file group must keep its only copy in every mode."""
    files = _group(tmp_path, n=1)
    for newest, oldest in [(False, False), (True, False), (False, True)]:
        assert _keep_indices(files, newest, oldest, None) == {0}


# ── 4. macmon.py Python-version guard ───────────────────────────────────


def test_python_version_guard_present_and_correct():
    """Bug: on Python < 3.11 macmon used to die with an obscure ImportError
    deep in config loading (stdlib tomllib is 3.11+). Fix: an explicit
    sys.exit guard at the very top of macmon.py.

    Structural test: we cannot launch an older interpreter here, so assert
    the guard's condition and message exist and run BEFORE third-party
    imports."""
    source = (Path(_REPO_ROOT) / "macmon.py").read_text(encoding="utf-8")

    assert "sys.version_info < (3, 11)" in source, "version guard condition missing"

    guard_pos = source.index("sys.version_info < (3, 11)")
    guard_block = source[guard_pos:guard_pos + 500]
    assert "sys.exit" in guard_block, "guard must exit, not just warn"
    assert "3.11" in guard_block, "exit message must tell the user 3.11+ is required"

    # The guard must fire before typer/rich are imported, or an old
    # interpreter could crash on those imports first.
    typer_pos = source.index("import typer")
    assert guard_pos < typer_pos, "guard must run before third-party imports"
