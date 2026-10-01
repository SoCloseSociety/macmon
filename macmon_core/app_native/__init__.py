"""AegisForge.app -- the TRUE native desktop face (PySide6 / Qt Widgets).

Real native widgets, no webview: a main window with the brand sidebar and
seven sections (Overview, Processes, Clean, Security, Docker, Disk,
Sentinel), the same funnels and the same design system as the rest of the
SoClose suite. It reuses the engine as-is:

  - ACTIONS go through ``macmon_core.app_api.Api`` in-process (no HTTP, no
    bridge): every guardrail that module enforces (protected set, recycled
    PID, never_touch + acknowledged override, SIGTERM-only, preview-first +
    Trash-first clean, dangling-only prune, the four opt-in keys, WebKit /
    own-tree refusal, name-safe verdicts) holds unchanged
  - READS are ``macmon_core.app_webui``'s pure dict builders, called on a
    worker thread -- the UI thread never calls the engine

Importing this package raises ImportError when PySide6 is not installed, so
``aegisforge_app.py`` can fall back to the pywebview window, then the
menu-bar agent. PySide6 is app-only (``pip install -e ".[app]"``); the
``macmon`` CLI never depends on it.
"""
from __future__ import annotations

import PySide6  # noqa: F401  -- the whole package is unavailable without Qt

from .window import MainWindow, Reads, apply_theme, run

__all__ = ["MainWindow", "Reads", "apply_theme", "run"]
