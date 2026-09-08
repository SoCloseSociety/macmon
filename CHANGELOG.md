# Changelog

All notable changes to macmon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- A pytest test suite (172 tests, hermetic: no network, no live subprocess, tmp_path + monkeypatch + a real PTY only) covering the pure formatters, the cross-platform layer, the size parsers, the security helpers (lsof field-anchoring, suspicious-port classification, pf IP validation and exact-token rule matching), the gc cache/mtime helpers, and the audited safety behaviors as regressions (dashboard ESC-drain on a real PTY, `_trash_or_rm` never escalating a Trash failure to permanent deletion across all four modules, the uninstall path-traversal guard, the kill/suspend protected-target blacklist, the sentinel fleet-trim config clamps, the duplicates keeper, the Python 3.11 guard).
- CI runs the suite on macOS, Ubuntu and Windows (Python 3.11 and 3.13); a `[test]` extra (`pip install -e ".[test]"`) pulls in pytest.

### Security / Safety (guardrail audit)
- **(hard gap) Dashboard key readers did not actually drain ESC sequences on a real terminal.** They read via a buffered `sys.stdin.read(1)`, which pulls the whole `ESC [ F` into a userspace buffer and returns `ESC`; `select()` then sees the kernel buffer empty and the drain loop exits, so the trailing `[`/`F` leaked into the next poll as fake shortcuts (End -> Focus mode quits apps + `purge`, with no confirmation). Both readers now use `os.read(fd, 1)` so `select()` and the read share the same kernel buffer; locked by a real-PTY regression test (the old `_FakeStdin` test could not reproduce buffering and gave false assurance).
- **(hard gap) `macmon uninstall .` / `..` resolved to `/Applications` and `/`.** The leftover scan built the target path from the raw argument, so a path-like name pointed at real directories that `--permanent -y` would `rmtree`. A new `_valid_app_name` guard rejects any name containing a separator, a `..` segment, or nothing but dots/spaces.
- **(hard gap) `suspend`/`nice`/`kill` by name could hit system-critical processes.** Name matching had no blacklist and the PID path only excluded macmon itself, so `macmon suspend <short string>` could SIGSTOP `loginwindow`/`Finder`/`Dock`/the parent shell, and a raw PID 1 was allowed. `_find_process` now refuses PID <= 1, macmon's own process and its parent shell, and a protected-name set; `suspend` also confirms per match (it had no confirmation at all) and gained a `--yes` flag.
- **(hardening) Sudoers rule now derives the user from `pwd.getpwuid(os.getuid())`** as its comment always claimed, instead of `getpass.getuser()` (which trusts `$USER`/`$LOGNAME`). The injection-rejecting regex was already the effective guard; this removes the spoofable input entirely.
- **(hardening) `sweep` no longer classifies package-manager services as orphans.** Executables under `/opt/homebrew`, `/usr/local`, `/opt/local` and `/nix` are spared, so a Homebrew-managed `ollama serve` / node / python service is never killed as a stray.
- **(hardening) Sentinel fleet-trim config is clamped.** `fleet_keep` and `idle_samples` are read through helpers that floor them at 1 (and fall back to defaults on garbage), so a `0` in the config can no longer disable the guard that spares the session you are actively using.

### Fixed
- `gc` reported a wildly inflated size for "Docker dangling images" -- it labeled the entry dangling and ran only `docker image prune -f`, but sized it with `docker system df`'s total Images reclaimable (what `prune -a` would free, every unused image). On a busy machine this overstated the reclaimable space by 100x+ (e.g. 20.8 GB shown vs ~120 MB actually freed). It now sums the dangling images' own sizes, matching what the safe prune reclaims. Found by dogfooding the tool on a real 65-container host.
- `security` Docker audit missed the most common root container: an implicit-root container reports an empty `Config.User`, and the space-split dropped that trailing empty field, so the "ROOT USER" finding never fired for it. The inspect fields are now pipe-delimited and an empty user is treated as root.
- `uninstaller` permanent delete reported success (and counted the freed bytes) even when `rmtree(ignore_errors=True)` left the path behind; it now returns whether the path is actually gone, matching the other modules' `_trash_or_rm`.
- `health` battery check fabricated a "100% / 0 cycles" pass on Windows/Linux laptops (cycle count and capacity come from macOS `system_profiler` only); it now abstains off-macOS.
- `sweep` had two latent off-macOS crashes: `_clean_dead_ports` used the Unix-only `signal.SIGKILL` (now falls back to `SIGTERM`), and `_kill_orphans` called the POSIX-only `psutil.Process.terminal()` (now guarded).
- `autopilot` daemon leaked a SQLite/WAL connection on any cycle where a rule raised (the connection was closed only on the success path); the rule body is now wrapped so the connection always closes.

### Changed
- The internal package was renamed `modules/` -> `macmon_core/` so a `pip install` no longer drops a generic top-level `modules` package into site-packages (which would collide with any other project shipping the same name). The `macmon.py` entry point, the console script, and every command invocation are unchanged; verified end-to-end by building the wheel and running the installed `macmon` console script from a neutral directory.
- Removed a dead `SUSPICIOUS_PROCESS_NAMES` import (and its no-op `try/except ImportError`) from the autopilot miner rule; the rule's own miner/pool-protocol keyword list is unchanged.
- The tree is now fully pyflakes-clean: removed three unnecessary `global` declarations in the dashboard cache refreshers (the caches are dicts mutated in place, never rebound), a dead `fan_pct` local, and an unused `pytest` import. The CI lint step is now a hard gate instead of report-only, so any regression fails the build.

## [1.2.1] - 2026-08-26

### Added
- `pyproject.toml`: macmon is now pip-installable (`pip install .`, `pipx install .`), with a `macmon` console entry point and a machine-enforced `requires-python >= 3.11` (no more obscure `tomllib` error on 3.10).

### Fixed
- **(high)** The live dashboard's Unix key reader did not drain ANSI escape sequences, so arrow / End / Delete keys could fire action shortcuts (End -> Focus mode quit apps, Delete -> a process kill, Right-arrow -> a full clean). Both readers now drain on ESC, matching the existing Windows guard.
- Sentinel network RTT used BSD-only `ping` flags, so RTT and the network-saturation alert were dead on Linux and Windows; now branches per OS.
- Sentinel `purge` could crash the sampler on timeout (losing cooldown state and spamming alerts) and stamped its cooldown only on success; now guarded and stamped on attempt.
- `sentinel --trim` could close an actively-running AI session; it now builds idle streaks from measured CPU and never trims a busy session.
- Windows `clean` double-counted and could over-delete `%LOCALAPPDATA%\Temp`; the cache scan is scoped to `INetCache`.
- `cleaner._clean_module` reported success even when nothing was deleted; the health Docker size was parsed with binary units against docker's decimal output; the dashboard fabricated a fan RPM off-macOS; the Linux cron line did not quote paths with spaces.

### Changed
- pyflakes cleanup across the tree (54 -> 5 remaining, all intentional): dead imports removed, placeholder-less f-strings fixed. No behavior change.

## [1.2.0] - 2026-07-16

### Added
- **Cross-platform core.** The portable commands (`ps`/`kill`/`suspend`/`nice`, `disk`/`bigfiles`/`dupes`, `clean`, `gc`, `network`/`flush-dns`, `docker`, `health`, `sentinel`, `dashboard`) run on Windows and Linux; macOS-only commands degrade with a clear "requires macOS" message instead of crashing. A GitHub Actions CI matrix (macOS/Ubuntu/Windows x Python 3.11/3.13) enforces this on every push.
- Sentinel auto-remediation on memory pressure: unload idle ollama models (Level 1a), purge inactive RAM (Level 1b, macOS), and close idle AI sessions (Level 2, opt-in). The console surfaces the hidden RAM hogs -- loaded ollama models and the Docker/Colima VM footprint.
- Branded macOS notifications carry the macmon icon instead of the generic Script Editor icon.
- New sentinel levers: `--enable-auto`, `--disable-auto`, `--aggressive`, `--trim`, `--unload-ollama`, `--setup-purge`, `--test-notify`.

### Fixed
- Load average read as `0.0` forever on Windows (psutil's emulated sampler never warms up in a one-shot CLI); now falls back to a measured estimate. Caught by the new Windows CI runner.

## [1.1.0] - 2026-07-12

### Added
- **MACMON-SENTINEL**: an always-on, near-zero-cost watchdog. A single-shot sampler fires every 60s via a LaunchAgent (~0.1% average CPU, no resident process), tracks CPU/RAM/swap/load/disk/network RTT/top process/AI-agent fleet, fires native macOS notifications on thresholds, and exposes a tactical console plus manual force levers.

### Fixed / Security
- A ground-up safety audit hardened all modules (120 defects). Highlights: deletes go to the Trash and never silently escalate to permanent removal; `clean --all` no longer touches non-regenerable data (Xcode Archives, `~/.m2`, `~/.gem`); exact-match (not substring) for uninstall/startup/quarantine targets; `security --block-ip` uses a dedicated pf anchor with IP validation; safer process sweeps and honest freed-size accounting.

## [1.0.0] - 2026-03-06

### Added
- Initial public release: a terminal-native macOS system monitor and cleaner -- 30 commands including a live TUI dashboard, process manager, system cleaner, dev garbage collector, security scanner, Docker manager, disk analyzer, duplicate finder, and an autopilot daemon. 100% local, zero telemetry, MIT licensed.

[Unreleased]: https://github.com/SoCloseSociety/macmon/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/SoCloseSociety/macmon/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/SoCloseSociety/macmon/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/SoCloseSociety/macmon/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/SoCloseSociety/macmon/releases/tag/v1.0.0
