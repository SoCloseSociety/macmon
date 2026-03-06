# macmon

**Mac Developer Monitor + System Cleaner CLI** — CCleaner Pro level, 100% local.

A powerful terminal-based monitoring and management tool for macOS. Live dashboard, system cleaner, security scanner, process manager, Docker management, and more — all from your terminal.

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![macOS 12+](https://img.shields.io/badge/macOS-12%2B-black)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

### Live Dashboard (`macmon`)
Full-screen TUI with real-time monitoring:
- CPU per-core with sparkline history
- RAM / Swap / Wired / Active breakdown
- Disk I/O rates
- Network upload/download with sparklines
- Battery status
- Thermal monitoring (CPU temp, fan RPM, throttle detection)
- Security score live panel
- Docker containers panel
- Process list with category icons (kill with `1-9` keys)
- Alerts & smart suggestions

**Keyboard shortcuts** (execute real actions, not just scans):

| Key | Action |
|-----|--------|
| `S` | Sweep: kill zombies, orphans, stale locks |
| `P` | Purge inactive RAM |
| `C` | Full system clean (junk, caches, browsers) |
| `G` | Dev garbage collector (node_modules, venvs) |
| `H` | Health check + auto-fix |
| `K` | Security audit |
| `D` | Docker overview |
| `F` | Focus mode (quit non-essentials, DND) |
| `1-9` | Kill process #N from the list |
| `Q` | Quit dashboard |

### System Cleaner (`macmon clean`)
- System junk (temp files, logs, crash reports)
- Browser cleaner (Chrome, Safari, Firefox, Arc, Brave, Opera, Edge, Chromium)
- App caches (Xcode, VSCode, Cursor, Slack, Spotify, JetBrains, etc.)
- User cache scanner (top 20 by size)
- Schedule via launchd
- Clipboard & recent items clear

### Dev Garbage Collector (`macmon gc`)
- npm / pnpm / yarn / bun caches
- Stale `node_modules` and `venvs`
- `__pycache__`, `.DS_Store`
- Homebrew, Docker, Xcode DerivedData
- iOS Simulators, Go cache, Cargo registry

### Process Manager (`macmon ps`)
- Process list with category detection (IDE, browser, Docker, Node, Python, etc.)
- Kill / suspend / resume / renice / quit / restart
- Zombie & orphan sweep
- Port manager with lsof fallback

### Privacy Cleaner (`macmon privacy`)
- Recent items, Finder history, QuickLook cache
- Shell history (zsh/bash/fish)
- REPL history (Python/Node/SQLite/IRB/MySQL/Redis)
- Spotlight suggestions, SSH known_hosts, Siri data

### Health Check (`macmon health`)
- 13+ system checks with score /100
- Auto-fix for safe issues
- Report saving

### Startup Manager (`macmon startup`)
- User/System LaunchAgents & LaunchDaemons
- Cron jobs
- Enable / disable / delete / audit suspicious

### App Uninstaller (`macmon uninstall`)
- Full leftover detection (15+ Library locations)
- Bundle ID detection via Info.plist
- Process kill + LaunchAgent unload

### Duplicate Finder (`macmon dupes`)
- 3-phase: size grouping, xxhash first 64KB, SHA-256 full
- Hardlink detection
- Auto-keep modes (newest/oldest/path)
- Empty directory & broken symlink finder

### Disk Analyzer (`macmon disk` / `macmon bigfiles`)
- File categorization (disk images, video, archives, VM images)
- Growth detection over time

### Network Monitor (`macmon network`)
- Active connections with process info
- Risk coloring
- DNS flush

### Security Scanner (`macmon security`)
- Security score /100
- Firewall, SIP, Gatekeeper, FileVault checks
- Suspicious connection detection (Metasploit, IRC, backdoor ports)
- Remote access tool detection (TeamViewer, AnyDesk, VNC, etc.)
- Malware indicators (crypto miners, /tmp executables)
- IP blocking via pf firewall
- Process quarantine

### Docker Manager (`macmon docker`)
- Container / image / volume / network listing
- Full prune, stop-all, restart, logs, live stats
- Compose project listing
- Security scan (privileged containers, host network, root, public ports)

### Autopilot Daemon (`macmon auto`)
Background daemon with automatic rules:
- RAM critical -> purge
- Zombie/orphan cleanup
- CPU runaway -> renice
- Browser RAM hog -> notification
- **Thermal management**: auto-renice on overheating, fan noise reduction
- **Security monitoring**: suspicious ports, remote tools, crypto miners, /tmp executables
- Low disk & weekly clean reminders

### Focus Mode (`macmon focus` / `macmon restore`)
- Quit non-essential apps
- Purge RAM
- Enable Do Not Disturb
- Restore everything on exit

## Installation

```bash
git clone https://github.com/SoCloseSociety/macmon.git
cd macmon
bash install.sh
```

This will:
1. Check Python 3.9+
2. Create a venv at `~/.macmon/venv`
3. Install dependencies
4. Create `/usr/local/bin/macmon` wrapper
5. Initialize config at `~/.macmon/config.toml`

### Build DMG (optional)

```bash
bash build_dmg.sh
open dist/macmon.dmg
```

## Requirements

- macOS 12+
- Python 3.9+

### Python dependencies

```
rich>=13.7.0
psutil>=5.9.0
typer[all]>=0.9.0
send2trash>=1.8.0
xxhash>=3.4.0
```

## Quick Start

```bash
# Launch the live dashboard
macmon

# System clean (full)
macmon clean --all -y

# Dev garbage collector
macmon gc --all -y

# Security audit
macmon security

# Health check + auto-fix
macmon health --fix

# Process list
macmon ps

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

## Command Reference

| Command | Description |
|---------|-------------|
| `macmon` | Live dashboard |
| `macmon ps` | Process list |
| `macmon kill <target>` | Kill process |
| `macmon suspend <target>` | Suspend process |
| `macmon resume <target>` | Resume process |
| `macmon nice <target> <val>` | Renice process |
| `macmon quit <app>` | Graceful quit |
| `macmon restart <app>` | Restart app |
| `macmon sweep` | Kill zombies/orphans |
| `macmon ports` | Port manager |
| `macmon clean --scan` | Preview cleanable junk |
| `macmon clean --all -y` | Full clean |
| `macmon clean --browsers` | Browser cleaner |
| `macmon gc --scan` | Preview dev garbage |
| `macmon gc --all -y` | Full dev GC |
| `macmon privacy --scan` | Preview privacy traces |
| `macmon privacy --full -y` | Wipe all traces |
| `macmon health` | Health check /100 |
| `macmon health --fix` | Auto-fix safe issues |
| `macmon startup --list` | List startup items |
| `macmon startup --audit` | Audit suspicious items |
| `macmon uninstall <app>` | Uninstall + leftovers |
| `macmon dupes <path>` | Find duplicates |
| `macmon bigfiles` | Find large files |
| `macmon disk` | Disk analyzer |
| `macmon network` | Network connections |
| `macmon flush-dns` | Flush DNS cache |
| `macmon security` | Security audit /100 |
| `macmon security --block-ip <ip>` | Block IP via pf |
| `macmon security --quarantine <proc>` | Kill + block process |
| `macmon docker` | Docker overview |
| `macmon docker --prune -y` | Docker full cleanup |
| `macmon auto --start` | Start autopilot |
| `macmon focus` | Enter focus mode |
| `macmon restore` | Exit focus mode |
| `macmon purge` | Purge RAM |
| `macmon report` | Session report |
| `macmon config --show` | Show config |

## Architecture

```
macmon.py              CLI router (typer)
modules/
  utils.py             Shared utilities, DB, logging
  config.py            TOML config manager
  dashboard.py         Live TUI (rich)
  processes.py         Process manager, sweep, ports
  cleaner.py           System cleaner
  gc.py                Dev garbage collector
  privacy.py           Privacy traces wiper
  health.py            Health check & reports
  startup.py           Startup/login items manager
  uninstaller.py       App uninstaller
  duplicates.py        Duplicate file finder
  disk.py              Disk analyzer & big file finder
  network.py           Network monitor
  security.py          Security scanner
  docker_mgr.py        Docker management
  autopilot.py         Daemon, focus mode, rules engine
```

## Config

Config lives at `~/.macmon/config.toml`. Edit with:

```bash
macmon config --edit
```

Key sections: `dashboard`, `thresholds`, `cleaner`, `privacy`, `gc`, `focus_mode`, `notifications`, `autopilot`.

## License

MIT
