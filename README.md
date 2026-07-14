<p align="center">
  <img src="https://raw.githubusercontent.com/SoCloseSociety/.github/main/assets/org-banner.svg" alt="SoClose Society" width="900">
</p>

<h1 align="center">macmon — the free, open-source Mac cleaner &amp; system monitor for developers</h1>

<p align="center">
  <strong>A terminal-native macOS system monitor, cleaner and optimizer — a free, private, open-source CCleaner alternative built for developers. 100% local, zero telemetry.</strong>
</p>

<p align="center">
  <a href="https://github.com/SoCloseSociety/macmon/stargazers"><img src="https://img.shields.io/github/stars/SoCloseSociety/macmon?style=flat-square&color=575ECF" alt="Stars"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-575ECF?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/macOS-12%2B-575ECF?style=flat-square&logo=apple&logoColor=white" alt="macOS 12+">
  <img src="https://img.shields.io/badge/license-MIT-575ECF?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/modules-17-575ECF?style=flat-square" alt="17 modules">
  <img src="https://img.shields.io/badge/commands-30%2B-575ECF?style=flat-square" alt="30+ commands">
  <img src="https://img.shields.io/badge/telemetry-none-brightgreen?style=flat-square" alt="No telemetry">
  <img src="https://img.shields.io/badge/cost-$0-brightgreen?style=flat-square" alt="Zero Cost">
</p>

<p align="center">
  <a href="#installation">Install</a> &bull;
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#screenshots">Screenshots</a> &bull;
  <a href="#macmon-sentinel">Sentinel</a> &bull;
  <a href="#features">Features</a> &bull;
  <a href="#command-reference">Commands</a> &bull;
  <a href="https://soclose.co">SoClose</a>
</p>

<p align="center">
  <img src="assets/sentinel.svg" alt="macmon sentinel — real-time tactical system monitor for macOS" width="820">
</p>

---

## What is macmon?

**macmon** is a **free, open-source Mac system monitor and cleaner** that runs entirely in your terminal. Think **CCleaner Pro + Activity Monitor + Security Scanner + Docker Manager**, reimagined as one fast CLI with a live TUI dashboard — purpose-built for developers who live in the shell.

If you have ever searched for *"how to clean my Mac"*, *"free CCleaner alternative for macOS"*, *"free up disk space on Mac"*, *"why is my Mac slow"*, or *"macOS system monitor CLI"* — this is the tool, and it never phones home.

**30+ commands** · **17 modules** · **Live dashboard** · **MACMON-SENTINEL always-on watchdog** · **Keyboard shortcuts that execute real actions** · **Autopilot daemon** · **Thermal management** · **Security &amp; malware scanner** · **Docker manager** · **100% local, zero telemetry**

### Why developers pick macmon

- **Free &amp; open source (MIT)** — no license, no account, no upsell, no cloud.
- **Private by design** — everything runs locally; nothing is uploaded, ever.
- **Developer-first cleanup** — reclaims `node_modules`, stale venvs, Docker cache, Xcode/DerivedData, npm/pip/brew caches with safety guards.
- **Safe deletes** — moves to Trash by default and never silently escalates to permanent deletion.
- **One binary of a habit** — monitor, clean, secure and keep your Mac fast from a single CLI.

---

## Screenshots

<table>
<tr>
<td width="50%" valign="top" align="center">
<strong>System health, scored /100</strong><br>
<img src="assets/health.svg" alt="macmon health — macOS system health check with a score out of 100" width="100%">
</td>
<td width="50%" valign="top" align="center">
<strong>One-pass junk &amp; cache cleaner</strong><br>
<img src="assets/clean.svg" alt="macmon clean — free up disk space on macOS by clearing caches, logs and temp files" width="100%">
</td>
</tr>
<tr>
<td width="50%" valign="top" align="center">
<strong>Security &amp; malware audit</strong><br>
<img src="assets/security.svg" alt="macmon security — macOS network security, firewall and malware indicator scan" width="100%">
</td>
<td width="50%" valign="top" align="center">
<strong>Categorized process monitor</strong><br>
<img src="assets/ps.svg" alt="macmon ps — developer process monitor for macOS grouped by category" width="100%">
</td>
</tr>
</table>

> Above: the **MACMON-SENTINEL** tactical console (top of page). All output is real, rendered live — no mockups.

---

## Features

<table>
<tr>
<td width="50%" valign="top">

### Live Dashboard
Full-screen TUI with real-time monitoring:
- CPU per-core with sparkline history
- RAM / Swap / Wired / Active breakdown
- Disk I/O rates & Network sparklines
- Thermal monitoring (CPU temp, fan RPM, throttle detection)
- Security score & Docker containers live panels
- Process list with category icons
- Alerts & smart suggestions

</td>
<td width="50%" valign="top">

### Interactive Keyboard Shortcuts
Execute **real actions** directly from the dashboard:

| Key | Action |
|-----|--------|
| `S` | Sweep: kill zombies, orphans, stale locks |
| `P` | Purge inactive RAM |
| `C` | Full system clean (junk, caches, browsers) |
| `G` | Dev garbage collector (node_modules, venvs) |
| `H` | Health check + auto-fix |
| `K` | Full security audit |
| `D` | Docker overview |
| `F` | Focus mode (quit apps, DND) |
| `1-9` | Kill process #N from the list |
| `Q` | Quit |

</td>
</tr>
</table>

<table>
<tr>
<td width="33%" valign="top">

### System Cleaner
- System junk (temp, logs, crash reports)
- 8 browsers (Chrome, Safari, Firefox, Arc, Brave, Opera, Edge, Chromium)
- App caches (Xcode, VSCode, Cursor, Slack, Spotify, JetBrains...)
- Clipboard & recent items clear
- Schedule via launchd

</td>
<td width="33%" valign="top">

### Dev Garbage Collector
- npm / pnpm / yarn / bun caches
- Stale `node_modules` and `venvs`
- `__pycache__`, `.DS_Store`
- Homebrew, Docker, Xcode DerivedData
- iOS Simulators, Go, Cargo

</td>
<td width="33%" valign="top">

### Security Scanner
- Security score /100
- Firewall, SIP, Gatekeeper, FileVault
- Suspicious port detection (Metasploit, IRC, backdoors)
- Remote tool detection (TeamViewer, AnyDesk, VNC)
- Malware indicators & crypto miners
- IP blocking via pf & process quarantine

</td>
</tr>
<tr>
<td width="33%" valign="top">

### Process Manager
- Category detection (IDE, browser, Docker, Node, Python...)
- Kill / suspend / resume / renice / quit / restart
- Zombie & orphan sweep
- Port manager with lsof fallback

</td>
<td width="33%" valign="top">

### Docker Manager
- Container / image / volume / network listing
- Full prune, stop-all, restart, logs, live stats
- Compose project listing
- Security scan (privileged, host network, root, public ports)

</td>
<td width="33%" valign="top">

### Autopilot Daemon
Background rules engine:
- RAM critical → purge
- CPU runaway → renice
- Thermal management → auto-throttle
- Security monitoring → alerts
- Browser RAM hog → notification
- Low disk & weekly clean reminders

</td>
</tr>
<tr>
<td width="33%" valign="top">

### Privacy Cleaner
- Recent items, Finder, QuickLook cache
- Shell history (zsh/bash/fish)
- REPL history (Python/Node/SQLite/IRB)
- Spotlight, SSH known_hosts, Siri

</td>
<td width="33%" valign="top">

### Health Check
- 13+ system checks with score /100
- Auto-fix for safe issues
- Report saving & history

</td>
<td width="33%" valign="top">

### More Tools
- **Startup Manager** — LaunchAgents, daemons, cron
- **App Uninstaller** — full leftover detection
- **Duplicate Finder** — 3-phase xxhash + SHA-256
- **Disk Analyzer** — big files, categorization
- **Focus Mode** — quit apps, DND, restore

</td>
</tr>
</table>

---

## Installation

```bash
git clone https://github.com/SoCloseSociety/macmon.git
cd macmon
bash install.sh
```

This will:
1. Check Python 3.11+
2. Create a venv at `~/.macmon/venv`
3. Install dependencies (`rich`, `psutil`, `typer`, `send2trash`, `xxhash`)
4. Create `/usr/local/bin/macmon` wrapper
5. Initialize config at `~/.macmon/config.toml`

### Build DMG (optional)

```bash
bash build_dmg.sh
open dist/macmon.dmg
```

---

## Quick Start

```bash
# Launch the live dashboard
macmon

# Full system clean
macmon clean --all -y

# Dev garbage collector
macmon gc --all -y

# Security audit
macmon security

# Health check + auto-fix
macmon health --fix

# Kill zombies & orphans
macmon sweep -y

# Purge RAM
macmon purge

# Start autopilot daemon
macmon auto --start

# Focus mode
macmon focus

# All commands
macmon --help
```

---

## Command Reference

| Command | Description |
|---------|-------------|
| `macmon` | Live dashboard |
| `macmon ps` | Process list with categories |
| `macmon kill <target>` | Kill process |
| `macmon suspend / resume <target>` | Suspend / resume process |
| `macmon nice <target> <val>` | Renice process |
| `macmon quit / restart <app>` | Graceful quit / restart app |
| `macmon sweep` | Kill zombies, orphans, stale locks |
| `macmon ports` | Port manager |
| `macmon clean --scan` | Preview cleanable junk |
| `macmon clean --all -y` | Full system clean |
| `macmon clean --browsers` | Browser cleaner |
| `macmon gc --scan` | Preview dev garbage |
| `macmon gc --all -y` | Full dev GC |
| `macmon privacy --scan` | Preview privacy traces |
| `macmon privacy --full -y` | Wipe all traces |
| `macmon health` | Health check /100 |
| `macmon health --fix` | Auto-fix safe issues |
| `macmon startup --list` | List startup items |
| `macmon startup --audit` | Audit suspicious items |
| `macmon uninstall <app>` | Uninstall + leftover cleanup |
| `macmon dupes <path>` | Find duplicate files |
| `macmon bigfiles` | Find large files |
| `macmon disk` | Disk usage analyzer |
| `macmon network` | Network connections |
| `macmon flush-dns` | Flush DNS cache |
| `macmon security` | Security audit /100 |
| `macmon security --block-ip <ip>` | Block IP via pf firewall |
| `macmon security --quarantine <proc>` | Kill + block process |
| `macmon docker` | Docker overview |
| `macmon docker --prune -y` | Docker full cleanup |
| `macmon docker --scan` | Docker security audit |
| `macmon auto --start / --stop` | Start / stop autopilot daemon |
| `macmon focus / restore` | Focus mode on / off |
| `macmon purge` | Purge inactive RAM |
| `macmon report` | Session report |
| `macmon config --show / --edit` | View / edit config |
| `macmon sentinel --install` | Arm the always-on light monitor |
| `macmon sentinel` | Tactical console snapshot |
| `macmon sentinel --watch` | Live tactical console |
| `macmon sentinel --force-clean` | Manual override: scan then clean |
| `macmon sentinel --test-notify` | Test notification (shows the macmon icon) |
| `macmon sentinel --enable-auto` | Enable safe auto-remediation (RAM purge) |
| `macmon sentinel --enable-auto --aggressive` | Also auto-close idle AI sessions |
| `macmon sentinel --trim` | Close idle AI sessions now |

---

## MACMON-SENTINEL (always-on monitor)

An ultra-light watchdog that keeps the Mac operational without you watching it.

- **Near-zero cost:** a single-shot sampler fires every 60s via a LaunchAgent,
  measures in ~0.5s, appends one compact JSON line, then exits. No resident
  process between samples (~0.1% average CPU, ~20 MB peak per sample).
- **Precise:** tracks CPU, RAM, swap, load, disk, network RTT, the top CPU
  process, and the AI-agent fleet (Claude Code / codex / MCP counts + RSS) so
  forgotten sessions are surfaced, not rediscovered in a crisis.
- **Auto-interaction:** threshold alerts fire native macOS notifications
  (swap high, memory pressure, runaway process, network saturated, low disk,
  AI fleet too large), each with a cooldown.
- **Auto-remediation (safe escalation):** when memory pressure is genuinely
  critical the Sentinel can act on its own, and always notifies what it did:
  - **Level 1 -- `auto_purge`** (non-destructive): frees inactive RAM via
    `purge`. Enable with `macmon sentinel --enable-auto`.
  - **Level 2 -- `auto_trim_fleet`** (opt-in): closes *idle* AI coding sessions
    (Claude Code) beyond a configurable minimum, protecting the most recently
    active ones so the session you are using is never touched. Sessions are
    resumable (`--resume`). Enable with `macmon sentinel --enable-auto --aggressive`.
  - Manual lever any time: `macmon sentinel --trim` closes idle sessions now.
- **Manual override:** force levers when you must push the system --
  `--force-purge`, `--force-clean`, `--force-focus`, `--pause` / `--resume`.
- **Branded notifications:** alerts pop up carrying the macmon icon (not the
  generic Script Editor icon) via a tiny bundled notifier app. Try it with
  `macmon sentinel --test-notify`.

```bash
macmon sentinel --install     # arm it (LaunchAgent, 60s sampler)
macmon sentinel               # tactical snapshot with gauges + sparklines
macmon sentinel --watch       # live console
macmon sentinel --status      # agent + config status
macmon sentinel --log         # recent alerts
```

Config lives in `~/.macmon/sentinel.conf` (JSON): thresholds and `auto_purge`.

---

## Architecture

```
macmon.py                CLI router (typer, 28+ commands)
modules/
  dashboard.py           Live TUI (rich) — 12 panels, keyboard shortcuts
  processes.py           Process manager, sweep, ports
  cleaner.py             System cleaner (junk, browsers, apps)
  gc.py                  Dev garbage collector
  security.py            Security scanner & IP blocking
  docker_mgr.py          Docker container/image/volume management
  autopilot.py           Daemon, thermal rules, security rules, focus mode
  health.py              Health check /100 & reports
  privacy.py             Privacy traces wiper
  startup.py             Startup/login items manager
  uninstaller.py         App uninstaller with leftover detection
  duplicates.py          Duplicate file finder (xxhash + SHA-256)
  disk.py                Disk analyzer & big file finder
  network.py             Network monitor
  config.py              TOML config manager
  utils.py               Shared utilities, SQLite DB, logging
```

## Tech Stack

<p>
  <img src="https://img.shields.io/badge/Python-575ECF?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Rich_TUI-575ECF?style=flat-square&logo=gnometerminal&logoColor=white" alt="Rich">
  <img src="https://img.shields.io/badge/Typer_CLI-575ECF?style=flat-square&logo=gnometerminal&logoColor=white" alt="Typer">
  <img src="https://img.shields.io/badge/psutil-575ECF?style=flat-square&logo=python&logoColor=white" alt="psutil">
  <img src="https://img.shields.io/badge/SQLite-575ECF?style=flat-square&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/macOS_native-575ECF?style=flat-square&logo=apple&logoColor=white" alt="macOS">
</p>

## Config

Config lives at `~/.macmon/config.toml`:

```bash
macmon config --edit
```

Key sections: `dashboard`, `thresholds`, `cleaner`, `privacy`, `gc`, `focus_mode`, `notifications`, `autopilot`.

## License

MIT — see [LICENSE](LICENSE). Free forever, for any use.

## FAQ

**Is macmon really free?** Yes — MIT licensed, no account, no telemetry, no paid tier.

**Does it send my data anywhere?** No. Everything runs locally on your Mac. There is zero network telemetry.

**Is it a CCleaner alternative for Mac?** Yes — it covers the same ground (caches, logs, temp, browser data, privacy traces) plus developer-specific cleanup (`node_modules`, venvs, Docker, Xcode) and safe Trash-first deletes.

**Will it delete something important?** Deletes move to the Trash by default and never silently escalate to permanent removal. Destructive categories are opt-in.

**Which macOS versions?** macOS 12 (Monterey) and newer, on Apple Silicon and Intel. Python 3.11+.

---

<sub><strong>Keywords:</strong> mac cleaner · macOS system monitor · clean my Mac · free CCleaner alternative macOS · free up disk space Mac · why is my Mac slow · macOS terminal system cleaner · developer Mac tools · node_modules cleaner · Docker disk cleanup Mac · macOS privacy cleaner · macOS malware scanner CLI · Activity Monitor alternative · Mac optimizer open source · Python Typer Rich TUI · zero telemetry mac utility</sub>

---

<p align="center">
  <sub>Built with purpose by <strong><a href="https://soclose.co">SoClose</a></strong> — Digital Innovation Through Automation & AI</sub>
</p>
