# Changelog

All notable changes to macmon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- A pytest test suite (123 tests, hermetic: no network, no live subprocess, tmp_path + monkeypatch only) covering the pure formatters, the cross-platform layer, the size parsers, the security helpers (lsof field-anchoring, suspicious-port classification, pf IP validation and exact-token rule matching), the gc cache/mtime helpers, and the audited safety behaviors as regressions (dashboard ESC-drain, `_trash_or_rm` never escalating a Trash failure to permanent deletion, the duplicates keeper, the Python 3.11 guard).
- CI runs the suite on macOS, Ubuntu and Windows (Python 3.11 and 3.13); a `[test]` extra (`pip install -e ".[test]"`) pulls in pytest.

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
