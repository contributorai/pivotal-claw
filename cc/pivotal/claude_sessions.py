from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import shlex
import shutil
import subprocess
import time
import uuid

try:
    from cc.pivotal.codex_sessions import build_story_prompt, story_slug
except ModuleNotFoundError:
    from codex_sessions import build_story_prompt, story_slug


def encode_cwd(cwd: Path | str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(cwd))


def resolve_claude_executable() -> Path:
    discovered = shutil.which("claude")
    if discovered and Path(discovered).is_file():
        return Path(discovered).resolve()
    raise FileNotFoundError("Claude CLI executable not found")


def write_launch_script(
    prompt_text: str,
    cwd: Path,
    story_text: str,
    session_id: str,
    sessions_dir: Path,
    *,
    timestamp: str | None = None,
    claude_executable: Path | str | None = None,
) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = sessions_dir / f"launch-{story_slug(story_text)}-{stamp}.sh"
    source = "\n".join(
        [
            "#!/bin/bash",
            "set -euo pipefail",
            f"cd {shlex.quote(str(cwd))}",
            f"exec {shlex.quote(str(claude_executable or resolve_claude_executable()))} "
            f"--session-id {shlex.quote(session_id)} {shlex.quote(prompt_text)}",
            "",
        ]
    )
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    cwd: Path
    path: Path


def session_file_path(claude_home: Path, cwd: Path, session_id: str) -> Path:
    return claude_home / "projects" / encode_cwd(cwd.resolve()) / f"{session_id}.jsonl"


def find_session_file(claude_home: Path, cwd: Path, session_id: str) -> SessionMetadata | None:
    path = session_file_path(claude_home, cwd, session_id)
    if not path.is_file():
        return None
    return SessionMetadata(session_id, cwd.resolve(), path)


def wait_for_session_file(
    claude_home: Path,
    cwd: Path,
    session_id: str,
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> SessionMetadata:
    deadline = monotonic() + timeout
    while True:
        match = find_session_file(claude_home, cwd, session_id)
        if match is not None:
            return match
        if monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for the new Claude session file")
        sleep(interval)


class ClaudeRuntime:
    def __init__(self, *, claude_home: Path | None = None, launch_dir: Path | None = None, runner=subprocess.run):
        self.claude_home = (claude_home or Path.home() / ".claude").expanduser()
        self.launch_dir = (launch_dir or Path.home() / ".acc" / "claude" / "sessions").expanduser()
        self.runner = runner
        self.claude_executable = resolve_claude_executable()

    def preview(self, story: dict, project_dir: Path) -> str:
        return build_story_prompt(story, project_dir)

    def start(self, story: dict, project_dir: Path) -> dict:
        session_id = str(uuid.uuid4())
        prompt = self.preview(story, project_dir)
        script = write_launch_script(
            prompt, project_dir, story.get("display_text") or story.get("text", "story"), session_id,
            self.launch_dir, claude_executable=self.claude_executable,
        )
        command = f'tell application "Terminal" to do script {json.dumps(str(script))}'
        completed = self.runner(["osascript", "-e", 'tell application "Terminal" to activate', "-e", command], check=False)
        if getattr(completed, "returncode", 0):
            raise RuntimeError("Terminal could not launch Claude")
        return {"session_id": session_id, "project_dir": project_dir, "script": script}

    def wait(self, context: dict) -> SessionMetadata:
        return wait_for_session_file(
            self.claude_home,
            Path(context["project_dir"]),
            context["session_id"],
        )

    def resume(self, session_id: str, cwd: Path) -> None:
        shell = (
            f"cd {shlex.quote(str(cwd))} && "
            f"exec {shlex.quote(str(self.claude_executable))} --resume {shlex.quote(session_id)}"
        )
        command = f'tell application "Terminal" to do script {json.dumps(shell)}'
        completed = self.runner(["osascript", "-e", 'tell application "Terminal" to activate', "-e", command], check=False)
        if getattr(completed, "returncode", 0):
            raise RuntimeError("Terminal could not resume Claude")
