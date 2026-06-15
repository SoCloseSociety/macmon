# NEO_CONNECTOR -- macmon
- service: macmoncli
- base_url_prod: N/A (no HTTP API -- local macOS CLI/TUI)
- auth: none (runs as the local user; some actions require sudo/admin privileges)
- env_required: []
- generated_at:

> Machine-readable connection manifest for NeoBot. Everything below is proven from
> code (`macmon.py` typer CLI + `modules/`). Do NOT edit by hand -- regenerate via the
> Neo Connector audit.
>
> **VERDICT: NO HTTP API. This project must NOT be wired as Neo HTTP tools.**
> macmon is a 100% local macOS developer-monitor + system-cleaner CLI built on
> `typer` (CLI) + `rich` (TUI). It exposes ZERO web server, HTTP endpoints, webhooks,
> SSE, WebSockets, or outbound HTTP calls. Verified by grep: no flask/fastapi/aiohttp/
> uvicorn/django/http.server/socket.listen/socket.bind/app.run/requests/urllib/httpx
> anywhere in `*.py` (the only "0.0.0.0" hit is a string parsed from `docker ps`
> output in `modules/docker_mgr.py:394`; the only framework names are a process-category
> keyword list in `modules/utils.py:185`). The "autopilot daemon" is a `os.fork()`
> background process tracked by a PID file (`~/.macmon/daemon.pid`), NOT a network
> listener -- see `modules/autopilot.py`.
>
> If Neo ever needs macmon, the ONLY integration path is shelling out to the `macmon`
> binary on the local machine where it is installed (e.g. via the Sentinel desktop
> body / run_shell), never an HTTP tool. The CLI command surface is documented below
> as the de-facto "interface".

## Endpoints
None. There is no HTTP/network endpoint of any kind.

This service is invoked exclusively via the local command line. The install wrapper
(`install.sh`) creates `/usr/local/bin/macmon` -> `~/.macmon/venv` running `macmon.py`.
Entrypoint: `app = typer.Typer(...)` in `macmon.py` with `@app.command()` decorators.

## CLI commands (the de-facto interface -- shell only, NOT HTTP)
Proven from `macmon.py` typer command definitions. Amounts/flags are CLI options, not
request bodies. `target` = process name or PID. `-y/--yes` skips confirmation.

| Command | Key args/options | Purpose |
|---|---|---|
| `macmon` (no subcommand) | `--refresh/-r <int=2>` | Launch live TUI dashboard |
| `macmon ps` | `--filter/-f`, `--sort/-s {cpu,ram,name,runtime}`, `--tree`, `--json` | Process list with categories |
| `macmon kill <target>` | `--category/-c`, `--yes/-y` | Kill process (or whole category) |
| `macmon suspend <target>` | -- | Suspend (SIGSTOP) a process |
| `macmon resume <target>` | -- | Resume (SIGCONT) a process |
| `macmon nice <target> <value>` | value -20..19 | Renice a process |
| `macmon quit <app_name>` | -- | Graceful quit an app |
| `macmon restart <app_name>` | -- | Restart an app |
| `macmon sweep` | `--zombies`, `--orphans`, `--yes/-y` | Kill zombies/orphans/stale locks |
| `macmon ports` | `--free <port>`, `--free-all-dev`, `--yes/-y` | Port manager |
| `macmon clean` | `--scan`, `--run`, `--all`, `--module/-m`, `--browsers`, `--all-browsers`, `--browser`, `--cookies`, `--cache`, `--clipboard`, `--recent`, `--schedule`, `--permanent`, `--yes/-y`, `--json` | System + browser + app-cache cleaner |
| `macmon gc` | `--scan`, `--all`, `--yes/-y` (+ more) | Dev garbage collector |
| `macmon privacy` | `--scan`, `--full`, `--yes/-y` | Wipe privacy traces |
| `macmon health` | `--fix` | Health check /100 (+ auto-fix) |
| `macmon startup` | `--list`, `--audit` | Startup/login items manager |
| `macmon uninstall <app>` | -- | Uninstall app + leftovers |
| `macmon dupes <path>` | -- | Duplicate file finder |
| `macmon bigfiles` | -- | Find large files |
| `macmon disk` | -- | Disk usage analyzer |
| `macmon network` | -- | Network connections monitor (read-only `lsof`/`netstat`) |
| `macmon flush-dns` | -- | Flush DNS cache |
| `macmon security` | `--block-ip <ip>`, `--quarantine <proc>` | Security audit /100 + pf-firewall blocking |
| `macmon docker` | `--prune`, `--scan`, `--yes/-y` | Docker overview / prune / security scan |
| `macmon auto` | `--start`, `--stop` | Start/stop autopilot daemon (fork + PID file) |
| `macmon focus` | -- | Focus mode (quit apps, DND) |
| `macmon restore` | -- | Exit focus mode |
| `macmon purge` | -- | Purge inactive RAM |
| `macmon report` | -- | Session report |
| `macmon config` | `--show`, `--edit` | View/edit TOML config |

> Some `--json` flags exist (`ps`, `clean`) -- these print JSON to stdout, which is the
> only structured/machine-readable output. Useful if Neo shells out and parses stdout.

## Local state / config (not network)
- Config: `~/.macmon/config.toml` (`modules/config.py`). Sections: `dashboard`,
  `thresholds`, `cleaner`, `privacy`, `gc`, `focus_mode`, `notifications`, `autopilot`.
- Runtime DB + logs: SQLite `*.db` + `*.log` under `~/.macmon/` (`modules/utils.py`),
  gitignored.
- Daemon PID: `~/.macmon/daemon.pid` (`modules/autopilot.py`).
- Install wrapper: `/usr/local/bin/macmon` (`install.sh`).

## Flows
No async generate->poll->fetch flows (no API). The only background flow is the local
autopilot daemon:
1. `macmon auto --start` -> `os.fork()` detaches a background process, writes PID to
   `~/.macmon/daemon.pid`. The child runs a local rules engine (RAM/CPU/thermal/security)
   polling `psutil`. No port, no socket.
2. `macmon auto --stop` -> reads `~/.macmon/daemon.pid`, sends signal to terminate.

## Gaps
- None material for connection purposes: the absence of any HTTP/network surface is
  definitive, not "unknown". Verified across all of `macmon.py` + `modules/*.py`.
- Exact field shapes of `--json` outputs (`ps`, `clean`) not enumerated here -- if Neo
  needs to parse them, inspect `modules/processes.py` (ps JSON) and
  `modules/cleaner.py` (clean JSON) for the dict structure printed to stdout.
