"""AegisForge native app -- the main window (PySide6 / Qt Widgets).

A real application window, not a webview: a left sidebar (brand lockup +
seven nav items + the Auto / Dark / Light segment), a top bar (worst-severity
pill, live vital chips, sample age) and a ``QStackedWidget`` of the seven
sections. The window is the "shell" the sections talk to:

  ``api``          app_api.Api -- the ONE mutation path, called in-process
  ``reads``        the app_webui dict builders (pure reads)
  ``confirm(...)`` the Confirm dialog every mutating funnel passes through
  ``toast(...)``   the result line with the engine's own outcome
  ``tick()``       status + history for the top bar and the Overview

Nothing runs at construction: ``start()`` (called by ``run`` once the window
is shown) fires the first tick and starts the two READ timers (status 5s,
history 30s), exactly like the page did. No action ever runs on a timer.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from .. import aegis
from . import theme, widgets, workers
from .sections import SECTIONS, VITALS, num, tone_of
from .widgets import ConfirmDialog, ToastHost, label, mark_pixmap, set_prop

REPO = Path(__file__).resolve().parent.parent.parent   # the PyInstaller bundle root when frozen
MARK_SVG = REPO / "assets" / "webui" / "mark.svg"
ORG, APP = "SoClose", "AegisForge"
THEME_MODES = ("auto", "dark", "light")


class Reads:
    """The read side: ``app_webui``'s pure dict builders, bound late so the
    engine modules load on first use (and so tests can swap any of them)."""

    def __init__(self):
        from .. import app_webui as w
        self.status, self.history, self.health = w.status_dict, w.history_dict, w.health_dict
        self.processes, self.security, self.docker = w.processes_dict, w.security_dict, w.docker_dict
        self.disk, self.bigfiles, self.sentinel = w.disk_dict, w.bigfiles_dict, w.sentinel_dict


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
    except AttributeError:
        return True


def apply_theme(app: QApplication, mode: str) -> dict:
    """Resolve ``mode`` (auto / dark / light) to a semantic layer and install
    its stylesheet application-wide. Returns the layer."""
    dark = mode == "dark" or (mode != "light" and system_is_dark())
    sem = theme.DARK if dark else theme.LIGHT
    theme.set_current(sem)
    widgets._ICON_CACHE.clear()
    app.setStyleSheet(theme.build_qss(sem))
    return sem


class MainWindow(QMainWindow):
    def __init__(self, api=None, reads=None, settings: QSettings | None = None, is_mac: bool | None = None):
        super().__init__()
        from ..app_api import Api
        self.api = api if api is not None else Api()
        self.reads = reads if reads is not None else Reads()
        self.settings = settings if settings is not None else QSettings(ORG, APP)
        self.is_mac = (sys.platform == "darwin") if is_mac is None else bool(is_mac)
        self.active_dialog: ConfirmDialog | None = None
        self.started = False
        self.status: dict | None = None
        self.setWindowTitle(aegis.BRAND)
        self.setMinimumSize(820, 560)
        self.resize(1180, 780)
        if MARK_SVG.exists():
            self.setWindowIcon(QIcon(mark_pixmap(str(MARK_SVG), 64)))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        rl = QHBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._build_side())
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        ml.addWidget(self._build_top())
        self.scroll = QScrollArea()
        self.scroll.setObjectName("content")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        il = QVBoxLayout(inner)
        il.setContentsMargins(theme.SP[6], theme.SP[6], theme.SP[6], theme.SP[10])
        self.stack = QStackedWidget()
        il.addWidget(self.stack)
        self.scroll.setWidget(inner)
        ml.addWidget(self.scroll, 1)
        rl.addWidget(main, 1)

        self.sections = {}
        for cls in SECTIONS:
            sec = cls(self)
            self.sections[cls.key] = sec
            self.stack.addWidget(sec)
        self.current = None
        self.toasts = ToastHost(self)
        self.toasts.hide()

        # read timers (started by start(), never at construction)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(5_000)
        self.status_timer.timeout.connect(self.tick)
        self.history_timer = QTimer(self)
        self.history_timer.setInterval(30_000)
        self.history_timer.timeout.connect(self.pull_history)
        self._tick_busy = False

        # 1..7 (and Ctrl/Cmd+1..7) jump to a section; a focused text field keeps its digits
        for i, cls in enumerate(SECTIONS):
            for seq in (str(i + 1), f"Ctrl+{i + 1}"):
                sc = QShortcut(QKeySequence(seq), self)
                sc.setContext(Qt.WindowShortcut)
                sc.activated.connect(lambda k=cls.key: self.go(k))

        self.theme_mode = str(self.settings.value("theme", "auto") or "auto")
        if self.theme_mode not in THEME_MODES:
            self.theme_mode = "auto"
        self._mark_theme_buttons()
        start = str(self.settings.value("view", "overview") or "overview")
        self.go(start if start in self.sections else "overview")

    # ── chrome ────────────────────────────────────────────────────────────

    def _build_side(self) -> QWidget:
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(224)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(theme.SP[3], theme.SP[4], theme.SP[3], theme.SP[3])
        lay.setSpacing(2)
        brand = QHBoxLayout()
        brand.setSpacing(theme.SP[3])
        brand.setContentsMargins(theme.SP[2], 0, theme.SP[2], theme.SP[4])
        mark = QLabel()
        if MARK_SVG.exists():
            mark.setPixmap(mark_pixmap(str(MARK_SVG), 40))
        mark.setFixedSize(40, 40)
        brand.addWidget(mark)
        self.brand_name = QLabel()
        self.brand_name.setObjectName("brandName")
        self._brand_text()
        tag = QLabel(aegis.TAGLINE.upper())
        tag.setObjectName("brandTag")
        text = QVBoxLayout()
        text.setSpacing(3)
        text.addWidget(self.brand_name)
        text.addWidget(tag)
        brand.addLayout(text, 1)
        lay.addLayout(brand)
        line = QFrame()
        line.setObjectName("brandLine")
        lay.addWidget(line)
        lay.addSpacing(theme.SP[3])
        self.nav: dict[str, QPushButton] = {}
        for i, cls in enumerate(SECTIONS):
            b = QPushButton(f"  {cls.title if cls.key != 'sentinel' else 'Sentinel'}")
            b.setProperty("nav", True)
            b.setProperty("active", False)
            b.setProperty("icon_name", cls.ico)
            b.setCursor(Qt.PointingHandCursor)
            b.setIconSize(QSize(16, 16))
            b.setToolTip(f"{cls.title}  ({i + 1})")
            b.setAccessibleName(cls.title)
            b.clicked.connect(lambda _c=False, k=cls.key: self.go(k))
            self.nav[cls.key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        foot = QFrame()
        fl = QVBoxLayout(foot)
        fl.setContentsMargins(theme.SP[2], theme.SP[3], theme.SP[2], 0)
        fl.setSpacing(theme.SP[2])
        self.bridge = QLabel("engine: in-process (native)")
        self.bridge.setObjectName("engine")
        fl.addWidget(self.bridge)
        eng = QLabel("engine: macmon sentinel")
        eng.setObjectName("engine")
        fl.addWidget(eng)
        seg = QFrame()
        seg.setObjectName("seg")
        seg.setAttribute(Qt.WA_StyledBackground, True)
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(2, 2, 2, 2)
        sl.setSpacing(2)
        self.theme_btns = {}
        for mode, name in (("auto", "Auto"), ("dark", "Dark"), ("light", "Light")):
            tb = QPushButton(name)
            tb.setProperty("seg", True)
            tb.setProperty("active", False)
            tb.setCursor(Qt.PointingHandCursor)
            tb.clicked.connect(lambda _c=False, m=mode: self.set_theme(m))
            self.theme_btns[mode] = tb
            sl.addWidget(tb)
        sl.addStretch(1)
        fl.addWidget(seg)
        lay.addWidget(foot)
        return side

    def _brand_text(self):
        self.brand_name.setText(f'Aegis<span style="color:{theme.current()["action"]}">Forge</span>')

    def _build_top(self) -> QWidget:
        top = QFrame()
        top.setObjectName("top")
        top.setMinimumHeight(52)
        lay = QHBoxLayout(top)
        lay.setContentsMargins(theme.SP[6], theme.SP[2], theme.SP[6], theme.SP[2])
        lay.setSpacing(theme.SP[3])
        self.worst = label("ALL CLEAR", "worst", sev="ok")
        self.worst.setAccessibleName("worst severity")
        lay.addWidget(self.worst)
        self.chips = QWidget()
        self.chips_lay = QHBoxLayout(self.chips)
        self.chips_lay.setContentsMargins(0, 0, 0, 0)
        self.chips_lay.setSpacing(theme.SP[2])
        self.chips.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay.addWidget(self.chips, 1)
        self.age = label("no sample yet", "age", stale=False)
        lay.addWidget(self.age)
        return top

    # ── navigation ────────────────────────────────────────────────────────

    def go(self, key: str) -> None:
        if key not in self.sections:
            return
        if self.current and self.current != key:
            self.sections[self.current].leave()
        self.current = key
        for k, b in self.nav.items():
            set_prop(b, "active", k == key)
        self.stack.setCurrentWidget(self.sections[key])
        self.scroll.verticalScrollBar().setValue(0)
        self.settings.setValue("view", key)
        self.sections[key].enter()

    # ── theme ─────────────────────────────────────────────────────────────

    def set_theme(self, mode: str) -> None:
        if mode not in THEME_MODES:
            mode = "auto"
        self.theme_mode = mode
        self.settings.setValue("theme", mode)
        app = QApplication.instance()
        if app is not None:
            apply_theme(app, mode)
        self._mark_theme_buttons()
        self._retheme()

    def _mark_theme_buttons(self):
        for m, b in self.theme_btns.items():
            set_prop(b, "active", m == self.theme_mode)

    def _retheme(self):
        """Repaint what carries an explicit colour: nav icons, brand lockup,
        chips, and each section's cached data."""
        s = theme.current()
        self._brand_text()
        for k, b in self.nav.items():
            b.setIcon(widgets.icon(b.property("icon_name"), s["accent"] if k == self.current else s["muted"]))
        if self.status is not None:
            self.render_top(self.status)
        for sec in self.sections.values():
            sec.retheme()

    def system_theme_changed(self, *_):
        if self.theme_mode == "auto":
            self.set_theme("auto")

    # ── reads: the status tick (top bar + overview) ───────────────────────

    def start(self) -> None:
        """Fire the first reads and start the READ timers. Called once the
        window is shown; never from the constructor."""
        if self.started:
            return
        self.started = True
        self._retheme()
        self.pull_history()
        self.tick()
        self.status_timer.start()
        self.history_timer.start()

    def stop(self) -> None:
        self.status_timer.stop()
        self.history_timer.stop()
        for sec in self.sections.values():
            sec.leave()

    def tick(self) -> None:
        if self._tick_busy:
            return
        self._tick_busy = True
        workers.run(self.reads.status, done=self._on_status, failed=self._on_status_failed, owner=self)

    def pull_history(self) -> None:
        workers.run(self.reads.history, 60, done=self._on_history, failed=lambda _m: None, owner=self)

    def _on_history(self, d: dict):
        self.sections["overview"].set_history((d or {}).get("rows") or [])
        if self.status is not None:
            self.sections["overview"].render_status(self.status)

    def _on_status(self, d: dict):
        self._tick_busy = False
        self.status = d or {}
        self.render_top(self.status)
        self.sections["overview"].render_status(self.status)

    def _on_status_failed(self, msg: str):
        self._tick_busy = False
        self.age.setText("engine unreachable")
        set_prop(self.age, "stale", True)
        self.sections["overview"].render_status_error(msg)

    def render_top(self, d: dict) -> None:
        self.age.setText(d.get("age_label") or "")
        set_prop(self.age, "stale", bool(d.get("stale")))
        worst = str(d.get("worst") or "ok")
        self.worst.setText("ALL CLEAR" if worst == "ok" else f"WORST: {worst.upper()}")
        set_prop(self.worst, "sev", worst)
        widgets.clear_layout(self.chips_lay)
        vit = d.get("vitals") or {}
        for vid, (name, unit, _mx, dec) in VITALS.items():
            tn = tone_of(vid, vit.get(vid))
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)
            c = label(f"{name}  {num(vit.get(vid), dec)}{(' ' + unit) if unit else ''}", "chip", tone=tn or "ok")
            widgets.tabular(c)
            hl.addWidget(c)
            self.chips_lay.addWidget(row)
        self.chips_lay.addStretch(1)

    # ── shell services for the sections ───────────────────────────────────

    @property
    def dialog_open(self) -> bool:
        return self.active_dialog is not None

    def confirm(self, title: str, body, ok: str = "Confirm", ack: str | None = None, danger: bool = True, on_ok=None) -> ConfirmDialog:
        """Open the Confirm dialog (window-modal, non-blocking). ``on_ok(acked)``
        runs only on an accepted dialog; with ``ack`` the OK button is disabled
        until the acknowledgement is ticked, and ``acked`` is that tick."""
        if self.active_dialog is not None:
            try:
                self.active_dialog.reject()
            except RuntimeError:
                pass
        dlg = ConfirmDialog(self, title, body, ok=ok, ack=ack, danger=danger)
        self.active_dialog = dlg

        def _accepted():
            acked = dlg.acked
            if on_ok is not None:
                on_ok(acked)

        def _finished(_r):
            if self.active_dialog is dlg:
                self.active_dialog = None
            dlg.deleteLater()
        dlg.accepted.connect(_accepted)
        dlg.finished.connect(_finished)
        dlg.open()
        return dlg

    def toast(self, text: str, kind: str = "ok", ms: int = 4200):
        return self.toasts.toast(str(text), kind, ms)

    # ── Qt events ─────────────────────────────────────────────────────────

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toasts.reanchor()

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)


def run(argv=None) -> int:
    """Launch the native app; blocks until the window closes. Returns the
    exit code. Raises ImportError before any window if PySide6 is absent
    (the import at the top of this package), so the entry can fall back."""
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName(aegis.BRAND)
    if MARK_SVG.exists():
        app.setWindowIcon(QIcon(mark_pixmap(str(MARK_SVG), 128)))
    settings = QSettings(ORG, APP)
    mode = str(settings.value("theme", "auto") or "auto")
    apply_theme(app, mode if mode in THEME_MODES else "auto")
    win = MainWindow(settings=settings)
    try:
        QGuiApplication.styleHints().colorSchemeChanged.connect(win.system_theme_changed)
    except AttributeError:
        pass
    win.show()
    win.start()
    return app.exec()
