"""AegisForge.app -- native macOS menu-bar app wrapping the macmon/Aegis engine.

Presented to the owner as AegisForge ("fleet health, forged"). This is the
menu-bar face of the SAME engine the CLI/sentinel use: it does NOT sample or
remediate on its own -- the `co.soclose.macmon.monitor` LaunchAgent keeps doing
the 60s sampling + notify-only detection. This app READS what the sampler wrote
(``~/.macmon/metrics.jsonl`` + the alerts log), runs the Aegis trend detectors
on those rows, and shows a glanceable status icon + a dropdown. Destructive
actions are explicit menu clicks only; nothing here ever auto-kills a workload.

Optional dependency: ``rumps`` (a thin wrapper over pyobjc/NSStatusBar). It is
NOT a core runtime dep of the macmon CLI -- install it with ``pip install
-e ".[app]"`` or via ``build_aegisforge.sh`` which freezes this into
``AegisForge.app`` with PyInstaller. Run in dev mode with
``python -m macmon_core.app_menubar``.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

try:  # the menu-bar layer is optional; the CLI never imports rumps
    import rumps
except ImportError:  # pragma: no cover - exercised via the packaged app only
    rumps = None

from . import aegis, sentinel

REFRESH_S = 30                       # menu refresh cadence (the sampler is 60s)
REPO = Path(__file__).resolve().parent.parent
# Menu-bar template glyph (monochrome, tinted by macOS). Rendered by
# build_aegisforge.sh from the brand mono mark; falls back to a text title.
MENUBAR_ICON = REPO / "assets" / "aegisforge-menubar.png"

# Worst-first ordering so the status icon reflects the most severe finding.
_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "ok": -1}
# Menu-bar title prefix per worst severity (icon is monochrome; the glyph before
# the value gives an at-a-glance read without relying on colour alone).
_SEV_MARK = {"critical": "◉", "high": "◉", "medium": "▲",
             "low": "▸", "info": "", "ok": ""}


def _cfg() -> dict:
    """Sentinel config (defaults + the user's sentinel.conf overlay if any)."""
    cfg = dict(sentinel.DEFAULTS)
    try:
        conf = sentinel.MACMON_DIR / "sentinel.conf"
        if conf.exists():
            import tomllib
            with open(conf, "rb") as fh:
                cfg.update(tomllib.load(fh) or {})
    except Exception:
        pass
    return cfg


def _finding_severity(f) -> str:
    return (getattr(f, "severity", None) or getattr(f, "sev", None) or "info").lower()


def _finding_text(f) -> str:
    return getattr(f, "msg", None) or getattr(f, "what", None) or str(f)


def gather() -> dict:
    """Read the sampler's output and derive the current picture. Never raises:
    on any error it returns a safe 'unknown' snapshot so the menu still renders."""
    out = {"vitals": {}, "findings": [], "alerts": [], "worst": "ok", "error": None}
    try:
        rows = sentinel._load_tail(16) or []
        now = time.time()
        cfg = _cfg()
        latest = rows[-1] if rows else {}
        out["vitals"] = {
            "cpu": latest.get("cpu"),
            "ram": latest.get("ram"),
            "swap_gb": latest.get("swap_gb"),
            "load1": latest.get("load1"),
            "disk_free_gb": latest.get("disk_free_gb"),
        }
        t = aegis.trends(rows, cfg, now)
        findings = []
        for det in (aegis.detect_swap_trend, aegis.detect_memory_trend, aegis.detect_load):
            f = det(t, cfg)
            if f:
                findings.append(f)
        try:
            findings.extend(aegis.detect_swarm(t, cfg) or [])
        except Exception:
            pass
        f = aegis.detect_leaks(t, cfg)
        if f:
            findings.append(f)
        out["findings"] = findings
        out["worst"] = worst_severity(findings)
        out["alerts"] = _recent_alerts(3)
    except Exception as e:  # never let a bad sample break the menu bar
        out["error"] = str(e)
    return out


def worst_severity(findings) -> str:
    worst = "ok"
    for f in findings:
        sev = _finding_severity(f)
        if _SEV_RANK.get(sev, 0) > _SEV_RANK.get(worst, -1):
            worst = sev
    return worst


def title_for(vitals: dict, worst: str) -> str:
    """Menu-bar title: quiet when healthy, surfaces the pressure when not."""
    mark = _SEV_MARK.get(worst, "")
    swap = vitals.get("swap_gb")
    if worst in ("critical", "high") and swap is not None:
        return f"{mark} {swap:.0f}G".strip()
    if worst in ("medium", "low") and swap is not None:
        return f"{mark}".strip()
    return ""  # healthy -> icon only


def _recent_alerts(n: int) -> list[str]:
    try:
        if not sentinel.ALERTS_LOG.exists():
            return []
        lines = sentinel.ALERTS_LOG.read_text(errors="replace").splitlines()
        return [ln.strip() for ln in lines[-n:] if ln.strip()]
    except Exception:
        return []


def menu_lines(snap: dict) -> list[str]:
    """The dropdown body as plain strings (also the unit-tested contract)."""
    v = snap.get("vitals", {})
    lines = []

    def _fmt(label, val, unit=""):
        return f"{label}: {val}{unit}" if val is not None else f"{label}: --"

    lines.append(_fmt("CPU", v.get("cpu"), "%"))
    lines.append(_fmt("RAM", v.get("ram"), "%"))
    sg = v.get("swap_gb")
    lines.append(f"Swap: {sg:.1f} GB" if sg is not None else "Swap: --")
    lines.append(_fmt("Load", v.get("load1")))
    df = v.get("disk_free_gb")
    lines.append(f"Disk free: {df:.0f} GB" if df is not None else "Disk free: --")

    findings = snap.get("findings", [])
    if findings:
        lines.append("--")
        for f in findings:
            lines.append(f"[{_finding_severity(f)}] {_finding_text(f)}")
    else:
        lines.append("--")
        lines.append("All clear -- forge cold")

    alerts = snap.get("alerts", [])
    if alerts:
        lines.append("--")
        lines.append("Recent alerts:")
        lines.extend(f"  {a}" for a in alerts)
    return lines


# ── actions (explicit clicks only; never auto-invoked) ───────────────────

def open_dashboard(_=None):
    """Open the live TUI dashboard in a Terminal window."""
    script = (f'tell application "Terminal" to do script '
              f'"cd {REPO} && python3 macmon.py dashboard"')
    try:
        subprocess.Popen(["osascript", "-e", 'tell application "Terminal" to activate',
                          "-e", script])
    except Exception:
        pass


def _run_cli(args: list[str]):
    """Run a macmon subcommand in the background (dev + frozen safe)."""
    def _worker():
        try:
            cmd = [sys.executable, str(REPO / "macmon.py"), *args]
            subprocess.run(cmd, cwd=str(REPO), timeout=600)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


def clean_caches(_=None):
    _run_cli(["clean", "--all", "-y"])


def purge_ram(_=None):
    # macmon purge shells out to `sudo purge`; macOS prompts for the password
    # in a GUI dialog when launched from the .app. Never silent, never forced.
    _run_cli(["purge"])


def pause_sentinel(_=None):
    _run_cli(["sentinel", "--pause"])


def resume_sentinel(_=None):
    _run_cli(["sentinel", "--resume"])


# ── the rumps app ────────────────────────────────────────────────────────

def _icon_or_none():
    return str(MENUBAR_ICON) if MENUBAR_ICON.exists() else None


if rumps is not None:  # pragma: no cover - requires a GUI session

    class AegisForgeApp(rumps.App):
        def __init__(self):
            icon = _icon_or_none()
            super().__init__(
                aegis.BRAND, title=None if icon else "AF",
                icon=icon, template=True, quit_button=None,
            )
            self.menu = [
                rumps.MenuItem("Open live dashboard", callback=open_dashboard),
                None,
                rumps.MenuItem("Clean caches", callback=clean_caches),
                rumps.MenuItem("Purge RAM (sudo)", callback=purge_ram),
                None,
                rumps.MenuItem("Pause sentinel", callback=pause_sentinel),
                rumps.MenuItem("Resume sentinel", callback=resume_sentinel),
                None,
                rumps.MenuItem(f"Quit {aegis.BRAND}", callback=rumps.quit_application),
            ]
            self._refresh(None)

        @rumps.timer(REFRESH_S)
        def _refresh(self, _):
            snap = gather()
            self.title = title_for(snap.get("vitals", {}), snap.get("worst", "ok"))
            # Rebuild the live status section at the top of the menu.
            for key in list(self.menu.keys()):
                if key.startswith("─") or key.startswith("live:"):
                    del self.menu[key]
            live = menu_lines(snap)
            for i, ln in enumerate(live):
                item = None if ln == "--" else rumps.MenuItem(f"live:{i} {ln}"[:120])
                self.menu.insert_before("Open live dashboard",
                                        item or rumps.separator)


def main():
    if rumps is None:
        print("AegisForge.app needs the menu-bar extra: pip install -e \".[app]\" "
              "(rumps). The CLI works without it: `macmon sentinel`.", file=sys.stderr)
        return 1
    AegisForgeApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
