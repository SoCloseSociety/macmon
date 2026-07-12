#!/usr/bin/env python3
"""Render real macmon output to SVG "screenshots" for the README.

Uses rich's record+save_svg. Each capture patches the target module's
`console` global with a recording Console so the real render is captured.
Run:  .venv/bin/python scripts/make_screenshots.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

from rich.console import Console
from rich.terminal_theme import MONOKAI

WIDTH = 100


def cap(name, title, render_fn):
    rec = Console(record=True, width=WIDTH, file=open("/dev/null", "w"))
    render_fn(rec)
    out = ASSETS / f"{name}.svg"
    rec.save_svg(str(out), title=title, theme=MONOKAI)
    print(f"  wrote {out.relative_to(ROOT)}")


def cap_sentinel(rec):
    import modules.sentinel as m
    m.console = rec
    rec.print(m._snapshot_panel())


def cap_health(rec):
    import modules.health as m
    m.console = rec
    m.run_health(fix=False, report=False, json_out=False)


def cap_ps(rec):
    import modules.processes as m
    m.console = rec
    # Filter to a compact category so the capture stays readable
    m.list_processes(filter_cat="llm", sort_by="cpu", tree=False, json_out=False)


def cap_clean(rec):
    import modules.cleaner as m
    m.console = rec
    m.run_cleaner(scan=True, json_out=False)


def cap_security(rec):
    import modules.security as m
    m.console = rec
    m.run_security(scan=True)


if __name__ == "__main__":
    print("Rendering screenshots...")
    for name, title, fn in [
        ("sentinel", "macmon sentinel", cap_sentinel),
        ("health", "macmon health", cap_health),
        ("ps", "macmon ps", cap_ps),
        ("clean", "macmon clean --scan", cap_clean),
        ("security", "macmon security --scan", cap_security),
    ]:
        try:
            cap(name, title, fn)
        except Exception as e:
            print(f"  SKIP {name}: {e.__class__.__name__}: {e}")
    print("Done.")
