#!/usr/bin/env python3
"""CI gate: macOS-only commands must degrade cleanly on Windows/Linux.

Each must exit 0 and say it requires macOS -- never traceback, never act.
"""
import subprocess
import sys

MACOS_ONLY = [
    ["security", "--scan"],
    ["privacy", "--scan"],
    ["startup", "--list"],
    ["uninstall", "--list"],
    ["auto", "--status"],
    ["focus"],
    ["restore"],
    ["purge"],
]

failures = []
for cmd in MACOS_ONLY:
    r = subprocess.run([sys.executable, "macmon.py", *cmd], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    label = " ".join(cmd)
    if r.returncode != 0:
        failures.append(f"{label}: exit {r.returncode} -- {out[:160]}")
    elif "Traceback" in out:
        failures.append(f"{label}: traceback -- {out[:160]}")
    elif "requires macOS" not in out:
        failures.append(f"{label}: no 'requires macOS' notice -- {out[:160]}")
    else:
        print(f"OK  {label:22s} -> {out.splitlines()[0][:70]}")

if failures:
    print("\nFAILURES:")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("\nAll macOS-only commands degraded cleanly.")
