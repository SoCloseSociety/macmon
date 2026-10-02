"""Shared utilities for macmon."""

import logging
import os
import sqlite3
import stat
import subprocess
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm

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
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
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
    try:
        return Confirm.ask(message, default=default)
    except EOFError:
        return default


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_notification(title: str, message: str, style: str = "osascript"):
    # Off macOS, osascript does not exist: route to the platform notifier
    # (PowerShell toast on Windows, notify-send on Linux).
    from .platform_compat import IS_MAC
    from .platform_compat import notify as _os_notify
    if not IS_MAC:
        _os_notify(title, message)
        return
    if style == "osascript":
        try:
            msg = _applescript_escape(message)
            ttl = _applescript_escape(title)
            subprocess.run(
                [
                    "osascript", "-e",
                    f'display notification "{msg}" with title "{ttl}"',
                ],
                capture_output=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            pass
    elif style == "terminal-notifier":
        try:
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", message],
                capture_output=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            pass


def log_action(action: str, details: str = ""):
    logger.info(f"{action}: {details}" if details else action)


def _sudo_argv(cmd: list[str], sudo: bool) -> list[str]:
    """argv for a (possibly) privileged command. Every sudo invocation --
    ``sudo=True`` or an explicit ``"sudo"`` argv[0] -- carries ``-n``: with no
    cached credentials it fails at once ("a password is required") instead of
    blocking a windowed app or a scheduled job on a prompt nobody can see."""
    cmd = list(cmd)
    if sudo and cmd[:1] != ["sudo"]:
        cmd = ["sudo"] + cmd
    if cmd[:1] == ["sudo"] and cmd[1:2] != ["-n"]:
        cmd.insert(1, "-n")
    return cmd


def run_cmd(cmd: list[str], sudo: bool = False, timeout: int = 30) -> tuple[str, str, int]:
    """Run ``cmd`` (an argv list, never a shell) -> (stdout, stderr, rc).

    Never interactive: stdin is /dev/null (a child inheriting a closed or
    windowed stdin can block on a read forever) and sudo is always ``sudo -n``
    (see ``_sudo_argv``). rc -1 = timed out, -2 = executable not found."""
    cmd = _sudo_argv(cmd, sudo)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                stdin=subprocess.DEVNULL)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", -1
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}", -2


def admin_run(commands, timeout: int = 120) -> tuple[str, str, int]:
    """Run privileged command(s) through the macOS NATIVE authorization dialog
    (``osascript 'do shell script ... with administrator privileges'``), for a
    USER-INITIATED action only (a GUI button, an explicit CLI step).

    Unlike ``run_cmd(sudo=True)`` -- which uses ``sudo -n`` and must never block
    an unattended sampler on a prompt nobody can see -- this is the dialog the
    user expects, and the password they type actually authorizes and RUNS the
    command. That is the fix for "the security popup asks for my password but
    nothing happens": a windowed app cannot answer a ``sudo`` TTY prompt, so a
    privileged action has to go through this authorization path.

    ``commands`` is one argv list, or a list of argv lists run under a SINGLE
    authorization (joined with ``&&``). Each argument is shell-quoted, then the
    whole script is AppleScript-escaped -- no value is interpreted as a shell
    token or an AppleScript token. Returns (stdout, stderr, rc); rc != 0 if the
    user cancels (osascript -128) or a command fails. Off macOS: falls back to
    ``run_cmd(sudo=True)`` on the first command (no native dialog exists)."""
    import shlex
    from .platform_compat import IS_MAC
    if commands and isinstance(commands[0], str):
        commands = [commands]
    if not IS_MAC:
        out, err, rc = "", "", 0
        for c in commands:
            out, err, rc = run_cmd(list(c), sudo=True, timeout=timeout)
            if rc != 0:
                break
        return out, err, rc
    shell = " && ".join(" ".join(shlex.quote(str(a)) for a in c) for c in commands)
    script = 'do shell script "%s" with administrator privileges' % _applescript_escape(shell)
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "Authorization timed out (no response to the password dialog)", -1
    except FileNotFoundError:
        return "", "osascript not found", -2


def dir_size(path: Path) -> int:
    """Total bytes of the regular files under ``path``.

    One ``lstat`` per entry (``os.walk`` + ``S_ISREG``): the previous
    ``rglob`` + ``is_file()`` + ``is_symlink()`` + ``stat()`` form cost up to
    three stat calls per file -- a warm, alternating A/B on a static 170 MB
    venv tree (12k files) runs 160 ms -> 79 ms on 3.14 and 163 -> 77 ms on
    3.13; a 10 GB cache tree is I/O-bound and gains little. Same semantics:
    symlinks are neither followed nor counted (``os.walk`` does not descend a
    symlinked directory; a symlink to a file is not ``S_ISREG``), hardlinked
    files count once, unreadable subtrees are skipped."""
    total = 0
    seen_links: set[tuple[int, int]] = set()  # count hardlinked files once
    try:
        for root, _dirs, files in os.walk(path):
            for fname in files:
                try:
                    st = os.lstat(os.path.join(root, fname))
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    if key in seen_links:
                        continue
                    seen_links.add(key)
                total += st.st_size
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
