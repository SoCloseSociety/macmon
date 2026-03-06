"""Config manager for macmon."""

import subprocess
import sys
from pathlib import Path

from rich.panel import Panel
from rich.syntax import Syntax

from .utils import CONFIG_PATH, MACMON_DIR, console, ensure_dirs, log_action

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

DEFAULT_CONFIG = """\
# macmon configuration
# ~/.macmon/config.toml

[dashboard]
refresh_seconds = 2
max_processes = 20

[thresholds]
cpu_warn = 70
cpu_critical = 90
ram_warn = 70
ram_critical = 88
disk_warn_gb = 15
disk_critical_gb = 5

[cleaner]
log_max_age_days = 7
stale_project_days = 14
safe_delete = true

[privacy]
shell_history_keep_lines = 0

[gc]
node_modules_stale_days = 14
venv_stale_days = 14
docker_image_stale_days = 30

[focus_mode]
essential_apps = ["code", "claude", "kitty", "iterm2", "terminal", "wezterm", "alacritty"]
kill_on_focus = ["spotify", "slack", "discord", "zoom", "teams", "messages", "mail"]

[dev_ports]
watch = [3000, 3001, 4000, 5000, 5173, 8000, 8080, 8888, 9000, 9229]

[notifications]
style = "osascript"

[autopilot]
enabled = true
interval_seconds = 30
"""


def load_config() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        init_config()
    try:
        if tomllib:
            with open(CONFIG_PATH, "rb") as f:
                return tomllib.load(f)
        else:
            # Fallback: parse basic TOML manually for key settings
            return _parse_basic_toml(CONFIG_PATH)
    except Exception as e:
        console.print(f"[yellow]Warning: Could not parse config: {e}[/yellow]")
        return _defaults()


def _defaults() -> dict:
    return {
        "dashboard": {"refresh_seconds": 2, "max_processes": 20},
        "thresholds": {
            "cpu_warn": 70, "cpu_critical": 90,
            "ram_warn": 70, "ram_critical": 88,
            "disk_warn_gb": 15, "disk_critical_gb": 5,
        },
        "cleaner": {"log_max_age_days": 7, "stale_project_days": 14, "safe_delete": True},
        "privacy": {"shell_history_keep_lines": 0},
        "gc": {"node_modules_stale_days": 14, "venv_stale_days": 14, "docker_image_stale_days": 30},
        "focus_mode": {
            "essential_apps": ["code", "claude", "kitty", "iterm2", "terminal"],
            "kill_on_focus": ["spotify", "slack", "discord", "zoom", "teams"],
        },
        "dev_ports": {"watch": [3000, 3001, 4000, 5000, 5173, 8000, 8080, 8888, 9000, 9229]},
        "notifications": {"style": "osascript"},
        "autopilot": {"enabled": True, "interval_seconds": 30},
    }


def _parse_basic_toml(path: Path) -> dict:
    """Very basic TOML parser fallback."""
    config = _defaults()
    return config


def init_config():
    ensure_dirs()
    if CONFIG_PATH.exists():
        console.print(f"[yellow]Config already exists at {CONFIG_PATH}[/yellow]")
        return
    CONFIG_PATH.write_text(DEFAULT_CONFIG)
    console.print(f"[green]Created default config at {CONFIG_PATH}[/green]")
    log_action("config_init", str(CONFIG_PATH))


def show_config():
    if not CONFIG_PATH.exists():
        console.print("[yellow]No config found. Run `macmon config --init` first.[/yellow]")
        return
    content = CONFIG_PATH.read_text()
    syntax = Syntax(content, "toml", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="macmon config", border_style="cyan"))


def set_config(key: str, value: str):
    if not CONFIG_PATH.exists():
        init_config()
    content = CONFIG_PATH.read_text()
    lines = content.split("\n")
    found = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(key) and "=" in stripped:
            k = stripped.split("=")[0].strip()
            if k == key:
                # Try to preserve type
                if value.lower() in ("true", "false"):
                    lines[i] = f"{key} = {value.lower()}"
                elif value.isdigit():
                    lines[i] = f"{key} = {value}"
                else:
                    lines[i] = f'{key} = "{value}"'
                found = True
                break
    if not found:
        console.print(f"[yellow]Key '{key}' not found in config.[/yellow]")
        return
    CONFIG_PATH.write_text("\n".join(lines))
    console.print(f"[green]Set {key} = {value}[/green]")
    log_action("config_set", f"{key}={value}")


def edit_config():
    import os
    editor = os.environ.get("EDITOR", "nano")
    if not CONFIG_PATH.exists():
        init_config()
    subprocess.run([editor, str(CONFIG_PATH)])
