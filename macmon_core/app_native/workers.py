"""AegisForge native app -- the threading model.

The UI thread never calls the engine. Every read (``app_webui.*_dict``) and
every action (``app_api.Api.*``) runs as a ``Job`` on the global
``QThreadPool``; the result (or the exception) comes back to the widget
that asked through a queued ``Signal``, on the UI thread. A disk walk or a
security scan can take seconds: the window stays responsive, the triggering
control is disabled by its section while the job is in flight.

``run(fn, *args, done=cb, failed=cb, owner=widget)`` is the only entry point
the sections use. The signal holder is parented to ``owner`` so a result that
arrives after the widget died is dropped by Qt, never delivered to a dead
slot. Nothing here runs on its own: a Job exists only because a section
asked for one (nothing at import, nothing on a timer of its own).
"""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    """One engine call on a pool thread. Exceptions are caught and reported
    as ``failed(message)`` -- a failing engine call is shown, never raised
    into the event loop."""

    def __init__(self, fn, args, kwargs, owner=None):
        super().__init__()
        self.setAutoDelete(True)
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals(owner)

    @Slot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # reported to the UI as the section's error state
            traceback.print_exc()
            self._emit("failed", f"{e.__class__.__name__}: {e}")
            return
        self._emit("done", result)

    def _emit(self, name: str, payload) -> None:
        # The owner widget (parent of _Signals) may have been destroyed while the
        # job ran -- navigating away or closing the window. Emitting through the
        # now-deleted C++ QObject raises RuntimeError; that just means "the asker
        # is gone", so the result is dropped silently (the intended behavior).
        try:
            getattr(self.signals, name).emit(payload)
        except RuntimeError:
            pass


def run(fn, *args, done=None, failed=None, owner=None, **kwargs) -> Job:
    """Run ``fn(*args, **kwargs)`` off the UI thread; ``done(result)`` /
    ``failed(message)`` are invoked on the UI thread (queued connection)."""
    job = Job(fn, args, kwargs, owner)
    if done is not None:
        job.signals.done.connect(done)
    if failed is not None:
        job.signals.failed.connect(failed)
    QThreadPool.globalInstance().start(job)
    return job


def wait_all(ms: int = 10_000) -> bool:
    """(tests) block until every queued job finished."""
    return QThreadPool.globalInstance().waitForDone(ms)
