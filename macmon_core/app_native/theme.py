"""AegisForge native app -- the design system as Qt tokens + one stylesheet.

One palette, three faces: the Rich console (``aegis.AEGIS_THEME`` / ``C[]``),
the web page (``assets/webui/app.css`` ``--af-*``) and this native app all
read the SAME brand values. ``BRAND`` below starts from ``aegis.C`` verbatim
(the single source of brand colour) and adds the kit's extra ramp tokens the
stylesheet names; ``DARK`` / ``LIGHT`` are the semantic layers (bg / surface
/ border / text / muted / accent mint / action ember / severity roles) that
``app.css`` defines, with the same hex values, so a theme swap never touches
a component rule. ``build_qss(sem)`` turns a semantic layer into the Qt
stylesheet; widgets select their look through object roles (dynamic
properties: ``role``, ``kind``, ``tone``, ``sev``, ``card`` ...), never a
hard-coded colour.

Rule of thumb from the kit: ground 70 / mint 20 / ember 10. Mint is structure,
OK and the safe default; ember marks the forge at work (destructive-capable
actions, HIGH findings, the opt-in switches). No em dashes anywhere ("--").
"""
from __future__ import annotations

from .. import aegis

# ── 1. Brand tokens (aegis.C verbatim + the kit ramp app.css names) ─────────

BRAND = {
    # from macmon_core/aegis.py C[] -- the console palette, verbatim
    "ground": aegis.C["ground"],        # #070A0F  page background
    "surface": aegis.C["surface"],      # #10171F  panel background
    "t1": aegis.C["text"],              # #EEF3F8
    "t3": aegis.C["dim"],               # #8393A6
    "mint": aegis.C["mint"],            # #34E5A0  ok / primary
    "ember": aegis.C["ember"],          # #FF7A1A  action / high
    "amber": aegis.C["amber"],          # #FBBF24  warn / medium
    "sky": aegis.C["sky"],              # #7DD3FC  low
    "bad": aegis.C["critical"],         # #FB7185  critical
    "ok": aegis.C["ok"],                # #34D399  ok-green
    # the kit ramp (app.css --af-*)
    "ground_2": "#0B1017", "surface_2": "#161F2A", "surface_3": "#1C2836",
    "line": "#1B2634", "line_2": "#26344A",
    "t2": "#B3C0CF", "t4": "#5A697D", "lunar": "#97A6B2",
    "teal": "#5EEAD4", "acc_ink": "#04221D",
    "hot": "#FFF1C6", "cool": "#C2410C", "ember_ink": "#1A0A00", "pop": "#C084FC",
}

# ── 2. Semantic layers (app.css :root / :root[data-theme="light"]) ──────────

DARK = {
    "name": "dark",
    "bg": BRAND["ground"], "bg_2": BRAND["ground_2"],
    "surface": BRAND["surface"], "surface_2": BRAND["surface_2"], "elev": BRAND["surface_3"],
    "border": BRAND["line"], "border_strong": BRAND["line_2"],
    "text": BRAND["t1"], "text_2": BRAND["t2"], "muted": BRAND["t3"], "faint": BRAND["t4"],
    "scrim": "rgba(7, 10, 15, 184)",
    "accent": BRAND["mint"], "accent_hover": "#4CEFB0", "accent_ink": BRAND["acc_ink"],
    "action": BRAND["ember"], "action_hover": "#FF8E3D", "action_ink": BRAND["ember_ink"],
    "sky": BRAND["sky"], "amber": BRAND["amber"],
    "sev_ok": BRAND["ok"], "sev_info": BRAND["t3"], "sev_low": BRAND["sky"],
    "sev_medium": BRAND["amber"], "sev_high": BRAND["ember"], "sev_critical": BRAND["bad"],
    "tint_mint": "rgba(52, 229, 160, 31)", "tint_mint_line": "rgba(52, 229, 160, 82)",
    "tint_ember": "rgba(255, 122, 26, 36)", "tint_ember_line": "rgba(255, 122, 26, 107)",
    "tint_amber": "rgba(251, 191, 36, 26)", "tint_amber_line": "rgba(251, 191, 36, 92)",
    "tint_sky": "rgba(125, 211, 252, 26)", "tint_sky_line": "rgba(125, 211, 252, 82)",
    "tint_bad": "rgba(251, 113, 133, 28)", "tint_bad_line": "rgba(251, 113, 133, 92)",
    "tint_ok": "rgba(52, 211, 153, 31)", "tint_ok_line": "rgba(52, 211, 153, 82)",
}

LIGHT = {
    "name": "light",
    "bg": "#F2F5F9", "bg_2": "#E9EEF4",
    "surface": "#FFFFFF", "surface_2": "#F4F7FA", "elev": "#E9EEF4",
    "border": "#E2E8EF", "border_strong": "#D2DBE5",
    "text": "#0E1620", "text_2": "#3B4A5B", "muted": "#63738A", "faint": "#8D9CAD",
    "scrim": "rgba(14, 22, 32, 115)",
    "accent": "#0EA5A0", "accent_hover": "#0B8A85", "accent_ink": "#FFFFFF",
    "action": "#C2410C", "action_hover": "#A83607", "action_ink": "#FFFFFF",
    "sky": "#0284C7", "amber": "#B45309",
    "sev_ok": "#0F9F6E", "sev_info": "#63738A", "sev_low": "#0284C7",
    "sev_medium": "#B45309", "sev_high": "#C2410C", "sev_critical": "#DC2647",
    "tint_mint": "rgba(14, 165, 160, 26)", "tint_mint_line": "rgba(14, 165, 160, 97)",
    "tint_ember": "rgba(194, 65, 12, 26)", "tint_ember_line": "rgba(194, 65, 12, 102)",
    "tint_amber": "rgba(180, 83, 9, 26)", "tint_amber_line": "rgba(180, 83, 9, 92)",
    "tint_sky": "rgba(2, 132, 199, 23)", "tint_sky_line": "rgba(2, 132, 199, 87)",
    "tint_bad": "rgba(220, 38, 71, 23)", "tint_bad_line": "rgba(220, 38, 71, 92)",
    "tint_ok": "rgba(15, 159, 110, 26)", "tint_ok_line": "rgba(15, 159, 110, 87)",
}

# ── 3. Type scale, spacing, radii (app.css --fs-* / --sp-* / --r-*) ─────────

FS = {"xs": 11, "sm": 12, "md": 13, "lg": 15, "xl": 18, "2xl": 22, "3xl": 28, "display": 34}
SP = {1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40}
RADIUS = {"xs": 4, "sm": 6, "md": 10, "lg": 14, "full": 999}
FONT_SANS = '"IBM Plex Sans", "SF Pro Text", "Segoe UI", "Helvetica Neue", Arial, sans-serif'
FONT_MONO = '"IBM Plex Mono", "SF Mono", Menlo, Consolas, "Roboto Mono", monospace'
# motion: the web uses 120 / 180 / 320 ms; the native app keeps animation
# subtle (toast fade, ring/bar transitions) and never blocks on it
DUR = {"fast": 120, "base": 180, "slow": 320}

# severity role -> semantic key (SOC schema: critical | high | medium | low | info | ok)
SEVERITY_KEYS = {"ok": "sev_ok", "info": "sev_info", "low": "sev_low", "medium": "sev_medium",
                 "high": "sev_high", "critical": "sev_critical"}
# vital / bar "tone" -> semantic key (web: tone-amber / tone-ember / tone-bad)
TONE_KEYS = {"": "accent", "ok": "accent", "amber": "sev_medium", "ember": "sev_high", "bad": "sev_critical"}

_current = dict(DARK)


def current() -> dict:
    """The semantic layer in force (painted widgets read their colours here)."""
    return _current


def set_current(sem: dict) -> None:
    _current.clear()
    _current.update(sem)


def sev_color(sev: str) -> str:
    return _current[SEVERITY_KEYS.get(str(sev).lower(), "sev_info")]


def tone_color(tone: str) -> str:
    return _current[TONE_KEYS.get(tone or "", "accent")]


def build_qss(s: dict) -> str:
    """The whole stylesheet for one semantic layer. Components only reference
    the semantic roles (never a raw brand hex), mirroring app.css."""
    r = RADIUS
    return f"""
    * {{ font-family: {FONT_SANS}; font-size: {FS['md']}px; }}
    QMainWindow, QDialog, QWidget#root {{ background: {s['bg']}; color: {s['text']}; }}
    QWidget {{ color: {s['text']}; }}
    QToolTip {{ background: {s['elev']}; color: {s['text']}; border: 1px solid {s['border_strong']}; padding: 4px 7px; }}

    /* layout: sidebar, top bar, content */
    QFrame#side {{ background: {s['bg_2']}; border-right: 1px solid {s['border']}; }}
    QFrame#top {{ background: {s['bg_2']}; border-bottom: 1px solid {s['border']}; }}
    QScrollArea#content {{ background: {s['bg']}; border: 0; }}
    QScrollArea#content > QWidget > QWidget {{ background: {s['bg']}; }}
    QWidget#view {{ background: transparent; }}
    QFrame#brandLine {{ background: {s['border']}; max-height: 1px; min-height: 1px; }}
    QLabel#brandName {{ font-size: {FS['lg']}px; font-weight: 600; }}
    QLabel#brandTag {{ font-family: {FONT_MONO}; font-size: 10px; color: {s['muted']}; letter-spacing: 1px; }}
    QLabel#engine {{ font-family: {FONT_MONO}; font-size: 10px; color: {s['faint']}; }}

    /* nav */
    QPushButton[nav="true"] {{ text-align: left; background: transparent; border: 0; border-left: 2px solid transparent;
        border-radius: {r['sm']}px; padding: 7px 10px; color: {s['text_2']}; font-weight: 500; }}
    QPushButton[nav="true"]:hover {{ background: {s['surface']}; color: {s['text']}; }}
    QPushButton[nav="true"][active="true"] {{ background: {s['surface_2']}; color: {s['text']}; border-left: 2px solid {s['accent']}; }}
    QPushButton[nav="true"]:focus {{ outline: none; border: 1px solid {s['accent']}; }}

    /* theme segment */
    QFrame#seg {{ background: {s['surface']}; border: 1px solid {s['border']}; border-radius: {r['sm']}px; }}
    QPushButton[seg="true"] {{ background: transparent; border: 0; border-radius: 4px; color: {s['muted']}; font-size: 10px; padding: 3px 7px; }}
    QPushButton[seg="true"]:hover {{ color: {s['text']}; }}
    QPushButton[seg="true"][active="true"] {{ background: {s['elev']}; color: {s['text']}; }}

    /* top bar pills + chips */
    QLabel[role="worst"] {{ font-size: {FS['xs']}px; font-weight: 600; padding: 4px 10px; border-radius: {r['full']}px;
        border: 1px solid {s['border_strong']}; background: {s['surface']}; }}
    QLabel[role="worst"][sev="ok"] {{ color: {s['sev_ok']}; }}
    QLabel[role="worst"][sev="info"] {{ color: {s['sev_info']}; }}
    QLabel[role="worst"][sev="low"] {{ color: {s['sev_low']}; }}
    QLabel[role="worst"][sev="medium"] {{ color: {s['sev_medium']}; border-color: {s['tint_amber_line']}; background: {s['tint_amber']}; }}
    QLabel[role="worst"][sev="high"] {{ color: {s['sev_high']}; border-color: {s['tint_ember_line']}; background: {s['tint_ember']}; }}
    QLabel[role="worst"][sev="critical"] {{ color: {s['sev_critical']}; border-color: {s['tint_bad_line']}; background: {s['tint_bad']}; }}
    QLabel[role="chip"] {{ font-size: {FS['xs']}px; padding: 3px 9px; border-radius: {r['full']}px; background: {s['surface']};
        border: 1px solid {s['border']}; color: {s['muted']}; }}
    QLabel[role="chip"][tone="amber"] {{ color: {s['sev_medium']}; }}
    QLabel[role="chip"][tone="ember"] {{ color: {s['sev_high']}; }}
    QLabel[role="chip"][tone="bad"] {{ color: {s['sev_critical']}; }}
    QLabel[role="chip-amber"] {{ color: {s['amber']}; border: 1px solid {s['tint_amber_line']}; background: {s['tint_amber']};
        font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: {r['full']}px; }}
    QLabel[role="chip-ember"] {{ color: {s['action']}; border: 1px solid {s['tint_ember_line']}; background: {s['tint_ember']};
        font-size: {FS['xs']}px; font-weight: 600; padding: 2px 8px; border-radius: {r['full']}px; }}
    QLabel[role="chip-dim"] {{ color: {s['muted']}; border: 1px solid {s['border']}; background: {s['surface']};
        font-size: {FS['xs']}px; padding: 2px 8px; border-radius: {r['full']}px; }}
    QLabel[role="age"] {{ font-size: {FS['xs']}px; color: {s['muted']}; }}
    QLabel[role="age"][stale="true"] {{ color: {s['amber']}; }}

    /* text roles */
    QLabel[role="h1"] {{ font-size: {FS['2xl']}px; font-weight: 600; }}
    QLabel[role="h3"] {{ font-size: {FS['xl']}px; font-weight: 600; }}
    QLabel[role="lead"] {{ color: {s['muted']}; }}
    QLabel[role="eyebrow"] {{ font-size: {FS['xs']}px; font-weight: 600; color: {s['muted']}; letter-spacing: 1px; }}
    QLabel[role="muted"] {{ color: {s['muted']}; }}
    QLabel[role="faint"] {{ color: {s['faint']}; font-size: {FS['xs']}px; }}
    QLabel[role="text2"] {{ color: {s['text_2']}; }}
    QLabel[role="mono"] {{ font-family: {FONT_MONO}; font-size: 11px; color: {s['text_2']}; }}
    QLabel[role="note"] {{ font-family: {FONT_MONO}; font-size: {FS['xs']}px; color: {s['faint']}; }}
    QLabel[role="ember"] {{ color: {s['action']}; font-weight: 600; }}
    QLabel[role="value"] {{ font-size: {FS['3xl']}px; font-weight: 600; }}
    QLabel[role="value"][tone="amber"] {{ color: {s['sev_medium']}; }}
    QLabel[role="value"][tone="ember"] {{ color: {s['sev_high']}; }}
    QLabel[role="value"][tone="bad"] {{ color: {s['sev_critical']}; }}
    QLabel[role="unit"] {{ font-size: {FS['sm']}px; color: {s['muted']}; font-weight: 500; }}
    QLabel[role="stat"] {{ font-size: {FS['2xl']}px; font-weight: 600; }}
    QLabel[role="stat"][tone="amber"] {{ color: {s['sev_medium']}; }}
    QLabel[role="big"] {{ font-size: {FS['display']}px; font-weight: 600; }}
    QLabel[role="result-ok"] {{ color: {s['sev_ok']}; font-size: {FS['xl']}px; font-weight: 600; }}
    QLabel[role="warn-line"] {{ color: {s['amber']}; font-size: {FS['sm']}px; }}
    QLabel[role="lamp-label"] {{ font-weight: 600; letter-spacing: 1px; }}
    QLabel[role="kv-k"] {{ color: {s['muted']}; font-family: {FONT_MONO}; font-size: 11px; }}
    QLabel[role="kv-v"] {{ color: {s['text']}; }}
    QLabel[role="lvl"] {{ font-size: 10px; font-weight: 700; letter-spacing: 1px; min-width: 58px; }}
    QLabel[role="lvl"][sev="critical"] {{ color: {s['sev_critical']}; }}
    QLabel[role="lvl"][sev="high"] {{ color: {s['sev_high']}; }}
    QLabel[role="lvl"][sev="medium"] {{ color: {s['sev_medium']}; }}
    QLabel[role="lvl"][sev="low"] {{ color: {s['sev_low']}; }}
    QLabel[role="lvl"][sev="info"], QLabel[role="lvl"][sev="ok"] {{ color: {s['sev_info']}; }}
    QLabel[role="lvl"][sev="engine"] {{ color: {s['sev_medium']}; }}
    QLabel[role="trend-lb"] {{ font-size: {FS['xs']}px; font-weight: 700; letter-spacing: 1px; color: {s['text_2']}; min-width: 60px; }}
    QLabel[role="trend-lb"][lv="ok"] {{ color: {s['sev_ok']}; }}
    QLabel[role="trend-lb"][lv="low"] {{ color: {s['sev_low']}; }}
    QLabel[role="trend-lb"][lv="medium"] {{ color: {s['sev_medium']}; }}
    QLabel[role="trend-lb"][lv="high"] {{ color: {s['sev_high']}; }}
    QLabel[role="trend-lb"][lv="critical"] {{ color: {s['sev_critical']}; }}
    QLabel[role="st"] {{ font-size: 10px; font-weight: 700; letter-spacing: 1px; min-width: 36px; }}
    QLabel[role="st"][status="pass"] {{ color: {s['sev_ok']}; }}
    QLabel[role="st"][status="warn"] {{ color: {s['sev_medium']}; }}
    QLabel[role="st"][status="fail"] {{ color: {s['sev_critical']}; }}
    QLabel[role="hot"] {{ color: {s['sev_high']}; }}
    QLabel[role="warm"] {{ color: {s['sev_medium']}; }}

    /* badges */
    QLabel[role="badge"] {{ font-size: 10px; font-weight: 700; letter-spacing: 1px; padding: 1px 7px; border-radius: {r['xs']}px;
        background: {s['elev']}; color: {s['muted']}; }}
    QLabel[role="badge"][kind="ok"] {{ color: {s['sev_ok']}; background: {s['tint_ok']}; }}
    QLabel[role="badge"][kind="warn"] {{ color: {s['sev_medium']}; background: {s['tint_amber']}; }}
    QLabel[role="badge"][kind="fail"] {{ color: {s['sev_critical']}; background: {s['tint_bad']}; }}
    QLabel[role="badge"][kind="ember"] {{ color: {s['sev_high']}; background: {s['tint_ember']}; }}
    QLabel[role="badge"][kind="sky"] {{ color: {s['sev_low']}; background: {s['tint_sky']}; }}
    QLabel[role="badge"][kind="protected"] {{ color: {s['sky']}; background: {s['tint_sky']}; border: 1px solid {s['tint_sky_line']}; }}

    /* cards + boxes */
    QFrame[card="true"] {{ background: {s['surface']}; border: 1px solid {s['border']}; border-radius: {r['md']}px; }}
    QFrame[card="true"][stale="true"] {{ border-color: {s['tint_amber_line']}; }}
    QFrame[role="finding"] {{ background: {s['bg_2']}; border: 1px solid {s['border']}; border-left: 3px solid {s['faint']}; border-radius: {r['sm']}px; }}
    QFrame[role="finding"][sev="critical"] {{ border-left-color: {s['sev_critical']}; }}
    QFrame[role="finding"][sev="high"] {{ border-left-color: {s['sev_high']}; }}
    QFrame[role="finding"][sev="medium"], QFrame[role="finding"][sev="engine"] {{ border-left-color: {s['sev_medium']}; }}
    QFrame[role="finding"][sev="low"] {{ border-left-color: {s['sev_low']}; }}
    QFrame[role="finding"][sev="ok"] {{ border-left-color: {s['sev_ok']}; }}
    QFrame[role="sec-item"] {{ background: {s['bg_2']}; border: 1px solid {s['border']}; border-left: 3px solid {s['sev_ok']}; border-radius: {r['sm']}px; }}
    QFrame[role="sec-item"][status="warn"] {{ border-left-color: {s['sev_medium']}; }}
    QFrame[role="sec-item"][status="fail"] {{ border-left-color: {s['sev_critical']}; }}
    QFrame[role="calm"] {{ background: {s['tint_mint']}; border: 1px solid {s['tint_mint_line']}; border-radius: {r['sm']}px; }}
    QFrame[role="calm"] QLabel {{ color: {s['accent']}; }}
    QFrame[role="errbox"] {{ background: {s['tint_bad']}; border: 1px solid {s['tint_bad_line']}; border-radius: {r['sm']}px; }}
    QFrame[role="errbox"] QLabel {{ color: {s['text']}; }}
    QFrame[role="warnbox"] {{ background: {s['tint_amber']}; border: 1px solid {s['tint_amber_line']}; border-radius: {r['sm']}px; }}
    QFrame[role="warnbox"] QLabel {{ color: {s['amber']}; font-size: {FS['sm']}px; }}
    QFrame[role="infoline"] QLabel {{ color: {s['sky']}; font-size: {FS['sm']}px; }}
    QFrame[role="ack"] {{ background: {s['tint_ember']}; border: 1px solid {s['tint_ember_line']}; border-radius: {r['sm']}px; }}
    QFrame[role="ack"] QCheckBox {{ color: {s['text']}; font-size: {FS['sm']}px; }}
    QFrame[role="notes"] {{ background: {s['bg_2']}; border: 1px solid {s['border']}; border-radius: {r['sm']}px; }}
    QFrame[role="notes"] QLabel {{ font-family: {FONT_MONO}; font-size: 11px; color: {s['muted']}; }}
    QFrame[role="alert-line"] {{ border-bottom: 1px dashed {s['border']}; }}
    QFrame[role="alert-line"] QLabel {{ font-family: {FONT_MONO}; font-size: {FS['sm']}px; color: {s['text_2']}; }}
    QFrame[role="row-dashed"] {{ border-bottom: 1px dashed {s['border']}; }}
    QFrame[role="pick"] {{ background: {s['bg_2']}; border: 1px solid {s['border']}; border-radius: {r['sm']}px; }}
    QFrame[role="pick"]:hover {{ border-color: {s['border_strong']}; background: {s['surface_2']}; }}
    QFrame[role="pick"][checked="true"] {{ border-color: {s['tint_mint_line']}; }}
    QFrame[role="summary"] {{ border-top: 1px solid {s['border']}; }}
    QFrame[role="empty"] QLabel {{ color: {s['muted']}; }}
    QFrame[role="empty"] QLabel[role="empty-title"] {{ color: {s['text_2']}; font-weight: 600; }}
    QFrame[role="vh-ico"] {{ background: {s['surface']}; border: 1px solid {s['border']}; border-radius: {r['md']}px; }}
    QFrame[role="toast"] {{ background: {s['surface']}; border: 1px solid {s['border_strong']}; border-left: 3px solid {s['accent']}; border-radius: {r['sm']}px; }}
    QFrame[role="toast"][kind="bad"] {{ border-left-color: {s['sev_critical']}; }}
    QFrame[role="toast"][kind="warn"] {{ border-left-color: {s['sev_medium']}; }}
    QFrame[role="toast"][kind="ember"] {{ border-left-color: {s['sev_high']}; }}
    QFrame[role="toast"][kind="info"] {{ border-left-color: {s['sky']}; }}
    QFrame[role="toast"] QLabel {{ font-size: {FS['sm']}px; }}
    QFrame[role="modal"] {{ background: {s['surface']}; border: 1px solid {s['border_strong']}; border-radius: {r['lg']}px; }}
    QLabel[role="modal-title"] {{ font-size: {FS['lg']}px; font-weight: 600; }}

    /* stepper */
    QLabel[role="step"] {{ font-size: {FS['sm']}px; font-weight: 500; color: {s['faint']}; padding: 5px 12px; border-radius: {r['full']}px;
        border: 1px solid {s['border']}; background: {s['bg_2']}; }}
    QLabel[role="step"][state="current"] {{ color: {s['text']}; border-color: {s['tint_mint_line']}; background: {s['surface']}; }}
    QLabel[role="step"][state="done"] {{ color: {s['text_2']}; border-color: {s['tint_ok_line']}; }}
    QLabel[role="step-link"] {{ color: {s['border_strong']}; }}
    QLabel[role="step-link"][done="true"] {{ color: {s['sev_ok']}; }}

    /* controls */
    QPushButton {{ background: {s['surface_2']}; border: 1px solid {s['border_strong']}; color: {s['text']}; padding: 0 13px; min-height: 32px;
        border-radius: {r['sm']}px; font-weight: 500; }}
    QPushButton:hover {{ background: {s['elev']}; border-color: {s['faint']}; }}
    QPushButton:disabled {{ color: {s['faint']}; border-color: {s['border']}; background: {s['surface']}; }}
    QPushButton:focus {{ outline: none; border: 1px solid {s['accent']}; }}
    QPushButton[kind="primary"] {{ background: {s['accent']}; border-color: {s['accent']}; color: {s['accent_ink']}; font-weight: 600; }}
    QPushButton[kind="primary"]:hover {{ background: {s['accent_hover']}; border-color: {s['accent_hover']}; }}
    QPushButton[kind="primary"]:disabled {{ background: {s['surface_2']}; border-color: {s['border']}; color: {s['faint']}; }}
    QPushButton[kind="danger"] {{ background: {s['tint_ember']}; border-color: {s['tint_ember_line']}; color: {s['action']}; font-weight: 600; }}
    QPushButton[kind="danger"]:hover {{ background: {s['action']}; border-color: {s['action']}; color: {s['action_ink']}; }}
    QPushButton[kind="danger"]:disabled {{ background: {s['surface']}; border-color: {s['border']}; color: {s['faint']}; }}
    QPushButton[kind="danger-solid"] {{ background: {s['action']}; border-color: {s['action']}; color: {s['action_ink']}; font-weight: 600; }}
    QPushButton[kind="danger-solid"]:hover {{ background: {s['action_hover']}; border-color: {s['action_hover']}; }}
    QPushButton[kind="danger-solid"]:disabled {{ background: {s['surface']}; border-color: {s['border']}; color: {s['faint']}; }}
    QPushButton[kind="warn"] {{ background: {s['tint_amber']}; border-color: {s['tint_amber_line']}; color: {s['amber']}; }}
    QPushButton[kind="warn"]:hover {{ background: {s['amber']}; border-color: {s['amber']}; color: {BRAND['ember_ink']}; }}
    QPushButton[kind="ghost"] {{ background: transparent; }}
    QPushButton[kind="ghost"]:hover {{ background: {s['elev']}; }}
    QPushButton[size="sm"] {{ min-height: 26px; padding: 0 10px; font-size: {FS['sm']}px; }}
    QPushButton[size="xs"] {{ min-height: 22px; padding: 0 8px; font-size: {FS['xs']}px; border-radius: 5px; }}
    QPushButton[role="toast-close"] {{ background: transparent; border: 0; color: {s['faint']}; min-height: 18px; padding: 0 4px; }}
    QLineEdit, QComboBox {{ background: {s['bg_2']}; border: 1px solid {s['border_strong']}; border-radius: {r['sm']}px; padding: 5px 10px;
        color: {s['text']}; min-height: 22px; selection-background-color: {s['tint_mint_line']}; }}
    QLineEdit:hover, QComboBox:hover {{ border-color: {s['faint']}; }}
    QLineEdit:focus, QComboBox:focus {{ border: 1px solid {s['accent']}; }}
    QLineEdit[role="mono"] {{ font-family: {FONT_MONO}; }}
    QComboBox::drop-down {{ border: 0; width: 22px; }}
    QComboBox QAbstractItemView {{ background: {s['surface']}; color: {s['text']}; border: 1px solid {s['border_strong']};
        selection-background-color: {s['surface_2']}; selection-color: {s['text']}; }}
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {s['border_strong']}; border-radius: 3px; background: {s['bg_2']}; }}
    QCheckBox::indicator:checked {{ background: {s['accent']}; border-color: {s['accent']}; }}
    QCheckBox::indicator:focus {{ border-color: {s['accent']}; }}
    QFrame[role="ack"] QCheckBox::indicator:checked {{ background: {s['action']}; border-color: {s['action']}; }}

    /* tables */
    QTableWidget {{ background: {s['surface']}; alternate-background-color: {s['surface']}; border: 1px solid {s['border']};
        border-radius: {r['md']}px; gridline-color: {s['border']}; font-size: {FS['sm']}px; selection-background-color: {s['surface_2']};
        selection-color: {s['text']}; outline: none; }}
    QTableWidget::item {{ padding: 4px 10px; border-bottom: 1px solid {s['border']}; }}
    QTableWidget::item:hover {{ background: {s['surface_2']}; }}
    QHeaderView::section {{ background: {s['bg_2']}; color: {s['muted']}; font-size: 10px; font-weight: 600; letter-spacing: 1px;
        padding: 7px 10px; border: 0; border-bottom: 1px solid {s['border']}; }}
    QTableCornerButton::section {{ background: {s['bg_2']}; border: 0; }}

    /* tabs */
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; border: 0; border-bottom: 2px solid transparent; color: {s['muted']}; padding: 8px 12px; font-weight: 500; }}
    QTabBar::tab:hover {{ color: {s['text']}; }}
    QTabBar::tab:selected {{ color: {s['text']}; border-bottom: 2px solid {s['accent']}; }}
    QFrame#tabline {{ background: {s['border']}; max-height: 1px; min-height: 1px; }}

    /* scrollbars */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {s['border_strong']}; border-radius: 3px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {s['faint']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {s['border_strong']}; border-radius: 3px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    """
