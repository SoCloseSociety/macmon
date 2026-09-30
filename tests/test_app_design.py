"""Design-system invariants for the AegisForge.app page (assets/webui).

These lock the things a visual refactor can silently break: the one shared
palette (Rich console == web app), dark + light themes, offline-only assets,
the read-only affordances, accessibility hooks, responsive rules, and the
structural contract between index.html / app.css / app.js (every id the
script touches exists; every static class it emits is styled). Hermetic:
text only, no browser.
"""
import re
import shutil
import subprocess

import pytest

from macmon_core import aegis, app_webui

WEBUI = app_webui.WEBUI_DIR
HTML = (WEBUI / "index.html").read_text(encoding="utf-8")
CSS = (WEBUI / "app.css").read_text(encoding="utf-8")
JS = (WEBUI / "app.js").read_text(encoding="utf-8")
SVG = (WEBUI / "mark.svg").read_text(encoding="utf-8")
ALL = {"index.html": HTML, "app.css": CSS, "app.js": JS, "mark.svg": SVG}


# ── one palette: the Rich theme and the page share the brand tokens ──────

class TestPalette:
    @pytest.mark.parametrize("name,hexval", sorted(aegis.C.items()))
    def test_every_aegis_token_is_a_css_token(self, name, hexval):
        # aegis.py C[] is the console's palette; the page mirrors it verbatim
        assert hexval.upper() in CSS.upper(), f"{name} {hexval} missing from app.css"

    def test_brand_tokens_are_named(self):
        for tok in ("--af-ground", "--af-surface", "--af-t1", "--af-mint", "--af-ember", "--af-amber",
                    "--af-sky", "--af-bad", "--af-ok"):
            assert re.search(rf"{tok}:\s*#[0-9A-Fa-f]{{6}}", CSS), tok
        assert re.search(r"--af-mint:\s*#34E5A0", CSS, re.I) and re.search(r"--af-ember:\s*#FF7A1A", CSS, re.I)

    def test_semantic_layer_and_scales(self):
        for tok in ("--bg", "--surface", "--border", "--text", "--muted", "--accent", "--action",
                    "--sev-ok", "--sev-info", "--sev-low", "--sev-medium", "--sev-high", "--sev-critical",
                    "--fs-xs", "--fs-md", "--fs-2xl", "--sp-1", "--sp-4", "--sp-8",
                    "--r-sm", "--r-md", "--r-full", "--shadow-1", "--shadow-3",
                    "--dur-fast", "--dur-base", "--ease-out", "--z-modal", "--z-toast",
                    "--font-sans", "--font-mono"):
            assert re.search(rf"{re.escape(tok)}:\s*[^;]+;", CSS), tok

    def test_components_use_tokens_not_raw_severity_hex(self):
        # component rules must reference var(--sev-*) etc.; raw brand hex is
        # confined to the token block at the top (":root {" ... first "}")
        body = CSS.split("/* ── 2. Base", 1)[1]
        raw = re.findall(r"#(?:34E5A0|FF7A1A|FBBF24|7DD3FC|FB7185|34D399)\b", body, re.I)
        assert raw == [], raw


# ── themes: dark by default, light coherent, explicit override ───────────

class TestThemes:
    def test_dark_and_light_both_defined(self):
        assert "@media (prefers-color-scheme: light)" in CSS
        assert ':root:not([data-theme="dark"])' in CSS          # system light, unless forced dark
        assert ':root[data-theme="light"]' in CSS                # forced light
        assert ':root[data-theme="dark"]' in CSS                 # forced dark
        assert 'name="color-scheme" content="dark light"' in HTML

    def test_body_has_an_explicit_background(self):
        m = re.search(r"\nbody\s*\{([^}]*)\}", CSS)
        assert m and "background: var(--bg)" in m.group(1) and "color: var(--text)" in m.group(1)

    def test_light_ramp_swaps_every_semantic_token(self):
        light = CSS.split(':root[data-theme="light"]', 1)[1].split("}", 1)[0]
        for tok in ("--bg", "--surface", "--border", "--text", "--muted", "--accent", "--action",
                    "--sev-ok", "--sev-medium", "--sev-high", "--sev-critical", "--tint-ember", "--shadow-3"):
            assert f"{tok}:" in light, tok

    def test_theme_toggle_is_wired(self):
        assert 'id="theme-seg"' in HTML
        assert "af.theme" in JS and "documentElement.dataset.theme" in JS
        assert 'data-theme="system"' in HTML and 'data-theme="dark"' in HTML and 'data-theme="light"' in HTML


# ── offline, sink-free, no inline script/style ───────────────────────────

class TestOffline:
    def test_no_remote_reference_anywhere(self):
        for name, text in ALL.items():
            hits = [u for u in re.findall(r"https?://[^\s\"')]+", text) if "www.w3.org/2000/svg" not in u]
            assert hits == [], (name, hits)
        assert "@import" not in CSS and "url(" not in CSS   # icons are inline SVG, no image/font files

    def test_font_stack_falls_back_to_the_system(self):
        assert '"IBM Plex Sans"' in CSS and "-apple-system" in CSS and "system-ui" in CSS
        assert '"IBM Plex Mono"' in CSS and "ui-monospace" in CSS
        assert "@font-face" not in CSS   # nothing to download: local() or the system face

    def test_no_inline_script_or_style_attributes(self):
        assert re.search(r"<script(?![^>]*\bsrc=)", HTML) is None
        assert ' style="' not in HTML and "<style" not in HTML
        assert re.search(r"\son\w+=", HTML) is None   # no inline handlers

    def test_no_html_string_sink(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "srcdoc", "eval(", "new Function"):
            assert sink not in JS, sink

    def test_no_em_dash_in_any_asset(self):
        for name, text in ALL.items():
            assert chr(0x2014) not in text, name   # the em dash, spelled so this file has none either

    def test_js_parses(self):
        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed")
        subprocess.run([node, "--check", str(WEBUI / "app.js")], check=True, timeout=30)


# ── read-only mode, accessibility, motion, responsive ────────────────────

class TestAffordances:
    def test_read_only_mode_is_visible_and_disables_actions(self):
        assert 'id="ro-pill"' in HTML and "body.is-readonly .ro-pill" in CSS
        assert 'classList.toggle("is-readonly", !on)' in JS
        assert HTML.count("data-needs-bridge") >= 3
        assert JS.count("disabled: !bridge.ready") >= 6
        assert "read-only tab" in JS.lower()

    def test_stale_state_is_surfaced(self):
        assert 'classList.toggle("is-stale", !!d.stale)' in JS and ".age.is-stale" in CSS and "body.is-stale" in CSS

    def test_accessibility_hooks(self):
        assert 'class="skip"' in HTML and 'id="content"' in HTML
        assert HTML.count("aria-labelledby=") >= 8                    # 7 sections + the dialog
        assert 'role="dialog"' in HTML and 'aria-modal="true"' in HTML and 'aria-describedby="modal-body"' in HTML
        assert 'aria-live="polite"' in HTML
        assert 'role="tablist"' in HTML and 'aria-selected=' in HTML
        assert 'aria-current="page"' in HTML and 'aria-current="step"' in HTML
        assert '"aria-hidden": "true"' in JS                          # decorative SVG icons
        assert 'role: "switch"' in JS and '"aria-checked"' in JS and '"aria-label": label' in JS
        assert ":focus-visible" in CSS and "outline: 2px solid var(--accent)" in CSS
        assert ".sr-only" in CSS and HTML.count('class="sr-only"') >= 4

    def test_focus_trap_and_escape_in_the_confirm_modal(self):
        assert 'e.key !== "Tab") return;' in JS and 'e.key === "Escape"' in JS
        assert "e.shiftKey && document.activeElement === first" in JS   # Tab wraps inside the dialog
        assert "opener.focus()" in JS                                  # focus restored on close
        assert "e.target === back" in JS                               # backdrop click cancels, never confirms

    def test_keyboard_navigation(self):
        assert "ArrowDown" in JS and '"Home"' in JS and '"End"' in JS   # sidebar
        assert "ArrowRight" in JS                                        # docker tabs
        assert "VIEW_ORDER[n - 1]" in JS                                 # 1..7 jumps

    def test_motion_respects_reduced_motion(self):
        assert "@media (prefers-reduced-motion: reduce)" in CSS
        assert "animation-duration: .01ms !important" in CSS

    def test_responsive_rules(self):
        assert "@media (max-width: 640px)" in CSS and "@media (max-width: 860px)" in CSS
        assert "--gutter: 16px" in CSS                                   # phone side gutter
        assert "minmax(0, 1fr)" in CSS                                   # grids that cannot overflow
        assert ".card-flush { padding: 0; overflow-x: auto;" in CSS      # tables scroll inside their card
        assert 'name="viewport" content="width=device-width, initial-scale=1' in HTML

    def test_numbers_are_tabular(self):
        assert "font-variant-numeric: tabular-nums" in CSS


# ── structural contract between the three files ──────────────────────────

class TestContract:
    def test_every_id_the_script_touches_exists(self):
        ids = set(re.findall(r'\$\("([\w-]+)"\)', JS))
        assert len(ids) > 30
        missing = [i for i in ids if f'id="{i}"' not in HTML]
        assert missing == [], missing

    def test_every_static_class_the_script_emits_is_styled(self):
        classes = set()
        for lit in re.findall(r'class: "([^"]*)"', JS):
            for tok in lit.split():
                if tok and not tok.endswith("-"):     # "sev-" + level: dynamic prefix, skipped
                    classes.add(tok)
        assert len(classes) > 40
        unstyled = [c for c in sorted(classes) if not re.search(rf"\.{re.escape(c)}(?![\w-])", CSS)]
        assert unstyled == [], unstyled

    def test_every_icon_placeholder_has_a_path(self):
        names = set(re.findall(r'data-ico="([\w-]+)"', HTML))
        assert {"pulse", "cpu", "broom", "shield", "box", "disk", "mark", "eye", "clock"} <= names
        for n in names:
            assert re.search(rf"\b{n}: ", JS), n
        assert 'querySelectorAll("[data-ico]")' in JS

    def test_sentinel_nav_uses_the_brand_mark_geometry(self):
        # the mono mark: heater shield + hex coal + anvil line, on the 48-unit grid
        assert 'data-ico="mark"' in HTML
        assert "mark: { box: 48" in JS and "M24 6 38 11V24C38 32 32 38 24 42 16 38 10 32 10 24V11Z" in JS

    def test_every_view_has_a_head_icon_and_a_labelled_heading(self):
        for s in ("overview", "processes", "clean", "security", "docker", "disk", "sentinel"):
            sec = HTML.split(f'id="view-{s}"', 1)[1].split("</section>", 1)[0]
            assert 'class="vh-ico"' in sec and f'id="h-{s}"' in sec, s
            assert f'aria-labelledby="h-{s}"' in HTML, s

    def test_states_are_components_not_ad_hoc_text(self):
        for fn in ("function skeleton(", "function emptyState(", "function errBox(", "function working("):
            assert fn in JS, fn
        assert ".skel" in CSS and ".empty" in CSS and ".errbox" in CSS and ".working" in CSS
        assert "skeletonRows(" in JS                                     # tables get row skeletons

    def test_bridge_contract_echoes_create_time_and_override(self):
        # process_guard() returns "ct"; every signal echoes (pid, create_time, override)
        assert 'api(`${verb}_process`, g.pid, g.ct != null ? g.ct : p.created, acked)' in JS
        # quarantine escalates -> same guarded-acknowledgement funnel as kill
        assert 'api("quarantine", g.pid, g.ct != null ? g.ct : null, !!g.guard && acked)' in JS
        assert "on a guarded process -- it will be killed and its binary blocked" in JS

    def test_protected_rows_carry_the_badge(self):
        assert '"badge badge-protected"' in JS and 'icon("lock")' in JS and ".badge-protected" in CSS
