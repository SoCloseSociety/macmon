"""AegisForge native app (PySide6) -- UI builds, nothing mutates on its own, and
every action still goes through the guarded ``app_api.Api``. Runs headless via
the offscreen Qt platform (set before QApplication is created)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QStackedWidget  # noqa: E402

from macmon_core import app_api, app_native  # noqa: E402
from macmon_core.app_native import sections as S  # noqa: E402
from macmon_core.app_native import workers  # noqa: E402

# Every Api method that CHANGES the machine. None may be called without the user
# driving a funnel to its confirm step -- never on construction or navigation.
MUTATING = {
    "kill_process", "suspend_process", "resume_process", "purge_ram",
    "clean_execute", "docker_prune_dangling", "quarantine",
    "sentinel_pause", "sentinel_resume", "sentinel_set_auto",
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    QThreadPool.globalInstance().waitForDone(3000)


class RecordingApi:
    """Stands in for app_api.Api: records every call. Read-ish methods return a
    benign shape; a mutating method records and returns a refusal (so even if the
    UI wrongly called one, nothing would happen) -- the test asserts none were."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*a, **k):
            self.calls.append(name)
            if name == "clean_scan":
                return {"ok": True, "categories": [], "total": 0}
            if name == "process_guard":
                return {"ok": True, "protected": False, "guard": None, "ct": None, "name": "x"}
            return {"ok": False, "refused": "test"}
        return rec


def test_exactly_seven_sections():
    assert len(S.SECTIONS) == 7
    assert [c.__name__ for c in S.SECTIONS] == [
        "Overview", "Processes", "Clean", "Security", "Docker", "Disk", "Sentinel"]


def test_window_builds_and_switches_every_section(qapp):
    w = app_native.MainWindow()
    stacks = [s for s in w.findChildren(QStackedWidget) if s.count() == len(S.SECTIONS)]
    assert stacks, "no 7-page section stack found"
    st = stacks[0]
    for i in range(st.count()):
        st.setCurrentIndex(i)
        qapp.processEvents()
    assert st.count() == 7
    w.deleteLater(); qapp.processEvents()


def test_default_api_is_the_real_guarded_app_api(qapp):
    # Unless a stand-in is injected, the window drives the REAL Api -- so every
    # guardrail (protected set, never_touch+override, SIGTERM-only, ...) applies.
    w = app_native.MainWindow()
    assert isinstance(w.api, app_api.Api)
    w.deleteLater(); qapp.processEvents()


def test_nothing_mutates_on_construction_or_navigation(qapp):
    rec = RecordingApi()
    w = app_native.MainWindow(api=rec)
    st = [s for s in w.findChildren(QStackedWidget) if s.count() == 7][0]
    for i in range(st.count()):
        st.setCurrentIndex(i)
        qapp.processEvents()
    QThreadPool.globalInstance().waitForDone(4000)
    qapp.processEvents()
    hit = [c for c in rec.calls if c in MUTATING]
    assert hit == [], f"a mutating Api method ran without a funnel confirm: {hit}"
    w.deleteLater(); qapp.processEvents()


def test_theme_mirrors_the_brand_palette(qapp):
    from macmon_core.aegis import C
    app_native.apply_theme(qapp, "dark")
    qss = qapp.styleSheet().lower()
    assert C["ember"].lower() in qss and C["mint"].lower() in qss, "brand hexes not in the Qt stylesheet"


def test_worker_emit_is_safe_after_its_signals_are_deleted(qapp):
    # The owner widget can die while a background read is in flight, taking its
    # child _Signals with it; emitting through the deleted C++ object must be
    # dropped, never raise into the pool thread (the lifecycle fix).
    import shiboken6
    job = workers.Job(lambda: 42, (), {}, None)
    shiboken6.delete(job.signals)               # deterministically destroy the signal holder
    assert not shiboken6.isValid(job.signals)
    job._emit("done", 42)                        # must NOT raise
    job._emit("failed", "boom")                  # must NOT raise
    job.run()                                    # the full run path is also safe


def test_nothing_runs_at_import():
    import inspect
    src = inspect.getsource(app_native.window)
    for bad in ("QTimer(", ".start(", "Thread(", "atexit"):
        # a bare timer/thread kicked off at module import would be auto-running;
        # timers created inside MainWindow.__init__ are fine (they are owned).
        assert f"\n{bad}" not in src, bad
