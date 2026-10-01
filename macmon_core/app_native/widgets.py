"""AegisForge native app -- the component kit (real Qt widgets, no webview).

Every component here is the native twin of an app.css class: cards, eyebrow
headings, chips and badges, the vital bar + sparkline, the health ring, the
funnel stepper, the ember opt-in switch, the Confirm dialog (with the
acknowledgement checkbox that gates a guarded action), toasts, and the four
shared states (skeleton / empty / error / working). Colours come from
``theme.current()`` (painted widgets) or the stylesheet roles (styled
widgets); icons are the SAME 16-unit SVG paths app.js draws, rendered through
QtSvg in the role's colour, so the native app and the page share one
geometry. No image, font or script is ever fetched: everything is inline.
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from . import theme

# ── icons: the app.js ICONS table, verbatim (16-unit grid; "mark" is 48) ─────
ICONS = {
    "pulse": "M2 8h3l2-5 3 10 2-5h4",
    "cpu": "M5 5h6v6H5z M2 6h3M2 10h3M11 6h3M11 10h3M6 2v3M10 2v3M6 11v3M10 11v3",
    "broom": "M11 2 7 6 M7 6l3 3 M4 9l3-3 3 3-3 5H4z",
    "shield": "M8 2 13 4v4c0 3-2.5 5-5 6-2.5-1-5-3-5-6V4z",
    "box": "M2 5l6-3 6 3v6l-6 3-6-3z M2 5l6 3 6-3 M8 8v6",
    "disk": "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
    "ember": "M8 2c1 2 4 3.5 4 7a4 4 0 0 1-8 0c0-2 1-3 1.5-3.5C6 7 7 7.5 7 8.5 7.5 7 8 5 8 2z",
    "check": "M3 8.5l3 3 7-7",
    "x": "M4 4l8 8M12 4l-8 8",
    "eye": "M2 8s2-4 6-4 6 4 6 4-2 4-6 4-6-4-6-4z M8 8m-1.5 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0",
    "clock": "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 5v3l2 1.5",
    "search": "M7 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M10 10l3.5 3.5",
    "refresh": "M13 8a5 5 0 1 1-1.5-3.5 M13 2.5v3h-3",
    "zap": "M9 2 4 9h4l-1 5 5-7H8z",
    "lock": "M4 7h8v6H4z M5.5 7V5a2.5 2.5 0 0 1 5 0v2",
    "trash": "M3 4h10 M6 4V2.5h4V4 M5 4l.6 9h4.8L11 4 M7 7v4M9 7v4",
    "folder": "M2 4h4l1.5 1.5H14V13H2z",
    "alert": "M8 2.5 14 13H2z M8 6.5v3 M8 11.2v.3",
    "info": "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 7.5v4 M8 5v.3",
    "pause": "M5 3v10 M11 3v10",
    "play": "M5 3l8 5-8 5z",
    "external": "M9 3h4v4 M13 3 7 9 M11 9v4H3V5h4",
    "up": "M8 12V4 M4.5 7.5 8 4l3.5 3.5",
    "down": "M8 4v8 M4.5 8.5 8 12l3.5-3.5",
    "flat": "M3 8h10",
    "inbox": "M2 9l2-6h8l2 6v4H2z M2 9h3.5l1 2h3l1-2H14",
    "mark": ("M24 6 38 11V24C38 32 32 38 24 42 16 38 10 32 10 24V11Z M24 15.5 30.5 19.25V26.75L24 30.5 "
             "17.5 26.75V19.25Z M18 34.5H30"),
}
_ICON_CACHE: dict = {}
# Motion: the native equivalent of prefers-reduced-motion. Set from the
# environment (AEGISFORGE_REDUCED_MOTION=1) or by the window from QSettings.
REDUCED_MOTION = os.environ.get("AEGISFORGE_REDUCED_MOTION", "") not in ("", "0", "false")


def icon_pixmap(name: str, color: str, size: int = 16, dpr: float = 2.0) -> QPixmap:
    """The icon path rendered in ``color`` (stroke, no fill) at ``size`` px."""
    key = (name, color, size, dpr)
    pm = _ICON_CACHE.get(key)
    if pm is not None:
        return pm
    d = ICONS.get(name, "")
    box = 48 if name == "mark" else 16
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box} {box}"><path d="{d}" fill="none" '
           f'stroke="{color}" stroke-width="{1.7 if box == 16 else 3.4}" stroke-linecap="round" stroke-linejoin="round"/></svg>')
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    _ICON_CACHE[key] = pm
    return pm


def icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    return QIcon(icon_pixmap(name, color or theme.current()["muted"], size))


def icon_label(name: str, color: str | None = None, size: int = 16) -> QLabel:
    lb = QLabel()
    lb.setPixmap(icon_pixmap(name, color or theme.current()["muted"], size))
    lb.setFixedSize(size, size)
    lb.setProperty("icon_name", name)
    lb.setProperty("icon_role", None if color else "muted")
    return lb


def mark_pixmap(path: str, size: int = 40, dpr: float = 2.0) -> QPixmap:
    """The brand mark (assets/webui/mark.svg) for the sidebar lockup."""
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    r = QSvgRenderer(path)
    if r.isValid():
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p)
        p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


# ── helpers ──────────────────────────────────────────────────────────────────

def set_prop(w: QWidget, name: str, value) -> None:
    """Set a stylesheet selector property and re-polish so the look updates."""
    w.setProperty(name, value)
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def clear_layout(lay: QLayout) -> None:
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def tabular(w: QWidget) -> QWidget:
    """Tabular (monospaced-width) figures for a label: the vitals and every
    numeric column line up. Qt 6.7+ exposes OpenType features; older builds
    fall back to the system face's default figures."""
    f = w.font()
    try:
        f.setFeature(QFont.Tag("tnum"), 1)
        w.setFont(f)
    except (AttributeError, TypeError):
        pass
    return w


def label(text: str = "", role: str | None = None, wrap: bool = False, **props) -> QLabel:
    lb = QLabel(str(text))
    if role:
        lb.setProperty("role", role)
    for k, v in props.items():
        lb.setProperty(k, v)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse if wrap else Qt.NoTextInteraction)
    return lb


def button(text: str, kind: str | None = None, size: str | None = None, ico: str | None = None,
           on_click=None, enabled: bool = True) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if size:
        b.setProperty("size", size)
    if ico:
        b.setProperty("icon_name", ico)
        b.setProperty("icon_kind", kind or "")
        b.setIcon(icon(ico, _button_icon_color(kind)))
        b.setIconSize(QSize(14, 14))
    b.setCursor(Qt.PointingHandCursor)
    b.setEnabled(enabled)
    if on_click is not None:
        b.clicked.connect(on_click)
    return b


def _button_icon_color(kind: str | None) -> str:
    s = theme.current()
    return {"primary": s["accent_ink"], "danger": s["action"], "danger-solid": s["action_ink"],
            "warn": s["amber"]}.get(kind or "", s["text_2"])


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            continue
        if x == "stretch":
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    if stretch_last:
        lay.addStretch(1)
    return w


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            continue
        if x == "stretch":
            lay.addStretch(1)
        else:
            lay.addWidget(x)
    return w


# ── cards + states ───────────────────────────────────────────────────────────

class Card(QFrame):
    """``.card``: surface + border + radius, with an optional eyebrow head row."""

    def __init__(self, title: str | None = None, note: str | None = None, head_widget: QWidget | None = None,
                 flush: bool = False):
        super().__init__()
        self.setProperty("card", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.lay = QVBoxLayout(self)
        pad = 0 if flush else theme.SP[4]
        self.lay.setContentsMargins(pad, pad, pad, pad)
        self.lay.setSpacing(theme.SP[3])
        self.note = None
        if title is not None or head_widget is not None:
            head = QHBoxLayout()
            head.setSpacing(theme.SP[3])
            if flush:
                head.setContentsMargins(theme.SP[4], theme.SP[3], theme.SP[4], 0)
            if title is not None:
                self.title = label(title.upper(), "eyebrow")
                head.addWidget(self.title)
            head.addStretch(1)
            if note is not None:
                self.note = label(note, "note")
                head.addWidget(self.note)
            if head_widget is not None:
                head.addWidget(head_widget)
            self.lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.SP[2])
        self.body.setContentsMargins(0, 0, 0, 0)
        self.lay.addLayout(self.body)

    def set_body(self, *widgets) -> None:
        clear_layout(self.body)
        for w in widgets:
            if w is not None:
                self.body.addWidget(w)

    def set_note(self, text: str) -> None:
        if self.note is not None:
            self.note.setText(text)


class Skeleton(QWidget):
    """``.skel``: shimmer placeholders while a read is in flight."""

    def __init__(self, n: int = 3, height: int = 12):
        super().__init__()
        self.n, self.h = n, height
        self.setMinimumHeight(n * (height + 8))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._t = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        if not REDUCED_MOTION:
            self.timer.start(60)

    def _tick(self):
        self._t = (self._t + 0.05) % 1.0
        self.update()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if not REDUCED_MOTION:
            self.timer.start(60)
        super().showEvent(e)

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base, hi = QColor(s["surface_2"]), QColor(s["elev"])
        w = self.width()
        for i in range(self.n):
            frac = 1.0 if i % 3 == 0 else 0.82 if i % 2 == 0 else 0.64
            mix = 0.5 + 0.5 * math.sin((self._t * 2 * math.pi) - i * 0.9) if not REDUCED_MOTION else 0.0
            c = QColor(int(base.red() + (hi.red() - base.red()) * mix), int(base.green() + (hi.green() - base.green()) * mix),
                       int(base.blue() + (hi.blue() - base.blue()) * mix))
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(0, i * (self.h + 8), w * frac, self.h), 4, 4)
        p.end()


def empty_state(ico: str, title: str, hint: str | None = None) -> QFrame:
    f = QFrame()
    f.setProperty("role", "empty")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(theme.SP[4], theme.SP[6], theme.SP[4], theme.SP[6])
    lay.setSpacing(6)
    lay.setAlignment(Qt.AlignCenter)
    i = icon_label(ico, theme.current()["faint"], 28)
    lay.addWidget(i, 0, Qt.AlignHCenter)
    t = label(title, "empty-title")
    t.setAlignment(Qt.AlignCenter)
    lay.addWidget(t)
    if hint:
        h = label(hint, wrap=True)
        h.setAlignment(Qt.AlignCenter)
        h.setMaximumWidth(420)
        lay.addWidget(h, 0, Qt.AlignHCenter)
    return f


def err_box(msg: str, retry=None) -> QFrame:
    f = QFrame()
    f.setProperty("role", "errbox")
    f.setAttribute(Qt.WA_StyledBackground, True)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(10)
    lay.addWidget(icon_label("alert", theme.current()["sev_critical"]), 0, Qt.AlignTop)
    m = label(msg, wrap=True)
    m.setObjectName("errmsg")
    lay.addWidget(m, 1)
    if retry is not None:
        lay.addWidget(button("Retry", "ghost", "xs", on_click=retry), 0, Qt.AlignTop)
    return f


def calm_box(text: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", "calm")
    f.setAttribute(Qt.WA_StyledBackground, True)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(9)
    lay.addWidget(icon_label("check", theme.current()["accent"]))
    lay.addWidget(label(text, wrap=True), 1)
    return f


def warn_box(text: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", "warnbox")
    f.setAttribute(Qt.WA_StyledBackground, True)
    lay = QHBoxLayout(f)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.addWidget(label(text, wrap=True), 1)
    return f


def info_line(text: str) -> QFrame:
    f = QFrame()
    f.setProperty("role", "infoline")
    lay = QHBoxLayout(f)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(icon_label("info", theme.current()["sky"]), 0, Qt.AlignTop)
    lay.addWidget(label(text, wrap=True), 1)
    return f


def notes_box(lines) -> QFrame:
    f = QFrame()
    f.setProperty("role", "notes")
    f.setAttribute(Qt.WA_StyledBackground, True)
    lay = QVBoxLayout(f)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.addWidget(label("\n".join(str(x) for x in lines), wrap=True))
    return f


def kv_rows(pairs) -> QWidget:
    """``.kv``: key / value rows (dashed separators), empties dropped."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    for k, v in pairs:
        if v is None or v == "":
            continue
        row = QFrame()
        row.setProperty("role", "row-dashed")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 5, 0, 5)
        rl.setSpacing(10)
        rl.addWidget(label(k, "kv-k"))
        rl.addStretch(1)
        val = label(str(v), "kv-v", wrap=True)
        val.setAlignment(Qt.AlignRight | Qt.AlignTop)
        rl.addWidget(val, 1)
        lay.addWidget(row)
    return w


class Working(QWidget):
    """``.working``: a spinner + text for an in-flight job."""

    def __init__(self, text: str):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, theme.SP[3], 0, theme.SP[3])
        lay.setSpacing(8)
        self.spinner = Spinner()
        lay.addWidget(self.spinner)
        lay.addWidget(label(text, "text2", wrap=True), 1)


class Spinner(QWidget):
    def __init__(self, size: int = 14):
        super().__init__()
        self.setFixedSize(size, size)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        if not REDUCED_MOTION:
            self.timer.start(70)

    def _tick(self):
        self.angle = (self.angle + 30) % 360
        self.update()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if not REDUCED_MOTION:
            self.timer.start(70)
        super().showEvent(e)

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        p.setPen(QPen(QColor(s["border_strong"]), 2))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(s["accent"]), 2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(r, (90 - self.angle) * 16, -100 * 16)
        p.end()


# ── badges, chips, severity ──────────────────────────────────────────────────

def badge(text: str, kind: str = "") -> QLabel:
    lb = label(text.upper(), "badge", kind=kind)
    lb.setAlignment(Qt.AlignCenter)
    return lb


def protected_badge() -> QWidget:
    w = hbox(icon_label("lock", theme.current()["sky"], 10), badge("protected", "protected"), spacing=4)
    w.setToolTip("Never a target: protected by the engine.")
    return w


def chip(text: str, tone: str = "") -> QLabel:
    return label(text, "chip", tone=tone)


class Lamp(QWidget):
    """``.lamp`` / ``.chip .dot``: a tone dot."""

    def __init__(self, tone: str = "", size: int = 8):
        super().__init__()
        self.tone = tone
        self.setFixedSize(size, size)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def paintEvent(self, e):
        s = theme.current()
        c = {"on": s["sev_ok"], "off": s["sev_critical"], "ok": s["sev_ok"]}.get(self.tone)
        if c is None:
            c = theme.tone_color(self.tone) if self.tone else s["faint"]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c))
        p.drawEllipse(QRectF(0, 0, self.width(), self.height()))
        p.end()


# ── painted data widgets ─────────────────────────────────────────────────────

class Bar(QWidget):
    """``.bar``: a 5px progress track with a tone-coloured fill."""

    def __init__(self, pct: float = 0.0, tone: str = "", height: int = 5):
        super().__init__()
        self.pct, self.tone = max(0.0, min(100.0, float(pct or 0))), tone
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAccessibleName("progress")

    def set(self, pct: float, tone: str = "") -> None:
        self.pct, self.tone = max(0.0, min(100.0, float(pct or 0))), tone
        self.setAccessibleDescription(f"{self.pct:.0f}%")
        self.update()

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        h, w = self.height(), self.width()
        p.setPen(QPen(QColor(s["border"]), 1))
        p.setBrush(QColor(s["bg_2"]))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)
        if self.pct > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.tone_color(self.tone)))
            p.drawRoundedRect(QRectF(1, 1, max(h - 2, (w - 2) * self.pct / 100), h - 2), (h - 2) / 2, (h - 2) / 2)
        p.end()


class Sparkline(QWidget):
    """``.spark``: the last n samples as a filled line with an end dot."""

    def __init__(self, values=None, tone: str = "", height: int = 32):
        super().__init__()
        self.values, self.tone = [], tone
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set(values or [], tone)

    def set(self, values, tone: str = "") -> None:
        self.values = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
        self.tone = tone
        self.update()

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        if len(self.values) < 2:
            pen = QPen(QColor(s["border"]), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(0, 1, w, 1)
            p.end()
            return
        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1.0
        pts = [(i / (len(self.values) - 1) * (w - 4) + 2, h - 3 - (v - lo) / span * (h - 8)) for i, v in enumerate(self.values)]
        c = QColor(theme.tone_color(self.tone))
        path = QPainterPath()
        path.moveTo(pts[0][0], h)
        for x, y in pts:
            path.lineTo(x, y)
        path.lineTo(pts[-1][0], h)
        fill = QColor(c)
        fill.setAlpha(26)
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPath(path)
        line = QPainterPath()
        line.moveTo(*pts[0])
        for x, y in pts[1:]:
            line.lineTo(x, y)
        p.setPen(QPen(c, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)
        p.setBrush(c)
        p.setPen(QPen(QColor(s["surface"]), 1.5))
        p.drawEllipse(QRectF(pts[-1][0] - 2.4, pts[-1][1] - 2.4, 4.8, 4.8))
        p.end()


class Ring(QWidget):
    """``.ring``: the score ring (0..100) with the value in the middle."""

    def __init__(self, score=None, size: int = 92):
        super().__init__()
        self.score = score
        self.setFixedSize(size, size)
        self.setAccessibleName("score")

    def set(self, score) -> None:
        self.score = score
        self.setAccessibleDescription(f"score {score} of 100" if score is not None else "no score")
        self.update()

    def tone(self) -> str:
        if self.score is None:
            return ""
        return "" if self.score >= 80 else "amber" if self.score >= 50 else "bad"

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(5, 5, self.width() - 10, self.height() - 10)
        p.setPen(QPen(QColor(s["bg_2"]), 7))
        p.drawEllipse(r)
        if self.score is not None:
            p.setPen(QPen(QColor(theme.tone_color(self.tone())), 7, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(r, 90 * 16, int(-360 * 16 * max(0, min(100, self.score)) / 100))
        f = p.font()
        f.setPointSize(theme.FS["2xl"] - 4)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(s["text"]))
        p.drawText(self.rect(), Qt.AlignCenter, "--" if self.score is None else str(int(self.score)))
        p.end()


class Switch(QAbstractButton):
    """``.switch``: the ember opt-in toggle (checked = ON)."""

    def __init__(self, on: bool = False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(bool(on))
        self.setFixedSize(40, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.busy = False

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, e):
        s = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        track = QColor(s["tint_ember"]) if on else QColor(s["elev"])
        border = QColor(s["action"]) if on else QColor(s["border_strong"])
        if not self.isEnabled():
            track.setAlpha(110)
            border.setAlpha(110)
        p.setPen(QPen(border, 1))
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0.5, 0.5, 39, 21), 11, 11)
        knob = QColor(s["amber"]) if self.busy else QColor(s["action"]) if on else QColor(s["muted"])
        p.setPen(Qt.NoPen)
        p.setBrush(knob)
        x = 21 if on else 3
        p.drawEllipse(QRectF(x, 3, 16, 16))
        p.end()


class Stepper(QWidget):
    """``.stepper``: the funnel steps (Scan -> Review -> Confirm -> Done)."""

    def __init__(self, steps):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.steps, self.links = [], []
        for i, name in enumerate(steps):
            if i:
                ln = label("--", "step-link")
                self.links.append(ln)
                lay.addWidget(ln)
            st = label(f"{i + 1}  {name}", "step", state="todo")
            self.steps.append(st)
            lay.addWidget(st)
        lay.addStretch(1)
        self.set(0)

    def set(self, idx: int, done_all: bool = False) -> None:
        for i, st in enumerate(self.steps):
            state = "done" if (i < idx or done_all) else "current" if i == idx else "todo"
            name = st.text().split("  ", 1)[-1]
            st.setText(("✓  " if state == "done" else f"{i + 1}  ") + name)
            set_prop(st, "state", state)
            if i:
                set_prop(self.links[i - 1], "done", state in ("done", "current") and (i - 1 < idx or done_all))


# ── the Confirm dialog (every mutating funnel passes through here) ───────────

class ConfirmDialog(QDialog):
    """``.modal``: title + body widgets + optional acknowledgement + Cancel / OK.

    With ``ack`` the OK button stays DISABLED until the acknowledgement is
    ticked: that tick is what the funnel passes as ``override`` to the
    engine. Escape / Cancel reject; nothing confirms but the OK click."""

    def __init__(self, parent, title: str, body, ok: str = "Confirm", ack: str | None = None, danger: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.ack_text = ack
        s = theme.current()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = QFrame()
        box.setProperty("role", "modal")
        box.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(box)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(theme.SP[5], theme.SP[5], theme.SP[5], theme.SP[5])
        lay.setSpacing(theme.SP[3])
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(icon_label("alert" if danger else "info", s["action"] if danger else s["sky"], 20), 0, Qt.AlignTop)
        self.title_label = label(title, "modal-title", wrap=True)
        head.addWidget(self.title_label, 1)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(theme.SP[2])
        for w in (body if isinstance(body, (list, tuple)) else [body]):
            if isinstance(w, str):
                w = label(w, "text2", wrap=True)
            if w is not None:
                self.body.addWidget(w)
        lay.addLayout(self.body)
        self.ack_box = None
        if ack:
            f = QFrame()
            f.setProperty("role", "ack")
            f.setAttribute(Qt.WA_StyledBackground, True)
            fl = QHBoxLayout(f)
            fl.setContentsMargins(10, 9, 10, 9)
            self.ack_box = QCheckBox(ack)
            self.ack_box.setChecked(False)
            self.ack_box.toggled.connect(self._on_ack)
            fl.addWidget(self.ack_box)
            lay.addWidget(f)
        actions = QHBoxLayout()
        actions.setSpacing(theme.SP[2])
        actions.addStretch(1)
        self.cancel_btn = button("Cancel", "ghost", on_click=self.reject)
        self.ok_btn = button(ok, "danger-solid" if danger else "primary", on_click=self.accept)
        self.ok_btn.setEnabled(not ack)
        self.ok_btn.setDefault(True)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.ok_btn)
        lay.addLayout(actions)
        (self.ack_box or self.ok_btn).setFocus()

    def _on_ack(self, on: bool):
        self.ok_btn.setEnabled(bool(on))

    @property
    def acked(self) -> bool:
        return bool(self.ack_box is not None and self.ack_box.isChecked())

    def accept(self):
        # belt and braces: with an acknowledgement, OK is only honoured ticked
        if self.ack_box is not None and not self.ack_box.isChecked():
            return
        super().accept()


# ── toasts ───────────────────────────────────────────────────────────────────

class Toast(QFrame):
    closed = Signal(object)

    def __init__(self, text: str, kind: str = "ok", ms: int = 4200):
        super().__init__()
        self.setProperty("role", "toast")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WA_StyledBackground, True)
        s = theme.current()
        col = {"ok": s["accent"], "bad": s["sev_critical"], "warn": s["sev_medium"], "ember": s["sev_high"],
               "info": s["sky"]}.get(kind, s["accent"])
        ico = {"ok": "check", "bad": "alert", "warn": "alert", "ember": "ember", "info": "info"}.get(kind, "info")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(10)
        lay.addWidget(icon_label(ico, col), 0, Qt.AlignTop)
        self.text = label(text, wrap=True)
        lay.addWidget(self.text, 1)
        x = button("", "ghost", on_click=self.dismiss)
        x.setProperty("role", "toast-close")
        x.setIcon(icon("x", s["faint"]))
        x.setIconSize(QSize(12, 12))
        x.setFixedSize(18, 18)
        x.setAccessibleName("Dismiss")
        lay.addWidget(x, 0, Qt.AlignTop)
        self.setMaximumWidth(420)
        QTimer.singleShot(ms, self.dismiss)

    def dismiss(self):
        self.closed.emit(self)
        self.hide()
        self.deleteLater()


class ToastHost(QWidget):
    """``.toasts``: the bottom-right stack. Lives on top of the main window;
    re-anchored on every resize by the window."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(theme.SP[2])
        self.lay.addStretch(1)
        self.setFixedWidth(420)
        self.messages: list = []   # (kind, text) history for tests + the status line

    def toast(self, text: str, kind: str = "ok", ms: int = 4200) -> Toast:
        self.messages.append((kind, text))
        t = Toast(text, kind, ms)
        t.closed.connect(self._gone)
        self.lay.addWidget(t)
        self.show()
        self.raise_()
        self.adjustSize()
        self.reanchor()
        return t

    def _gone(self, t):
        self.adjustSize()
        self.reanchor()

    def reanchor(self):
        par = self.parentWidget()
        if par is None:
            return
        self.adjustSize()
        self.move(par.width() - self.width() - theme.SP[4], par.height() - self.height() - theme.SP[4])
