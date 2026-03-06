"""Shared utilities for macmon."""

import logging
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm
from rich.text import Text

MACMON_DIR = Path.home() / ".macmon"
CONFIG_PATH = MACMON_DIR / "config.toml"
DB_PATH = MACMON_DIR / "macmon.db"
LOG_PATH = MACMON_DIR / "macmon.log"
REPORTS_DIR = MACMON_DIR / "reports"

console = Console()
err_console = Console(stderr=True)


def ensure_dirs():
    MACMON_DIR.mkdir(exist_ok=True)
    REPORTS_DIR.mkdir(exist_ok=True)


def get_logger(name: str = "macmon") -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = RotatingFileHandler(
            LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    return logger


logger = get_logger()


def get_db() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_db(conn)
    return conn


def _init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_type TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            total_size INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            freed_size INTEGER DEFAULT 0,
            details TEXT
        );
        CREATE TABLE IF NOT EXISTS autopilot_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            rule_name TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            cooldown_until TEXT
        );
        CREATE TABLE IF NOT EXISTS focus_session (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            killed_apps TEXT
        );
    """)
    conn.commit()


def format_size(size_bytes: int) -> str:
    if size_bytes < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_bytes) < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} PB"


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    elif seconds < 86400:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h {m}m"
    else:
        d = int(seconds // 86400)
        h = int((seconds % 86400) // 3600)
        return f"{d}d {h}h"


def confirm_action(message: str, default: bool = False, force_yes: bool = False) -> bool:
    if force_yes:
        return True
    return Confirm.ask(message, default=default)


def send_notification(title: str, message: str, style: str = "osascript"):
    if style == "osascript":
        try:
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{message}" with title "{title}"',
                ],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
    elif style == "terminal-notifier":
        try:
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", message],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


def log_action(action: str, details: str = ""):
    logger.info(f"{action}: {details}" if details else action)


def run_cmd(cmd: list[str], sudo: bool = False, timeout: int = 30) -> tuple[str, str, int]:
    if sudo:
        cmd = ["sudo"] + cmd
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}", -2


def dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total


def safe_stat(path: Path):
    try:
        return path.stat()
    except (OSError, PermissionError):
        return None


def get_process_categories() -> dict[str, list[str]]:
    return {
        "llm": ["claude", "ollama", "llm", "copilot"],
        "ide": ["code helper", "code", "electron", "cursor", "zed", "xcode", "nova", "idea", "webstorm", "pycharm", "goland"],
        "browser": ["chrome", "safari", "firefox", "arc", "brave", "opera", "edge", "chromium"],
        "docker": ["docker", "com.docker"],
        "node": ["node", "npm", "bun", "deno", "vite", "webpack", "esbuild", "turbo", "pnpm", "yarn"],
        "python": ["python", "python3", "uvicorn", "gunicorn", "celery", "fastapi", "flask", "django"],
        "build": ["make", "cargo", "go", "gradle", "bazel", "ninja", "cmake", "rustc", "gcc", "clang"],
        "jvm": ["java", "kotlin", "scala", "gradle"],
    }


CATEGORY_EMOJI = {
    "llm": "\U0001f916",
    "ide": "\U0001f4bb",
    "browser": "\U0001f310",
    "docker": "\U0001f433",
    "node": "\U0001f4e6",
    "python": "\U0001f40d",
    "build": "\U0001f527",
    "jvm": "\u2615",
    "other": "\u2699\ufe0f",
}


SHORT_KEYWORDS = {"go", "bun", "arc", "npm", "zed", "code", "node", "make"}


def categorize_process(name: str) -> str:
    name_lower = name.lower()
    for category, keywords in get_process_categories().items():
        for kw in keywords:
            if kw in SHORT_KEYWORDS:
                # Exact match or process name starts with keyword
                if name_lower == kw or name_lower.startswith(kw + " ") or name_lower.startswith(kw + "-"):
                    return category
            elif kw in name_lower:
                return category
    return "other"


def smart_suggestions(
    cpu_percent: float = 0,
    ram_percent: float = 0,
    zombie_count: int = 0,
    orphan_count: int = 0,
) -> list[str]:
    tips = []
    if ram_percent > 85:
        tips.append(
            "Memory pressure high -- run `macmon purge` or close browser tabs"
        )
    if zombie_count > 0 or orphan_count > 0:
        tips.append(
            f"{zombie_count} zombies + {orphan_count} orphans -- run `macmon sweep`"
        )
    if cpu_percent > 85:
        tips.append("CPU load high -- check `macmon ps --sort cpu` for hogs")
    return tips[:3]
