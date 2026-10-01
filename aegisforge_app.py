#!/usr/bin/env python3
"""PyInstaller entry point for AegisForge.app (the desktop app).

The NATIVE Qt app (``macmon_core.app_native``, PySide6 widgets -- no webview)
is the primary face: a real window with the seven sections, calling the
engine in-process through ``macmon_core.app_api.Api`` (every guardrail lives
there) and reading through ``macmon_core.app_webui``'s dict builders on a
worker thread. It runs on macOS and Windows.

Fallbacks, in order, so nothing regresses when a dependency is missing:
  1. PySide6 absent  -> the pywebview / WKWebView window (``app_webui.run_window``)
  2. pywebview absent -> the menu-bar agent (``app_menubar.main``), which opens
     the read-only dashboard in the browser and carries its own action items
All logic lives in macmon_core so the CLI and the .app share one engine.
Build with ./build_aegisforge.sh.
"""


def main() -> int:
    try:
        from macmon_core import app_native
    except ImportError:
        app_native = None
    if app_native is not None:
        return app_native.run()
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
