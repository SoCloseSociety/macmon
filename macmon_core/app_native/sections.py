"""AegisForge native app -- the seven sections and their funnels.

Each section is a plain QWidget built from the component kit. READS call the
``app_webui`` dict builders (``shell.reads``) on a worker; ACTIONS call
``app_api.Api`` (``shell.api``) on a worker, and ONLY from a funnel's confirm
step: guard -> Confirm dialog (acknowledgement when the sweep-style guard
names the target, which becomes ``override``) -> engine -> toast with the
engine's own outcome. Nothing here runs at construction; a section loads
when it is entered, an action when its button is clicked.

The funnels mirror assets/webui/app.js one for one (same wording, same
arguments to the bridge), so the native app and the page are the same
product on two surfaces.
"""
from __future__ import annotations

import datetime as _dt
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLineEdit, QSizePolicy, QTabBar, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import theme, workers
from .widgets import (Bar, Card, Lamp, Ring, Skeleton, Sparkline, Stepper, Switch, Working, badge, button,
                      calm_box, clear_layout, empty_state, err_box, hbox, icon_label, icon_pixmap, info_line,
                      kv_rows, label, notes_box, protected_badge, set_prop, tabular, warn_box)

# ── formatting (app.js fmtBytes / fmtAge / num) ─────────────────────────────


def fmt_bytes(n) -> str:
    n = float(n or 0)
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024
        i += 1
    return f"{n:.{1 if i else 0}f} {units[i]}"


def fmt_age(s) -> str:
    if s is None:
        return "?"
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def num(v, d: int = 1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "--"
    if f != f:  # NaN
        return "--"
    return f"{f:.{d}f}"


SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "ok": -1}
VITALS = {"cpu": ("CPU", "%", 100, 1), "ram": ("RAM", "%", 100, 1), "swap_gb": ("Swap", "GB", 32, 1),
          "load1": ("Load", "", 16, 2), "disk_free_gb": ("Disk free", "GB", 512, 0)}


def tone_of(vid: str, v) -> str:
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        v = 0.0
    if vid == "swap_gb":
        return "bad" if v > 16 else "ember" if v > 8 else "amber" if v > 4 else ""
    if vid == "ram":
        return "bad" if v > 92 else "ember" if v > 85 else "amber" if v > 72 else ""
    if vid == "cpu":
        return "ember" if v > 90 else "amber" if v > 70 else ""
    if vid == "load1":
        return "ember" if v > 16 else "amber" if v > 10 else ""
    if vid == "disk_free_gb":
        return "bad" if v < 5 else "amber" if v < 15 else ""
    return ""


def pct(v, mx) -> float:
    try:
        return max(0.0, min(100.0, float(v or 0) / mx * 100))
    except (TypeError, ValueError):
        return 0.0


def trend_of(vid: str, series) -> str | None:
    v = [float(x) for x in series if x is not None]
    if len(v) < 4:
        return None
    tail = v[-6:]
    a, b = tail[0], tail[-1]
    eps = max(abs(a) * 0.03, 1.0 if vid in ("cpu", "ram") else 0.05)
    return "up" if b - a > eps else "down" if a - b > eps else "flat"


# ── table helper ─────────────────────────────────────────────────────────────

def make_table(headers, numeric=()) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([h.upper() for h in headers])
    t.verticalHeader().hide()
    t.setShowGrid(False)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.setFocusPolicy(Qt.StrongFocus)
    t.setAlternatingRowColors(False)
    t.setWordWrap(False)
    t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.Interactive)
    hh.setStretchLastSection(True)
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for i in numeric:
        hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
    t.setMinimumHeight(240)
    return t


def cell(text, align=None, tooltip: str | None = None) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text if text is not None else ""))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if align is not None:
        it.setTextAlignment(align | Qt.AlignVCenter)
    if tooltip:
        it.setToolTip(tooltip)
    return it


def view_head(ico: str, title: str, lead: str) -> QWidget:
    box = QFrame()
    box.setProperty("role", "vh-ico")
    box.setAttribute(Qt.WA_StyledBackground, True)
    box.setFixedSize(34, 34)
    bl = QHBoxLayout(box)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.addWidget(icon_label(ico, theme.current()["accent"], 18), 0, Qt.AlignCenter)
    text = vbox_(label(title, "h1"), label(lead, "lead", wrap=True), spacing=4)
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(theme.SP[3])
    lay.addWidget(box, 0, Qt.AlignTop)
    lay.addWidget(text, 1)
    return w


def vbox_(*widgets, spacing=8):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is not None:
            lay.addWidget(x)
    return w


class Section(QWidget):
    """Base: a view head + a vertical stack; ``enter`` / ``leave`` hooks;
    ``job`` runs an engine call off the UI thread."""
    key = ""
    title = ""
    lead = ""
    ico = "pulse"

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.setObjectName("view")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(theme.SP[4])
        self.lay.addWidget(view_head(self.ico, self.title, self.lead))

    @property
    def api(self):
        return self.shell.api

    @property
    def reads(self):
        return self.shell.reads

    def toast(self, text, kind="ok", ms=4200):
        return self.shell.toast(text, kind, ms)

    def job(self, fn, *args, done=None, failed=None, **kw):
        return workers.run(fn, *args, done=done, failed=failed, owner=self, **kw)

    def enter(self):
        pass

    def leave(self):
        pass

    def retheme(self):
        """Re-render cached data after a theme swap (painted colours)."""


# ── 1. Overview ──────────────────────────────────────────────────────────────

class VitalCard(QFrame):
    def __init__(self, vid: str):
        super().__init__()
        self.vid = vid
        self.setProperty("card", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        name, unit, _mx, _dec = VITALS[vid]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SP[4], theme.SP[3], theme.SP[4], theme.SP[3])
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label(name.upper(), "eyebrow"))
        head.addStretch(1)
        self.trend = icon_label("flat", theme.current()["faint"], 12)
        self.trend.hide()
        head.addWidget(self.trend)
        lay.addLayout(head)
        vrow = QHBoxLayout()
        vrow.setSpacing(3)
        self.value = tabular(label("--", "value"))
        vrow.addWidget(self.value, 0, Qt.AlignBaseline)
        self.unit = label(unit, "unit")
        vrow.addWidget(self.unit, 0, Qt.AlignBaseline)
        vrow.addStretch(1)
        lay.addLayout(vrow)
        self.bar = Bar(0, "")
        lay.addWidget(self.bar)
        self.spark = Sparkline([], "")
        lay.addWidget(self.spark)

    def set(self, val, series) -> None:
        _name, _unit, mx, dec = VITALS[self.vid]
        tn = tone_of(self.vid, val)
        self.value.setText(num(val, dec))
        set_prop(self.value, "tone", tn)
        self.bar.set(pct(val, mx), tn)
        self.spark.set(series, tn)
        d = trend_of(self.vid, series)
        if d:
            self.trend.setPixmap(icon_pixmap(d, theme.tone_color(tn) if tn else theme.current()["faint"], 12))
            self.trend.show()
        else:
            self.trend.hide()


class Overview(Section):
    key, title, ico = "overview", "Overview", "pulse"
    lead = "What the 60s sampler sees, and where each vital is heading."

    def __init__(self, shell):
        super().__init__(shell)
        self.history: list = []
        self.status: dict | None = None
        self.health_at = 0.0
        self.health_data = None
        grid = QGridLayout()
        grid.setSpacing(theme.SP[3])
        self.vitals = {}
        for i, vid in enumerate(VITALS):
            vc = VitalCard(vid)
            self.vitals[vid] = vc
            grid.addWidget(vc, 0, i)
            grid.setColumnStretch(i, 1)
        self.lay.addLayout(grid)
        two = QHBoxLayout()
        two.setSpacing(theme.SP[4])
        forge = label("", "eyebrow")
        forge.setObjectName("forgeTitle")
        self.forge_title = forge
        self.findings_card = Card(title="", note="")
        self.findings_card.title.hide()
        self.findings_card.lay.itemAt(0).layout().insertWidget(0, forge)
        self.findings_card.set_body(Skeleton(3))
        two.addWidget(self.findings_card, 1)
        self.health_btn = button("Re-check", "ghost", "sm", ico="refresh", on_click=lambda: self.load_health(True))
        self.health_card = Card(title="Health score", head_widget=self.health_btn)
        self.health_card.set_body(hbox(Ring(None), Skeleton(5), spacing=theme.SP[4]))
        two.addWidget(self.health_card, 1)
        self.lay.addLayout(two)
        self.alerts_card = Card(title="Recent alerts", note="sentinel_alerts.log")
        self.alerts_card.set_body(Skeleton(3))
        self.lay.addWidget(self.alerts_card)
        self.lay.addStretch(1)
        self._forge_title()

    def _forge_title(self):
        self.forge_title.setText(f'<span style="color:{theme.current()["action"]}">FORGE</span> ANTICIPATION')

    def enter(self):
        self.shell.tick()
        self.load_health(False)

    def retheme(self):
        self._forge_title()
        if self.status is not None:
            self.render_status(self.status)
        if self.health_data is not None:
            self._render_health(self.health_data)

    # status (vitals + findings + alerts) is pushed by the window's tick
    def set_history(self, rows):
        self.history = rows or []
        self.findings_card.set_note(f"{len(self.history)} samples" if self.history else "")

    def render_status(self, d: dict) -> None:
        self.status = d
        vit = d.get("vitals") or {}
        for vid, vc in self.vitals.items():
            vc.set(vit.get(vid), [r.get(vid) for r in self.history])
            set_prop(vc, "stale", bool(d.get("stale")))
        items = []
        if d.get("error"):
            items.append(self._finding("engine", str(d["error"])))
        fl = d.get("findings") or []
        if not fl:
            items.append(calm_box("All clear -- forge cold."))
        for f in sorted(fl, key=lambda f: -SEV_RANK.get(f.get("severity"), 0)):
            items.append(self._finding(f.get("severity", "info"), f.get("text", "")))
        self.findings_card.set_body(*items)
        al = d.get("alerts") or []
        if not al:
            self.alerts_card.set_body(empty_state("inbox", "No recent alerts", "The sentinel writes here when a detector fires."))
        else:
            rows = []
            for x in al:
                fr = QFrame()
                fr.setProperty("role", "alert-line")
                l2 = QHBoxLayout(fr)
                l2.setContentsMargins(0, 5, 0, 5)
                l2.addWidget(label(str(x), wrap=True))
                rows.append(fr)
            self.alerts_card.set_body(*rows)

    def render_status_error(self, msg: str) -> None:
        if self.status is None:
            self.findings_card.set_body(err_box("status unavailable: " + msg, self.shell.tick))
            self.alerts_card.set_body(empty_state("inbox", "No recent alerts"))

    @staticmethod
    def _finding(sev: str, text: str) -> QFrame:
        f = QFrame()
        f.setProperty("role", "finding")
        f.setProperty("sev", sev)
        f.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(f)
        lay.setContentsMargins(11, 9, 11, 9)
        lay.setSpacing(10)
        lay.addWidget(label(sev.upper(), "lvl", sev=sev), 0, Qt.AlignTop)
        lay.addWidget(label(text, "text2", wrap=True), 1)
        return f

    def load_health(self, force: bool) -> None:
        if not force and self.health_at and time.time() - self.health_at < 300:
            return
        self.health_btn.setEnabled(False)
        self.health_card.set_body(hbox(Ring(None), Skeleton(5), spacing=theme.SP[4]))
        self.job(self.reads.health, done=self._on_health, failed=self._on_health_failed)

    def _on_health(self, d: dict):
        self.health_btn.setEnabled(True)
        self.health_at = time.time()
        self.health_data = d
        self._render_health(d)

    def _on_health_failed(self, msg: str):
        self.health_btn.setEnabled(True)
        self.health_card.set_body(err_box("health check failed: " + msg, lambda: self.load_health(True)))

    def _render_health(self, d: dict):
        order = {"fail": 0, "warn": 1, "pass": 2}
        checks = sorted(d.get("checks") or [], key=lambda c: order.get(c.get("status"), 3))
        col = QWidget()
        cl = QVBoxLayout(col)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(5)
        for c in checks[:9]:
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(label(str(c.get("status", "")).upper(), "st", status=c.get("status")))
            row.addWidget(label(c.get("name", "")))
            dt = label(c.get("detail", ""), "muted")
            dt.setToolTip(c.get("detail", ""))
            row.addWidget(dt, 1)
            cl.addLayout(row)
        if len(checks) > 9:
            cl.addWidget(label(f"+{len(checks) - 9} more (macmon health)", "faint"))
        cl.addStretch(1)
        ring = Ring(d.get("score"))
        self.health_card.set_body(hbox(ring, col, spacing=theme.SP[4]))


# ── 2. Processes ─────────────────────────────────────────────────────────────

VERBS = {
    "kill": ("Kill", "Sends SIGTERM (graceful, never SIGKILL). The process may save state and exit; a stuck one may ignore it."),
    "suspend": ("Suspend", "Sends SIGSTOP: the process freezes (and its windows stop responding) until you Resume it."),
    "resume": ("Resume", "Sends SIGCONT: the process continues where it was frozen."),
}
GUARD_TEXT = "Guarded ({0}): AegisForge would never touch this on its own -- it looks like a live workload, an agent, the fleet or a user app."
ACK_GUARDED = "I understand: I am doing this by hand, on a guarded process."


class Processes(Section):
    key, title, ico = "processes", "Processes", "cpu"
    lead = ("Top processes by CPU / RAM. Kill sends SIGTERM (never SIGKILL); suspend freezes until resumed. "
            "Protected processes are never targets.")

    def __init__(self, shell):
        super().__init__(shell)
        self.rows: list = []
        self.total = 0
        self.sort = "cpu"
        self.busy = False
        self.timer = QTimer(self)
        self.timer.setInterval(15_000)
        self.timer.timeout.connect(self._auto)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name, PID or category")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.setAccessibleName("Filter processes")
        self.search.textChanged.connect(lambda _t: self.render())
        self.sort_box = QComboBox()
        for v, t in (("cpu", "Sort: CPU"), ("ram", "Sort: RAM"), ("name", "Sort: name"), ("runtime", "Sort: runtime")):
            self.sort_box.addItem(t, v)
        self.sort_box.currentIndexChanged.connect(lambda _i: self.load())
        self.refresh_btn = button("Refresh", "ghost", ico="refresh", on_click=self.load)
        self.purge_btn = button("Purge RAM (sudo -n)", "ghost", ico="zap", on_click=self.purge_funnel)
        if not shell.is_mac:
            self.purge_btn.setEnabled(False)
            self.purge_btn.setToolTip("Purge inactive RAM is macOS only.")
        self.lay.addWidget(hbox(self.search, self.sort_box, self.refresh_btn, "stretch", self.purge_btn, spacing=10))
        self.table = make_table(["Name", "PID", "CPU %", "RAM", "Category", "Status", "Actions"], numeric=(1, 2, 3))
        self.lay.addWidget(self.table, 1)
        self.note = label("", "faint")
        self.lay.addWidget(self.note)

    def enter(self):
        self.load()
        self.timer.start()

    def leave(self):
        self.timer.stop()

    def retheme(self):
        self.render()

    def _auto(self):
        if not self.shell.dialog_open:
            self.load()

    def load(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_btn.setEnabled(False)
        sort = self.sort_box.currentData() or "cpu"
        self.job(self.reads.processes, sort, done=self._on_rows, failed=self._on_failed)

    def _on_rows(self, d: dict):
        self.busy = False
        self.refresh_btn.setEnabled(True)
        self.rows = d.get("processes") or []
        self.total = d.get("total") or len(self.rows)
        self.sort = d.get("sort") or "cpu"
        self.render()

    def _on_failed(self, msg: str):
        self.busy = False
        self.refresh_btn.setEnabled(True)
        self.table.setRowCount(0)
        self.note.setText("could not list processes: " + msg)
        self.toast("could not list processes: " + msg, "bad")

    def filtered(self) -> list:
        q = (self.search.text() or "").strip().lower()
        return [p for p in self.rows if not q or q in str(p.get("name", "")).lower() or str(p.get("pid")) == q
                or q in str(p.get("category", ""))]

    def render(self):
        rows = self.filtered()[:120]
        t = self.table
        t.setRowCount(0)
        t.setRowCount(len(rows))
        s = theme.current()
        for i, p in enumerate(rows):
            stopped = p.get("status") == "stopped"
            name = cell(p.get("name", ""), tooltip=p.get("name", ""))
            if p.get("protected"):
                name.setForeground(QColor(s["muted"]))
            t.setItem(i, 0, name)
            t.setItem(i, 1, cell(p.get("pid"), Qt.AlignRight))
            cpu = cell(num(p.get("cpu"), 1), Qt.AlignRight)
            c = float(p.get("cpu") or 0)
            if c > 50:
                cpu.setForeground(QColor(s["sev_high"] if c > 90 else s["sev_medium"]))
            t.setItem(i, 2, cpu)
            t.setItem(i, 3, cell(p.get("ram_label", ""), Qt.AlignRight))
            cat = str(p.get("category", ""))
            t.setCellWidget(i, 4, hbox(badge(cat, {"ide": "sky", "llm": "ember", "docker": "sky"}.get(cat, "")), "stretch",
                                       margins=(8, 2, 8, 2)))
            st = str(p.get("status", "")) + (f"  · {fmt_age(p.get('age_s'))}" if p.get("age_s") is not None else "")
            st_item = cell(st)
            if stopped:
                st_item.setForeground(QColor(s["sev_medium"]))
            t.setItem(i, 5, st_item)
            if p.get("protected"):
                acts = hbox("stretch", protected_badge(), margins=(8, 2, 8, 2))
            else:
                verb = "resume" if stopped else "suspend"
                acts = hbox("stretch",
                            button(VERBS[verb][0], "ghost", "xs", on_click=lambda _c=False, v=verb, pp=p: self.signal_funnel(v, pp)),
                            button("Kill", "danger", "xs", on_click=lambda _c=False, pp=p: self.signal_funnel("kill", pp)),
                            margins=(8, 2, 8, 2), spacing=4)
            t.setCellWidget(i, 6, acts)
        t.resizeRowsToContents()
        self.note.setText(f"{len(rows)} shown of {len(self.rows)} listed ({self.total} matched the dev filter). "
                          f"Sorted by {self.sort}. Auto-refresh 15s.")

    # the signal funnel: guard -> confirm (ack when guarded) -> engine -> toast
    def signal_funnel(self, verb: str, p: dict):
        self.job(self.api.process_guard, p.get("pid"), p.get("created"),
                 done=lambda g: self._guard_result(verb, p, g), failed=lambda m: self.toast(m, "bad"))

    def _guard_result(self, verb: str, p: dict, g: dict):
        if not g.get("ok"):
            self.toast(g.get("detail") or "refused", "bad")
            self.load()
            return
        title, what = VERBS[verb]
        body = [kv_rows([("process", g.get("name")), ("pid", g.get("pid")), ("user", g.get("user")), ("command", g.get("cmd"))]),
                info_line(what)]
        if g.get("in_service") is True:
            body.append(warn_box("This process (or a child) has an open or listening socket: something may be talking to it right now."))
        if g.get("guard"):
            body.append(warn_box(GUARD_TEXT.format(g["guard"])))
        self.shell.confirm(title=f"{title} {g.get('name')} (PID {g.get('pid')})?", body=body, ok=title,
                           danger=verb != "resume", ack=ACK_GUARDED if g.get("guard") else None,
                           on_ok=lambda acked: self._signal(verb, p, g, acked))

    def _signal(self, verb: str, p: dict, g: dict, acked: bool):
        title = VERBS[verb][0]
        self.toast(f"{title}ing {g.get('name')}...", "warn", 1500)
        ct = g.get("ct") if g.get("ct") is not None else p.get("created")
        fn = getattr(self.api, f"{verb}_process")
        # (pid, create_time, override): the guard's own "ct" is echoed back so a
        # recycled PID is refused by the engine; override only matters when guarded.
        self.job(fn, g.get("pid"), ct, acked, done=lambda r: self._signal_done(verb, r), failed=lambda m: self.toast(m, "bad"))

    def _signal_done(self, verb: str, r: dict):
        ok = bool(r.get("ok"))
        self.toast(r.get("detail") or ("done" if ok else "refused"), ("ember" if verb == "kill" else "ok") if ok else "bad")
        QTimer.singleShot(900, self.load)

    def purge_funnel(self):
        body = [label("Runs sudo -n purge. Non-destructive: it flushes inactive memory and the disk cache (apps may feel cold "
                      "for a moment). It never prompts -- it fails fast unless macmon sentinel --setup-purge installed the "
                      "sudoers rule.", "text2", wrap=True)]
        self.shell.confirm(title="Purge inactive RAM?", body=body, ok="Purge", danger=False, on_ok=lambda _a: self._purge())

    def _purge(self):
        self.purge_btn.setEnabled(False)
        self.job(self.api.purge_ram, done=self._purge_done, failed=lambda m: (self.purge_btn.setEnabled(True), self.toast(m, "bad")))

    def _purge_done(self, r: dict):
        self.purge_btn.setEnabled(True)
        self.toast(r.get("detail") or "done", "ok" if r.get("ok") else "bad", 6000)


# ── 3. Clean ─────────────────────────────────────────────────────────────────

class Clean(Section):
    key, title, ico = "clean", "Clean", "broom"
    lead = ("Scan first, review every category, then clean what you ticked. Trash-first: nothing is deleted permanently, "
            "and anything Trash refuses is skipped, never escalated.")

    def __init__(self, shell):
        super().__init__(shell)
        self.state = "idle"
        self.scan: dict | None = None
        self.picked: set = set()
        self.result: dict | None = None
        self.error: str | None = None
        self.stepper = Stepper(["Scan", "Review", "Confirm", "Done"])
        self.lay.addWidget(self.stepper)
        self.stage = Card()
        self.lay.addWidget(self.stage)
        self.lay.addStretch(1)
        self.render()

    def enter(self):
        self.render()

    def retheme(self):
        self.render()

    def render(self):
        s = self.state
        if s in ("idle", "error"):
            self.stepper.set(0)
            self.scan_btn = button("Scan", "primary", ico="search", on_click=self.do_scan)
            self.stage.set_body(
                label("Scan for junk", "h3"),
                label("Old logs, crash reports, stale temp files, browser caches (cache + crash reports only -- never cookies, "
                      "history or logins), app caches and the biggest user caches. The scan touches nothing.", "lead", wrap=True),
                err_box(self.error) if self.error else None,
                hbox(self.scan_btn, "stretch"))
        elif s == "scanning":
            self.stepper.set(0)
            self.stage.set_body(Working("scanning system junk, browser, app and user caches..."), Skeleton(4))
        elif s == "review":
            self.stepper.set(1)
            self._render_review()
        elif s == "running":
            self.stepper.set(2)
            self.stage.set_body(Working("moving to the Trash..."))
        elif s == "done":
            self.stepper.set(3, True)
            r = self.result or {}
            cleaned = r.get("cleaned") or []
            big = hbox(tabular(label(f"freed {r.get('freed_label', '?')}", "big")),
                       label(f"{len(cleaned)} categor{'y' if len(cleaned) == 1 else 'ies'} -> Trash", "muted"), "stretch", spacing=8)
            self.stage.set_body(
                label("Cleaned", "result-ok"), big,
                label(" · ".join(str(c) for c in cleaned), "text2", wrap=True),
                label(f"{r.get('skipped')} path(s) skipped (Trash refused them; nothing was force-deleted).", "warn-line", wrap=True)
                if r.get("skipped") else None,
                notes_box(r["notes"]) if r.get("notes") else None,
                hbox(button("Scan again", "ghost", ico="refresh", on_click=self.reset), "stretch"))

    def reset(self):
        self.state, self.scan, self.result, self.error = "idle", None, None, None
        self.picked = set()
        self.render()

    def _render_review(self):
        cats = (self.scan or {}).get("categories") or []
        if not cats:
            self.stage.set_body(calm_box("Nothing to clean -- the system is already clean."),
                                hbox(button("Back", "ghost", on_click=self.reset), "stretch"))
            return
        picks = QWidget()
        pl = QVBoxLayout(picks)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(5)
        self.pick_boxes = {}
        for c in cats:
            row = QFrame()
            row.setProperty("role", "pick")
            row.setProperty("checked", c["id"] in self.picked)
            row.setAttribute(Qt.WA_StyledBackground, True)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 10, 8)
            rl.setSpacing(12)
            cb = QCheckBox(str(c.get("name", "?")))
            cb.setChecked(c["id"] in self.picked)
            cb.toggled.connect(lambda on, cid=c["id"], fr=row: self._toggle(cid, on, fr))
            self.pick_boxes[c["id"]] = cb
            rl.addWidget(cb, 1)
            n = int(c.get("count") or 0)
            rl.addWidget(label(f"{n} item{'' if n == 1 else 's'}", "faint"))
            sz = tabular(label(c.get("size_label", ""), "text2"))
            sz.setMinimumWidth(80)
            sz.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(sz)
            pl.addWidget(row)
        sel = [c for c in cats if c["id"] in self.picked]
        sel_bytes = sum(int(c.get("size") or 0) for c in sel)
        self.sel_label = tabular(label(fmt_bytes(sel_bytes), "big"))
        self.sel_note = label(f"selected in {len(sel)} categor{'y' if len(sel) == 1 else 'ies'}", "muted")
        self.clean_btn = button(f"Clean {fmt_bytes(sel_bytes)}", "danger-solid", ico="trash", on_click=self.confirm_step,
                                enabled=bool(sel))
        summary = QFrame()
        summary.setProperty("role", "summary")
        sl = QHBoxLayout(summary)
        sl.setContentsMargins(0, 10, 0, 0)
        sl.setSpacing(12)
        sl.addWidget(self.sel_label)
        sl.addWidget(self.sel_note)
        sl.addStretch(1)
        sl.addWidget(button("All", "ghost", "sm", on_click=lambda: self._pick_all(True)))
        sl.addWidget(button("None", "ghost", "sm", on_click=lambda: self._pick_all(False)))
        sl.addWidget(self.clean_btn)
        self.stage.set_body(
            label("Review what goes to the Trash", "h3"),
            label(f"Found {self.scan.get('total_label')} across {len(cats)} categories. Untick anything you want to keep.", "lead", wrap=True),
            picks,
            notes_box(self.scan["notes"]) if self.scan.get("notes") else None,
            summary)

    def _toggle(self, cid, on: bool, row: QFrame):
        if on:
            self.picked.add(cid)
        else:
            self.picked.discard(cid)
        set_prop(row, "checked", on)
        self._update_summary()

    def _pick_all(self, on: bool):
        cats = (self.scan or {}).get("categories") or []
        self.picked = {c["id"] for c in cats} if on else set()
        self._render_review()

    def _update_summary(self):
        cats = (self.scan or {}).get("categories") or []
        sel = [c for c in cats if c["id"] in self.picked]
        b = sum(int(c.get("size") or 0) for c in sel)
        self.sel_label.setText(fmt_bytes(b))
        self.sel_note.setText(f"selected in {len(sel)} categor{'y' if len(sel) == 1 else 'ies'}")
        self.clean_btn.setText(f"Clean {fmt_bytes(b)}")
        self.clean_btn.setEnabled(bool(sel))

    def selected(self) -> list:
        return [c for c in ((self.scan or {}).get("categories") or []) if c["id"] in self.picked]

    def do_scan(self):
        self.state, self.error = "scanning", None
        self.render()
        self.job(self.api.clean_scan, done=self._scanned, failed=self._failed)

    def _scanned(self, r: dict):
        if not r.get("ok"):
            return self._failed(r.get("detail") or "scan failed")
        self.scan = r
        self.picked = {c["id"] for c in r.get("categories") or []}
        self.state = "review"
        self.render()

    def _failed(self, msg: str):
        self.state, self.error = "error", msg
        self.toast(msg, "bad")
        self.render()

    def confirm_step(self):
        cats = self.selected()
        b = sum(int(c.get("size") or 0) for c in cats)
        names = ", ".join(str(c.get("name")) for c in cats)
        body = [label(f"{len(cats)} categor{'y' if len(cats) == 1 else 'ies'}: {names}.", "text2", wrap=True),
                info_line("Trash-first: files go to the Trash (recoverable). Anything the Trash refuses is skipped -- never deleted permanently.")]
        self.shell.confirm(title=f"Move {fmt_bytes(b)} to the Trash?", body=body, ok="Clean",
                           on_ok=lambda _a: self.execute([c["id"] for c in cats]))

    def execute(self, ids: list):
        self.state = "running"
        self.render()
        self.job(self.api.clean_execute, list(ids), done=self._executed, failed=self._failed)

    def _executed(self, r: dict):
        if not r.get("ok"):
            return self._failed(r.get("detail") or "clean refused")
        self.result, self.state = r, "done"
        self.toast(f"Freed {r.get('freed_label')}", "ok")
        self.render()


# ── 4. Security ──────────────────────────────────────────────────────────────

class Security(Section):
    key, title, ico = "security", "Security", "shield"
    lead = "Firewall, SIP, Gatekeeper, FileVault, suspicious connections, remote tools, startup items, sharing and SSH -- scored /100."

    def __init__(self, shell):
        super().__init__(shell)
        self.busy = False
        self.data = None
        self.scan_btn = button("Run security scan", "primary", ico="shield", on_click=self.scan)
        self.sec_note = label("", "muted")
        self.lay.addWidget(hbox(self.scan_btn, self.sec_note, "stretch", spacing=10))
        self.result = QVBoxLayout()
        self.result.setContentsMargins(0, 0, 0, 0)
        self.lay.addLayout(self.result)
        self.q_pid = QLineEdit()
        self.q_pid.setPlaceholderText("PID")
        self.q_pid.setFixedWidth(150)
        self.q_pid.setAccessibleName("PID to quarantine")
        self.q_pid.returnPressed.connect(lambda: self.q_btn.isEnabled() and self.quarantine_funnel())
        self.q_btn = button("Quarantine", "danger", ico="lock", on_click=self.quarantine_funnel)
        self.q_note = label("", "muted", wrap=True)
        card = Card(title="Quarantine a process", note="kill + block its binary in the application firewall (sudo)")
        card.set_body(hbox(self.q_pid, self.q_btn, self.q_note, "stretch", spacing=10))
        self.lay.addWidget(card)
        self.lay.addStretch(1)

    def retheme(self):
        if self.data is not None:
            self._render(self.data)

    def scan(self):
        if self.busy:
            return
        self.busy = True
        self.scan_btn.setEnabled(False)
        c = Card()
        c.set_body(Working("firewall, SIP, Gatekeeper, FileVault, connections, remote tools, processes, startup items, sharing, SSH..."),
                   Skeleton(5))
        self._set_result(c)
        self.job(self.reads.security, done=self._scanned, failed=self._failed)

    def _set_result(self, w: QWidget):
        clear_layout(self.result)
        self.result.addWidget(w)

    def _scanned(self, d: dict):
        self.busy = False
        self.scan_btn.setEnabled(True)
        self.data = d
        self._render(d)

    def _failed(self, msg: str):
        self.busy = False
        self.scan_btn.setEnabled(True)
        c = Card()
        c.set_body(err_box("scan failed: " + msg, self.scan))
        self._set_result(c)

    def _render(self, d: dict):
        c = Card()
        if d.get("error"):
            # the OS gate (off macOS the checks cannot run): shown, never a null/100 ring
            c.set_body(err_box(str(d["error"])))
            self.sec_note.setText("")
            self._set_result(c)
            return
        order = {"fail": 0, "warn": 1, "pass": 2}
        items = sorted(d.get("findings") or [], key=lambda f: order.get(f.get("status"), 3))
        fails = sum(1 for f in items if f.get("status") == "fail")
        warns = sum(1 for f in items if f.get("status") == "warn")
        score = d.get("score")
        head = hbox(Ring(score), vbox_(hbox(tabular(label(str(score), "big")), label("/100 security score", "muted"), "stretch", spacing=6),
                                       label(f"{fails} fail · {warns} warn · {len(items) - fails - warns} pass", "muted"), spacing=2),
                    "stretch", spacing=theme.SP[5])
        rows = [head]
        for f in items:
            it = QFrame()
            it.setProperty("role", "sec-item")
            it.setProperty("status", f.get("status"))
            it.setAttribute(Qt.WA_StyledBackground, True)
            il = QVBoxLayout(it)
            il.setContentsMargins(12, 9, 12, 9)
            il.setSpacing(4)
            st = str(f.get("status", ""))
            il.addWidget(hbox(badge(st, {"pass": "ok", "warn": "warn", "fail": "fail"}.get(st, "")),
                              label(str(f.get("name", "")), None), label(str(f.get("detail", "")), "muted", wrap=True), spacing=10))
            if st != "pass" and f.get("fix_hint"):
                il.addWidget(label(str(f["fix_hint"]), "text2", wrap=True))
            if f.get("items"):
                il.addWidget(label("\n".join("• " + str(x) for x in f["items"][:12]), "mono", wrap=True))
            rows.append(it)
        c.set_body(*rows)
        self.sec_note.setText("scanned " + _dt.datetime.now().strftime("%H:%M:%S"))
        self._set_result(c)

    # quarantine escalates (SIGTERM -> SIGKILL + firewall block): the same
    # guarded-acknowledgement funnel as kill, and the ack is ALWAYS required
    def quarantine_funnel(self):
        try:
            pid = int((self.q_pid.text() or "").strip())
        except ValueError:
            pid = 0
        if not pid:
            self.q_note.setText("enter a PID")
            return
        self.q_btn.setEnabled(False)
        self.job(self.api.process_guard, pid, done=self._q_guard, failed=lambda m: (self.q_btn.setEnabled(True), self.toast(m, "bad")))

    def _q_guard(self, g: dict):
        self.q_btn.setEnabled(True)
        if not g.get("ok"):
            self.q_note.setText(g.get("detail") or "refused")
            self.toast(g.get("detail") or "refused", "bad")
            return
        body = [kv_rows([("process", g.get("name")), ("pid", g.get("pid")), ("user", g.get("user")), ("command", g.get("cmd"))]),
                warn_box("Kills the process (SIGTERM, then SIGKILL if it survives 1s) and adds its binary to the application "
                         "firewall's block list (needs sudo; inbound only). The firewall step reports honestly if sudo needs a password.")]
        if g.get("guard"):
            body.append(warn_box(GUARD_TEXT.format(g["guard"])))
        ack = ("I understand: I am doing this by hand, on a guarded process -- it will be killed and its binary blocked."
               if g.get("guard") else "I understand this kills the process and blocks its binary.")
        self.shell.confirm(title=f"Quarantine {g.get('name')} (PID {g.get('pid')})?", body=body, ok="Quarantine", ack=ack,
                           on_ok=lambda acked: self._quarantine(g, acked))

    def _quarantine(self, g: dict, acked: bool):
        self.q_btn.setEnabled(False)
        # override is only what the engine needs on a GUARDED target: bool(guard) and acked
        self.job(self.api.quarantine, g.get("pid"), g.get("ct"), bool(g.get("guard")) and acked,
                 done=self._quarantined, failed=lambda m: (self.q_btn.setEnabled(True), self.toast(m, "bad")))

    def _quarantined(self, r: dict):
        self.q_btn.setEnabled(True)
        self.q_note.setText("\n".join(r.get("lines") or []) or r.get("detail") or "")
        self.toast(r.get("detail") or ("quarantined" if r.get("ok") else "refused"), "ember" if r.get("ok") else "bad", 6000)


# ── 5. Docker ────────────────────────────────────────────────────────────────

class Stat(QFrame):
    def __init__(self, k: str, v: str, tone: str = ""):
        super().__init__()
        self.setProperty("card", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(theme.SP[4], theme.SP[3], theme.SP[4], theme.SP[3])
        lay.setSpacing(2)
        lay.addWidget(label(k.upper(), "eyebrow"))
        lay.addWidget(tabular(label(v, "stat", tone=tone)))


class Docker(Section):
    key, title, ico = "docker", "Docker", "box"
    lead = "Containers, images and volumes. The only prune here is guarded: dangling images only, never volumes, never -a."
    TABS = (("containers", "Containers"), ("images", "Images"), ("volumes", "Volumes"), ("usage", "Disk usage"))

    def __init__(self, shell):
        super().__init__(shell)
        self.data = None
        self.busy = False
        self.refresh_btn = button("Refresh", "ghost", ico="refresh", on_click=self.load)
        self.prune_btn = button("Prune dangling images", "danger", ico="trash", on_click=self.prune_funnel, enabled=False)
        self.lay.addWidget(hbox(self.refresh_btn, "stretch", self.prune_btn, spacing=10))
        self.stats = QGridLayout()
        self.stats.setSpacing(theme.SP[3])
        self.lay.addLayout(self.stats)
        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        for key, name in self.TABS:
            self.tabs.addTab(name)
        self.tabs.currentChanged.connect(lambda _i: self.render())
        self.lay.addWidget(self.tabs)
        line = QFrame()
        line.setObjectName("tabline")
        self.lay.addWidget(line)
        self.panel = QVBoxLayout()
        self.panel.setContentsMargins(0, 0, 0, 0)
        self.lay.addLayout(self.panel, 1)
        self._set_panel(empty_state("box", "Docker", "Refresh to ask the daemon."))

    @property
    def tab(self) -> str:
        return self.TABS[max(0, self.tabs.currentIndex())][0]

    def enter(self):
        self.load()

    def retheme(self):
        self.render()

    def _set_panel(self, w: QWidget):
        clear_layout(self.panel)
        self.panel.addWidget(w)

    def load(self):
        if self.busy:
            return
        self.busy = True
        self.refresh_btn.setEnabled(False)
        if not self.data:
            self._set_panel(vbox_(Working("asking docker..."), Skeleton(4)))
        self.job(self.reads.docker, done=self._loaded, failed=self._failed)

    def _loaded(self, d: dict):
        self.busy = False
        self.refresh_btn.setEnabled(True)
        self.data = d
        self.render()

    def _failed(self, msg: str):
        self.busy = False
        self.refresh_btn.setEnabled(True)
        self._set_panel(err_box("docker: " + msg, self.load))

    def render(self):
        d = self.data
        clear_layout(self.stats)
        if not d:
            return
        if not d.get("available"):
            self.stats.addWidget(Stat("docker", "not running / not installed"), 0, 0, 1, 4)
            self._set_panel(empty_state("box", "Docker is not available", "Start Docker Desktop (or the daemon) and refresh."))
            self.prune_btn.setEnabled(False)
            return
        o = d.get("overview") or {}
        dangling = int(o.get("dangling_images") or 0)
        for i, (k, v, tn) in enumerate((("running", len(o.get("running") or []), ""), ("stopped", len(o.get("stopped") or []), ""),
                                        ("dangling images", dangling, "amber" if dangling else ""), ("volumes", o.get("volumes") or 0, ""))):
            self.stats.addWidget(Stat(k, str(v), tn), 0, i)
            self.stats.setColumnStretch(i, 1)
        self.prune_btn.setEnabled(bool(dangling))
        self.prune_btn.setText(f"Prune dangling images ({dangling})" if dangling else "Prune dangling images")
        tab = self.tab
        if tab == "containers":
            cols = [("name", "Name"), ("image", "Image"), ("status", "Status"), ("ports", "Ports"), ("size", "Size")]
            rows = d.get("containers") or []
        elif tab == "images":
            cols = [("repository", "Repository"), ("tag", "Tag"), ("id", "ID"), ("created", "Created"), ("size", "Size")]
            rows = d.get("images") or []
        elif tab == "volumes":
            cols = [("name", "Name"), ("driver", "Driver"), ("in_use", "In use")]
            rows = d.get("volumes") or []
        else:
            cols = [("type", "Type"), ("total", "Total"), ("active", "Active"), ("size", "Size"), ("reclaimable", "Reclaimable")]
            rows = o.get("disk_usage") or []
        if not rows:
            self._set_panel(empty_state("box", {"containers": "No containers", "images": "No images", "volumes": "No volumes"}.get(tab, "No usage data")))
            return
        t = make_table([c[1] for c in cols])
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, (k, _n) in enumerate(cols):
                v = r.get(k)
                if k == "in_use":
                    v = "yes" if v else "no"
                t.setItem(i, j, cell("" if v is None else v, tooltip=str(v)))
        t.resizeColumnsToContents()
        self._set_panel(t)

    def prune_funnel(self):
        n = int(((self.data or {}).get("overview") or {}).get("dangling_images") or 0)
        body = [label("Runs docker image prune -f -- dangling (untagged, unreferenced) images only.", "text2", wrap=True),
                info_line("Never -a, never containers, volumes, build cache or networks. Those stay in macmon docker --prune, "
                          "where you confirm them in the terminal.")]
        self.shell.confirm(title=f"Prune {n} dangling image{'' if n == 1 else 's'}?", body=body, ok="Prune",
                           on_ok=lambda _a: self._prune())

    def _prune(self):
        self.prune_btn.setEnabled(False)
        self.toast("pruning...", "warn", 1500)
        self.job(self.api.docker_prune_dangling, done=self._pruned, failed=lambda m: (self.toast(m, "bad"), self._reload()))

    def _pruned(self, r: dict):
        self.toast(r.get("detail") or ("pruned" if r.get("ok") else "refused"), "ok" if r.get("ok") else "bad", 6000)
        self._reload()

    def _reload(self):
        self.data = None
        self.load()


# ── 6. Disk ──────────────────────────────────────────────────────────────────

class Disk(Section):
    key, title, ico = "disk", "Disk", "disk"
    lead = "Where the space goes, and the biggest files. Reveal opens Finder; nothing here deletes."

    def __init__(self, shell):
        super().__init__(shell)
        self.path = QLineEdit("~")
        self.path.setProperty("role", "mono")
        self.path.setFixedWidth(220)
        self.path.setAccessibleName("Path to analyze")
        self.path.returnPressed.connect(lambda: self.go_btn.isEnabled() and self.load_usage())
        self.go_btn = button("Analyze", "ghost", "sm", ico="folder", on_click=self.load_usage)
        self.usage = Card(title="Usage", head_widget=hbox(self.path, self.go_btn, spacing=6))
        self.lay.addWidget(self.usage)
        self.min_box = QComboBox()
        for v, t in (("50MB", "> 50 MB"), ("200MB", "> 200 MB"), ("1GB", "> 1 GB")):
            self.min_box.addItem(t, v)
        self.big_btn = button("Find big files", "ghost", "sm", ico="search", on_click=self.load_big)
        self.big = Card(title="Big files", head_widget=hbox(self.min_box, self.big_btn, spacing=6))
        self.lay.addWidget(self.big, 1)
        self.lay.addStretch(0)
        self.idle()

    def idle(self):
        self.usage.set_body(empty_state("folder", "Analyze a folder",
                                        "Sizes every top-level entry of the path (hardlink-aware). A big home folder can take a minute or two "
                                        "-- click Analyze when you want it."))
        self.big.set_body(empty_state("search", "Find big files", "Scans the same path as Usage (skips Library, node_modules, .git, venvs)."))

    def _path(self) -> str:
        return (self.path.text() or "").strip() or "~"

    def load_usage(self):
        p = self._path()
        self.go_btn.setEnabled(False)
        self.usage.set_body(Working(f"sizing {p} ..."), Skeleton(6))
        self.job(self.reads.disk, p, done=self._usage, failed=lambda m: self._usage({"error": "disk: " + m}))

    def _usage(self, d: dict):
        self.go_btn.setEnabled(True)
        if d.get("error"):
            # includes the Full Disk Access hint on the "scanner busy" case (from disk_dict)
            self.usage.set_body(err_box(str(d["error"]), self.load_usage))
            return
        entries = d.get("entries") or []
        if not entries:
            self.usage.set_body(empty_state("folder", "Empty folder", d.get("path")))
            return
        top = max(max(int(e.get("size") or 0) for e in entries), 1)
        rows = [label(f"{d.get('path')} -- {d.get('total_label')} in {len(entries)} top-level entries", "muted")]
        for e in entries:
            size = int(e.get("size") or 0)
            tn = "ember" if size > 20 * 1024 ** 3 else "amber" if size > 5 * 1024 ** 3 else ""
            row = QFrame()
            row.setProperty("role", "row-dashed")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 6, 0, 6)
            rl.setSpacing(12)
            nm = label(str(e.get("name", "")))
            nm.setToolTip(str(e.get("path", "")))
            nm.setFixedWidth(200)
            rl.addWidget(nm)
            rl.addWidget(Bar(size / top * 100, tn, 7), 1)
            sz = tabular(label(e.get("size_label", ""), "text2"))
            sz.setFixedWidth(84)
            sz.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(sz)
            pc = tabular(label(f"{e.get('pct', 0)}%", "faint"))
            pc.setFixedWidth(50)
            pc.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            rl.addWidget(pc)
            rows.append(row)
        self.usage.set_body(*rows)

    def load_big(self):
        p = self._path()
        self.big_btn.setEnabled(False)
        self.big.set_body(Working(f"walking {p} for files {self.min_box.currentText()} ... (can take a while on a big home)"), Skeleton(5))
        self.job(self.reads.bigfiles, p, self.min_box.currentData() or "50MB", done=self._big,
                 failed=lambda m: self._big({"error": "bigfiles: " + m}))

    def _big(self, d: dict):
        self.big_btn.setEnabled(True)
        if d.get("error"):
            self.big.set_body(err_box(str(d["error"]), self.load_big))
            return
        files = d.get("files") or []
        if not files:
            self.big.set_body(calm_box("No file that big under " + str(d.get("path"))))
            return
        headers = ["Path", "Category", "Modified", "Size"] + (["Reveal"] if self.shell.is_mac else [])
        t = make_table(headers, numeric=(3,))
        t.setRowCount(len(files))
        home = os.path.expanduser("~")
        for i, f in enumerate(files):
            path = str(f.get("path", ""))
            shown = "~" + path[len(home):] if home and path.startswith(home) else path
            t.setItem(i, 0, cell(shown, tooltip=path))
            t.setCellWidget(i, 1, hbox(badge(str(f.get("category", ""))), "stretch", margins=(8, 2, 8, 2)))
            try:
                mod = _dt.datetime.fromtimestamp(float(f.get("mtime") or 0)).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                mod = "?"
            t.setItem(i, 2, cell(mod))
            t.setItem(i, 3, cell(f.get("size_label", ""), Qt.AlignRight))
            if self.shell.is_mac:
                t.setCellWidget(i, 4, hbox("stretch", button("Reveal", "ghost", "xs", ico="external",
                                                              on_click=lambda _c=False, pp=path: self.reveal(pp)),
                                           margins=(8, 2, 8, 2)))
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.big.set_body(t, label(f"{len(files)} files, {d.get('total_label')} total. Nothing here deletes -- use Clean, or Finder.", "muted"))

    def reveal(self, path: str):
        # read-only for the filesystem (spawns Finder); macOS only -- the button is hidden elsewhere
        self.job(self.api.reveal, path, done=lambda r: None if r.get("ok") else self.toast(r.get("detail") or "cannot reveal", "bad"),
                 failed=lambda m: self.toast(m, "bad"))


# ── 7. Sentinel ──────────────────────────────────────────────────────────────

AUTO_WARN = {
    "auto_purge": ("Level 1, non-destructive: on memory pressure the sentinel runs sudo -n purge (needs the passwordless rule from "
                   "macmon sentinel --setup-purge; skipped otherwise)."),
    "auto_unload_ollama": "Level 1, non-destructive: idle ollama models are unloaded when RAM is critical. They reload on demand.",
    "auto_trim_fleet": ("Level 2: when RAM is critical the sentinel CLOSES idle AI sessions (keeps fleet_keep, resumable). Only sessions "
                        "idle for idle_samples in a row."),
    "auto_reap_orphans": ("Level 2: SIGTERMs dev processes under PID 1 whose parent the sentinel WATCHED die, idle 5 min, with no live or "
                          "listening socket. Never a process with a live parent, never one in service."),
}
ACK_OPT_IN = "I opt in: the sentinel may act on its own under these rules."


def heading(slope, eta, crit, unit) -> str:
    try:
        slope = float(slope or 0)
    except (TypeError, ValueError):
        slope = 0.0
    if slope > 0 and eta is not None:
        return f"past the {crit}{unit} critical line" if eta <= 0 else f"-> {crit}{unit} in ~{max(1, round(eta))} min"
    return "stable" if abs(slope) < 1e-9 else "falling"


class Sentinel(Section):
    key, title, ico = "sentinel", "AegisForge Sentinel", "mark"
    lead = ("The proactive layer: a 60s sampler, five anticipatory detectors (notify-only), and opt-in auto-remediation that is OFF "
            "unless you turn it on here.")

    def __init__(self, shell):
        super().__init__(shell)
        self.data = None
        self.busy = False
        self.switches: dict = {}
        two = QHBoxLayout()
        two.setSpacing(theme.SP[4])
        self.status_card = Card(title="Sampler", note="LaunchAgent co.soclose.macmon.monitor")
        self.status_card.set_body(Skeleton(4))
        two.addWidget(self.status_card, 1)
        self.trends_card = Card(title="", note="")
        self.trends_card.title.setText(f'<span style="color:{theme.current()["action"]}">FORGE</span> TRENDS')
        self.trends_card.set_body(Skeleton(5))
        two.addWidget(self.trends_card, 1)
        self.lay.addLayout(two)
        self.det_card = Card(title="Detectors", note="always on, notify-only")
        self.det_card.set_body(Skeleton(5))
        self.lay.addWidget(self.det_card)
        self.auto_card = Card(title="Auto-remediation", note="default OFF -- each switch writes sentinel.conf",
                              head_widget=None)
        self.auto_card.lay.itemAt(0).layout().insertWidget(1, label("opt-in", "chip-amber"))
        self.auto_card.set_body(Skeleton(4))
        self.lay.addWidget(self.auto_card)
        self.thr_card = Card(title="Thresholds", note="read-only here; edit sentinel.conf or use the CLI")
        self.thr_card.set_body(Skeleton(4))
        self.lay.addWidget(self.thr_card)
        self.lay.addStretch(1)

    def enter(self):
        self.load()

    def retheme(self):
        self.trends_card.title.setText(f'<span style="color:{theme.current()["action"]}">FORGE</span> TRENDS')
        if self.data:
            self.render()

    def load(self):
        if self.busy:
            return
        self.busy = True
        self.job(self.reads.sentinel, done=self._loaded, failed=self._failed)

    def _loaded(self, d: dict):
        self.busy = False
        self.data = d
        self.render()

    def _failed(self, msg: str):
        self.busy = False
        self.status_card.set_body(err_box("sentinel: " + msg, self.load))

    def render(self):
        d = self.data or {}
        active = bool(d.get("sampler_active"))
        if active:
            self.toggle_btn = button("Pause", "warn", "sm", ico="pause", on_click=lambda: self.toggle_funnel("pause"))
        else:
            self.toggle_btn = button("Resume", "primary", "sm", ico="play", on_click=lambda: self.toggle_funnel("resume"))
        status = hbox(Lamp("on" if active else "off", 10), label("ACTIVE" if active else "STOPPED", "lamp-label"),
                      label("sampling every 60 s" if active else "paused -- no new samples, no alerts", "muted"), "stretch", self.toggle_btn,
                      spacing=12)
        weekly = d.get("weekly_active")
        self.status_card.set_body(status, kv_rows([
            ("weekly health agent", "macOS only" if weekly is None else "ACTIVE" if weekly else "STOPPED"),
            ("samples on disk", d.get("samples")), ("metrics", d.get("metrics_path")),
            ("passwordless purge", ("configured" if d.get("purge_ready") else "not configured (macmon sentinel --setup-purge)")
             if d.get("is_mac") else "macOS only")]))
        t = d.get("trends") or {}
        if not t.get("points"):
            self.trends_card.set_body(empty_state("clock", "No history yet", "The sampler needs a few minutes of samples before a slope means anything."))
            self.trends_card.set_note("")
        else:
            self.trends_card.set_note(f"slope over {t.get('points')} pts / {num(t.get('span_min'), 0)} min")
            s, r, ld, lk = t.get("swap") or {}, t.get("ram") or {}, t.get("load") or {}, t.get("leaks") or {}
            sg = float(s.get("slope") or 0)
            rg = float(r.get("slope") or 0)
            rows = [
                self._trend("SWAP", f"{num(s.get('gb'))} GB  {'+' if sg >= 0 else ''}{num(sg, 2)} GB/min", heading(sg, s.get("eta"), s.get("crit"), " GB"),
                            "medium" if sg > 0.05 and float(s.get("gb") or 0) >= 1 else "ok"),
                self._trend("RAM", f"{num(r.get('pct'), 0)}%  {'+' if rg >= 0 else ''}{num(rg)}%/min", heading(rg, r.get("eta"), r.get("crit"), "%"),
                            "medium" if rg > 0.3 and float(r.get("pct") or 0) >= 60 else "ok"),
                self._trend("LOAD", f"{num(ld.get('load1'))} / {ld.get('ncpu')} cores ({num(ld.get('ratio'))}x)",
                            f"{ld.get('sustained')} sample(s) above {num(ld.get('factor'), 0)}x" if ld.get("sustained") else "nominal",
                            "medium" if float(ld.get("ratio") or 0) > 1 else "ok"),
            ]
            hl = (t.get("swarm") or {}).get("headless")
            if hl:
                g = hl.get("growth") or 0
                rows.append(self._trend("SWARM", f"headless x{hl.get('count')} ({'+' if g >= 0 else ''}{g})", f"<- {t.get('spawn') or 'unknown spawner'}", "low"))
            else:
                rows.append(self._trend("SWARM", "none", "no runaway family", "ok"))
            auto = d.get("auto") or {}
            names = lk.get("names") or []
            rows.append(self._trend("LEAKS", f"{lk.get('leak') or 0} proven / {lk.get('p1') or 0} under PID 1",
                                    ("auto-reap ON (opt-in)" if auto.get("auto_reap_orphans") else "auto-reap OFF -- notify-only")
                                    + ("  " + ", ".join(names) if names else ""), "low" if lk.get("leak") else "ok"))
            self.trends_card.set_body(*rows)
        dets = []
        for x in d.get("detectors") or []:
            row = QFrame()
            row.setProperty("role", "row-dashed")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 7, 0, 7)
            rl.setSpacing(14)
            nm = label(str(x.get("name", "")))
            nm.setFixedWidth(150)
            rl.addWidget(nm, 0, Qt.AlignTop)
            rl.addWidget(label(str(x.get("what", "")), "muted", wrap=True), 1)
            dets.append(row)
        self.det_card.set_body(*dets)
        self.switches = {}
        sw_rows = []
        labels = d.get("auto_labels") or {}
        for key, on in (d.get("auto") or {}).items():
            sw = Switch(bool(on))
            sw.setAccessibleName(labels.get(key, key))
            sw.clicked.connect(lambda _c=False, k=key, w=sw: self.auto_toggle(k, w))
            self.switches[key] = sw
            row = QFrame()
            row.setProperty("role", "row-dashed")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 10, 0, 10)
            rl.setSpacing(14)
            rl.addWidget(sw)
            rl.addWidget(vbox_(label(labels.get(key, key), wrap=True), label(key, "note"), spacing=2), 1)
            rl.addWidget(label("ON" if on else "OFF", "chip-ember" if on else "chip-dim"))
            sw_rows.append(row)
        self.auto_card.set_body(*sw_rows)
        thr = d.get("thresholds") or {}
        grid = QWidget()
        gl = QGridLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(18)
        gl.setVerticalSpacing(6)
        items = list(thr.items())
        for i, (k, v) in enumerate(items):
            row = QFrame()
            row.setProperty("role", "row-dashed")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 5, 0, 5)
            rl.addWidget(label(k, "kv-k"))
            rl.addStretch(1)
            rl.addWidget(tabular(label("--" if v is None else str(v), "kv-v")))
            gl.addWidget(row, i // 3, i % 3)
        self.thr_card.set_body(grid)
        self.thr_card.set_note(f"{d.get('conf_path')} -- read-only here" if d.get("conf_path") else "")

    @staticmethod
    def _trend(lb: str, value: str, note: str, level: str) -> QWidget:
        row = QFrame()
        row.setProperty("role", "row-dashed")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 6, 0, 6)
        rl.setSpacing(12)
        rl.addWidget(label(lb, "trend-lb", lv=level), 0, Qt.AlignTop)
        rl.addWidget(vbox_(tabular(label(value, wrap=True)), label(note, "faint", wrap=True), spacing=2), 1)
        return row

    def toggle_funnel(self, verb: str):
        pause = verb == "pause"
        body = [label("Unloads the 60s sampler LaunchAgent. No new samples, no trend alerts, no auto-remediation until you resume."
                      if pause else "Reinstalls the 60s sampler LaunchAgent.", "text2", wrap=True)]
        self.shell.confirm(title="Pause the sentinel?" if pause else "Resume the sentinel?", body=body, ok="Pause" if pause else "Resume",
                           danger=pause, on_ok=lambda _a: self._toggle(verb))

    def _toggle(self, verb: str):
        self.toggle_btn.setEnabled(False)
        fn = self.api.sentinel_pause if verb == "pause" else self.api.sentinel_resume
        self.job(fn, done=lambda r: self._toggled(verb, r), failed=lambda m: (self.toast(m, "bad"), self.load()))

    def _toggled(self, verb: str, r: dict):
        self.toast(r.get("detail") or "done", ("warn" if verb == "pause" else "ok") if r.get("ok") else "bad")
        QTimer.singleShot(600, self.load)

    # the opt-in switches: an ON flip needs the acknowledged confirm; OFF is direct
    def auto_toggle(self, key: str, sw: Switch):
        want = sw.isChecked()          # Qt already flipped the visual state on click
        sw.setChecked(not want)        # revert: the engine's answer sets the real state
        if want:
            body = [warn_box(AUTO_WARN.get(key, key)),
                    info_line("Writes sentinel.conf. Turn it off any time here or with macmon sentinel --disable-auto.")]
            self.shell.confirm(title=f"Enable {key}?", body=body, ok="Enable", ack=ACK_OPT_IN,
                               on_ok=lambda acked: self._set_auto(key, True, sw) if acked else None)
        else:
            self._set_auto(key, False, sw)

    def _set_auto(self, key: str, on: bool, sw: Switch):
        sw.busy = True
        sw.setEnabled(False)
        sw.update()
        self.job(self.api.sentinel_set_auto, key, on, done=lambda r: self._auto_done(key, r), failed=lambda m: (self.toast(m, "bad"), self.load()))

    def _auto_done(self, key: str, r: dict):
        if not r.get("ok"):
            self.toast(r.get("detail") or "refused", "bad")
        else:
            self.toast(f"{key}: {'ON' if r.get('on') else 'OFF'}", "ember" if r.get("on") else "ok")
            if r.get("on") and key == "auto_purge" and r.get("purge_ready") is False:
                self.toast("auto_purge is ON but passwordless purge is not configured: run macmon sentinel --setup-purge "
                           "(until then it is skipped, notifications still fire).", "warn", 9000)
        self.load()


SECTIONS = (Overview, Processes, Clean, Security, Docker, Disk, Sentinel)
