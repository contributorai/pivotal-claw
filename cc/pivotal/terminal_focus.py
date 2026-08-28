"""Locate live Claude/Codex CLI sessions and focus their Terminal window.

Claude Code writes a ``~/.claude/sessions/<pid>.json`` file for every running
process, which gives an exact pid -> session_id mapping. ``ps`` turns the pid
into a tty, and AppleScript selects the Terminal tab attached to that tty.
Codex has no equivalent registry, so its sessions are matched by scanning
``ps`` output for the session id in the command line (covers ``codex resume``
launches).
"""

from __future__ import annotations

from pathlib import Path
import json
import re
import subprocess

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

FOCUS_TTY_SCRIPT = """
on run argv
  set targetTty to item 1 of argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if (tty of t) is targetTty then
          set selected of t to true
          set index of w to 1
          set frontmost of w to true
          activate
          return "focused"
        end if
      end repeat
    end repeat
  end tell
  return "not-found"
end run
"""


def _run_ps(runner) -> list[tuple[int, str, str]]:
    completed = runner(
        ["ps", "-axo", "pid=,tty=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    rows = []
    for line in (completed.stdout or "").splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_text, tty, command = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        rows.append((pid, tty, command))
    return rows


def _tty_processes(runner) -> list[tuple[int, str, str]]:
    return [(pid, tty, command) for pid, tty, command in _run_ps(runner) if tty.startswith("tty")]


def list_running_claude_sessions(claude_home: Path | None = None, runner=subprocess.run) -> dict[str, dict]:
    """Map session_id -> {pid, tty, cwd} for live tty-attached Claude processes."""
    home = (claude_home or Path.home() / ".claude").expanduser()
    registry = {}
    for entry in sorted((home / "sessions").glob("*.json")):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = record.get("pid")
        session_id = record.get("sessionId")
        if isinstance(pid, int) and isinstance(session_id, str) and session_id:
            registry[pid] = {"session_id": session_id, "cwd": record.get("cwd")}
    running = {}
    for pid, tty, command in _tty_processes(runner):
        entry = registry.get(pid)
        if entry is None or "claude" not in command.lower():
            continue
        running[entry["session_id"]] = {"pid": pid, "tty": tty, "cwd": entry.get("cwd")}
    return running


def list_running_codex_sessions(runner=subprocess.run) -> dict[str, dict]:
    """Best-effort session_id -> {pid, tty} for codex processes with the id in argv."""
    running = {}
    for pid, tty, command in _tty_processes(runner):
        if "codex" not in command.lower():
            continue
        match = UUID_RE.search(command)
        if match:
            running[match.group(0).lower()] = {"pid": pid, "tty": tty}
    return running


def list_running_sessions(claude_home: Path | None = None, runner=subprocess.run) -> dict[tuple[str, str], dict]:
    combined = {}
    for session_id, info in list_running_claude_sessions(claude_home, runner).items():
        combined[("claude", session_id)] = info
    for session_id, info in list_running_codex_sessions(runner).items():
        combined[("codex", session_id)] = info
    return combined


def focus_tty(tty: str, runner=subprocess.run) -> bool:
    """Bring the Terminal tab attached to ``tty`` (e.g. ``ttys003``) to the front."""
    device = tty if tty.startswith("/dev/") else f"/dev/{tty}"
    completed = runner(
        ["osascript", "-e", FOCUS_TTY_SCRIPT, device],
        capture_output=True,
        text=True,
        check=False,
    )
    return getattr(completed, "returncode", 1) == 0 and "focused" in (completed.stdout or "")


def focus_session(provider: str, session_id: str, claude_home: Path | None = None, runner=subprocess.run) -> bool:
    """Focus the Terminal window of a running session. Returns False if not running."""
    if provider == "claude":
        info = list_running_claude_sessions(claude_home, runner).get(session_id)
    elif provider == "codex":
        info = list_running_codex_sessions(runner).get(session_id.lower())
    else:
        info = None
    if not info:
        return False
    return focus_tty(info["tty"], runner)
