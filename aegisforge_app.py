#!/usr/bin/env python3
"""PyInstaller entry point for AegisForge.app (the macOS app).

Prefers a NATIVE app window (pywebview/WKWebView) rendering the local dashboard
-- the fleet Sentinel pattern: a real window, not a browser tab. Falls back to
the menu-bar agent (which opens the dashboard in the browser) if pywebview is
absent, then to a helpful message. All logic lives in macmon_core so the CLI and
the .app share one engine. Build with ./build_aegisforge.sh.
"""


def main() -> int:
    try:
        from macmon_core import app_webui
        if app_webui.run_window():   # blocks until the window closes
            return 0
    except Exception:
        pass  # fall through to the menu-bar / message path
    from macmon_core.app_menubar import main as menubar_main
    return menubar_main()


if __name__ == "__main__":
    raise SystemExit(main())
