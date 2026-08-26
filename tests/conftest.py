"""Pytest configuration: make the repo root importable from anywhere.

Allows `import macmon_core.X` and `import macmon` to resolve regardless of the
directory pytest is invoked from (macOS, Linux, Windows CI runners alike).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
