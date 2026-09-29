# Changelog

All notable changes to macmon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added -- AegisForge proactive layer
- **AegisForge branding layer (user-facing only).** The sentinel now presents as *AegisForge -- fleet health, forged*: a Rich theme (`macmon_core/aegis.py: AEGIS_THEME`, tokens ground/surface/text/dim/mint/ember/amber/sky/critical/ok) drives the console and status panels, notifications are titled "AegisForge: ...", and the macOS notifier applet carries the AegisForge icon (`assets/aegisforge.icns`, `assets/macmon.icns` kept as fallback). The engine keeps its name everywhere that matters: the `macmon` CLI and console script, the `macmon_core` package, `macmon.py`, the LaunchAgent labels and the notifier bundle identifier are unchanged (Sentinel vendoring, PyPI and the notification permission all key on them).
- **Anticipatory detectors** (`macmon_core/aegis.py`), computed by the 60s sampler over the metrics.jsonl history so they alert on *trajectory*, not just level. All ON by default, notify-only:
  - *swap climbing*: swap already high and rising fast enough to cross `swap_critical_gb` within `swap_eta_min` -- "Swap 5.7 GB, +0.30 GB/min, reaches the 8 GB critical line in ~8 min", naming the biggest RSS holders. Fires only *before* the line; past it the absolute alert owns the incident.
  - *memory pressure climbing*: the same contract on RAM%.
  - *load catastrophe*: load1 above `load_factor` x logical cores for `load_sustain` consecutive samples, naming the top CPU offenders.
  - *process swarm*: a rapidly growing family -- headless browsers (puppeteer / playwright / Chrome for Testing / any browser binary run `--headless`) or any executable whose instance count jumps -- with the spawner named by walking the parent chain ("59 headless browser processes (+35 in 5 min), spawned by node shoot.mjs (pid 4242)"). Alert-only by construction.
  - *leaked orphans*: dev processes (headless browsers, node incl. next-server, python) reparented to PID 1 whose parent the sampler **watched die**, counted and trended.
  - The console gained a **FORGE** panel (slope, ETA, sustained load, swarm growth + spawner, proven/unproven orphans) and the verdict line gained trajectory flags (SWAP^, RAM^, LOAD, SWARM, LEAKS). Each sample now also records `ncpu`, family counts, per-executable counts, orphan counts, top-3 CPU and top-2 RSS.
- **Opt-in auto-reap of leaked orphans** (`auto_reap_orphans`, default **false**; `--enable-auto --reap`, manual `--reap-orphans`). Reaps ONLY processes that are under PID 1 *and* whose parent is proven dead by lineage (an earlier sample saw the live parent, a later one sees it exited / zombie / PID reused), in the dev families, outside the never-touch set, idle (< 1% CPU, itself and its descendants) for `reap_idle_samples` samples, with no ESTABLISHED TCP connection (unknown => skip), still under PID 1 at signal time -- and only with SIGTERM. A dead process-*group* leader is deliberately NOT accepted as proof: on macOS every launchd/brew-services job (e.g. `ollama serve`) and the Android emulator look like that.
- New sentinel config keys (see README): `trend_window`, `swap_trend_min_gb`, `swap_slope_gb_min`, `swap_eta_min`, `ram_trend_min`, `ram_slope_pct_min`, `ram_eta_min`, `load_factor`, `load_sustain`, `swarm_window`, `swarm_headless_min/growth/max`, `swarm_floor`, `swarm_min`, `swarm_growth`, `orphan_alert_min`, `auto_reap_orphans`, `reap_idle_samples`, `reap_families`, `reap_max`.
- 137 new tests (`tests/test_aegis.py`, suite total 309) covering the slope/ETA math, family classification, the never-touch set, lineage proof (including PID reuse and zombie parents), every detector in its incident and healthy scenarios, the first-deploy "no growth claim against old-format history" guard, and the safety invariants: notify-only default, the swarm path has no kill primitive (source-level check + `os.kill`/`psutil.Process.*` tripwires), only lineage-proven leaks are reapable, every reap guard blocks individually, SIGTERM only, protected processes are never leaks even when reparented.

### Added -- AegisForge.app (native macOS menu-bar app)
- A native macOS menu-bar app, built like the fleet's Sentinel.app (PyInstaller `--windowed`, ember-shield icon, `LSUIElement` agent, ad-hoc codesign). `macmon_core/app_menubar.py` is a `rumps` app that is the menu-bar FACE of the same engine -- it does not sample or remediate on its own: it reads what the 60s LaunchAgent sampler wrote (`metrics.jsonl` + the alerts log), runs the Aegis trend detectors on those rows, and shows a status glyph (tinted by worst severity) plus a dropdown with live CPU/RAM/swap/load, the FORGE anticipation lines, recent alerts, and explicit actions (open dashboard, clean, purge, pause/resume). No action is ever auto-invoked; nothing auto-kills a workload.
- `build_aegisforge.sh` builds `dist/AegisForge.app` (`--dmg` optional); `aegisforge_app.py` is the frozen entry point. `rumps`/`pyobjc` are an OPTIONAL `[app]` extra -- the public `macmon` CLI still installs and runs with the core deps alone (no GUI dependency). 13 hermetic tests (`tests/test_app_menubar.py`) cover the severity ranking, title, menu body, the resilient `gather()` (never raises on a bad sample), and that the actions map to the right subcommands.

### Safety (AegisForge)
- The never-touch set is applied before any other judgment: PID <= 1, macmon's own process and parent shell, the hardened `_is_protected_target` names, other users, IDEs (VSCode/Cursor/Zed/Xcode and their helpers), Claude/codex/MCP sessions and native hosts, `~/.claude` `~/.codex` `~/.cursor` `~/.vscode` `~/.ollama` `~/.macmon` infrastructure, ollama and local LLM runtimes, Sentinel/NeoBot/fleet (WireGuard, sshd), VMs and containers, terminals/shells/multiplexers, anything under /System, /usr/libexec, /usr/sbin, and the user's apps under /Applications (except a browser binary running `--headless`, which is an automation instance, not an app anyone is looking at). A process with a live parent is never signalled, whatever the swarm looks like.

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
