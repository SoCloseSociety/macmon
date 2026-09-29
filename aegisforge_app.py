#!/usr/bin/env python3
"""PyInstaller entry point for AegisForge.app (the macOS menu-bar app).

Kept tiny on purpose: all logic lives in macmon_core.app_menubar so the CLI and
the .app share one engine. Build with ./build_aegisforge.sh.
"""
from macmon_core.app_menubar import main

if __name__ == "__main__":
    raise SystemExit(main())
