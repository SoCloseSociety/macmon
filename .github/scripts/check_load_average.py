#!/usr/bin/env python3
"""CI gate: load_average() must report a real value on every platform.

On Windows, psutil emulates load average with a background sampler that returns
(0.0, 0.0, 0.0) until it warms up (~5 min). macmon is a one-shot CLI, so a fresh
process never warms it up: without a fallback, load would read 0.0 forever and
the health check's "CPU Load" would always pass meaninglessly.

The load must be applied DURING the call, not before it: the fallback measures a
short cpu_percent window, so a burst that has already finished leaves an idle
window and 0.0 would be a legitimate reading of an idle machine.
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

from modules.platform_compat import OS_NAME, load_average

stop = threading.Event()


def burn():
    while not stop.is_set():
        sum(i * i for i in range(5000))


# Keep the CPU genuinely busy for the whole measurement window.
threads = [threading.Thread(target=burn, daemon=True) for _ in range(2)]
for t in threads:
    t.start()
time.sleep(0.5)  # let the CPU ramp before measuring

try:
    la = load_average()
finally:
    stop.set()
    for t in threads:
        t.join(timeout=2)

print(f"OS={OS_NAME}  load_average()={la}  (measured under sustained load)")

if not isinstance(la, tuple) or len(la) != 3:
    sys.exit(f"FAIL: expected a 3-tuple, got {la!r}")
if not all(isinstance(v, (int, float)) for v in la):
    sys.exit(f"FAIL: non-numeric values in {la!r}")
if not any(la):
    sys.exit(
        f"FAIL: load_average() returned the all-zero placeholder {la!r} on {OS_NAME} "
        "while the CPU was deliberately busy. The psutil fallback is reporting "
        "'no data' as 'idle'."
    )
print("OK: load_average() reports a real value under load.")
