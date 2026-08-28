from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time


TAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_]+\b")
ID_RE = re.compile(r"\s*\bID:\s*(?:t:[a-z0-9]{7}|T[-A-Za-z0-9]+)\b")
COMPLETION_RE = re.compile(r"\s*(?:✓|Completed)\s*\d{4}-\d{2}-\d{2}\b")


@dataclass(frozen=True)
class ThreadMetadata:
    thread_id: str
    cwd: Path
    started_at: datetime
    path: Path


class AmbiguousThreadError(RuntimeError):
    pass


def clean_story_title(value: str) -> str:
    text = ID_RE.sub("", value)
    text = COMPLETION_RE.sub("", text)
    text = TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def story_slug(value: str, max_length: int = 40) -> str:
    cleaned = clean_story_title(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return slug[:max_length].rstrip("-") or "story"


def build_story_prompt(story: dict, project_dir: Path) -> str:
    title = clean_story_title(str(story.get("display_text") or story.get("text") or "Story"))
    tags = " ".join(f"#{tag}" for tag in story.get("tags", [])) or "None"
    children = story.get("children", []) or []
    child_lines = [
        f"- {clean_story_title(str(child.get('display_text') or child.get('text') or 'Subtask'))} — {child.get('status', 'Unknown')}"
        for child in children
    ]
    subtasks = "\n".join(child_lines) if child_lines else "- None"
    return f"""# {title}

Work only on this selected Pivotal story.

## Story

- ID: {story.get('task_id', '')}
- Status: {story.get('status', '')}
- Tags: {tags}
- Source: {story.get('source_file', '')}
- Section: {story.get('section', '') or 'None'}
- Project directory: {project_dir}

## Subtasks

{subtasks}

## Working agreement

Read AGENTS.md before changing files; preserve unrelated changes. Implement only this story, use test-first development, run relevant verification, and update Pivotal story state only through the project's normal workflow.
"""


def write_launch_script(
    prompt_text: str,
    cwd: Path,
    story_text: str,
    sessions_dir: Path,
    *,
    timestamp: str | None = None,
    codex_executable: Path | str | None = None,
) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = sessions_dir / f"launch-{story_slug(story_text)}-{stamp}.sh"
    source = "\n".join(
        [
            "#!/bin/bash",
            "set -euo pipefail",
            f"exec {shlex.quote(str(codex_executable or resolve_codex_executable()))} --cd {shlex.quote(str(cwd))} {shlex.quote(prompt_text)}",
            "",
        ]
    )
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def resolve_codex_executable() -> Path:
    discovered = shutil.which("codex")
    candidates = [Path(discovered)] if discovered else []
    candidates.append(Path("/Applications/Codex.app/Contents/Resources/codex"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Codex CLI executable not found")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_thread_metadata(path: Path) -> ThreadMetadata | None:
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    return None
                if record.get("type") != "session_meta":
                    continue
                payload = record.get("payload", {})
                thread_id = str(payload.get("id", "")).strip()
                cwd = str(payload.get("cwd", "")).strip()
                timestamp = str(payload.get("timestamp") or record.get("timestamp") or "").strip()
                if not thread_id or not cwd or not timestamp:
                    return None
                return ThreadMetadata(thread_id, Path(cwd).resolve(), parse_timestamp(timestamp), path)
    except (OSError, ValueError):
        return None
    return None


def iter_thread_metadata(sessions_root: Path):
    if not sessions_root.exists():
        return
    for path in sessions_root.rglob("*.jsonl"):
        metadata = read_thread_metadata(path)
        if metadata is not None:
            yield metadata


def snapshot_thread_ids(sessions_root: Path) -> set[str]:
    return {metadata.thread_id for metadata in iter_thread_metadata(sessions_root)}


def find_new_thread(
    sessions_root: Path,
    known_ids: set[str],
    cwd: Path,
    launched_at: datetime,
) -> ThreadMetadata | None:
    resolved_cwd = cwd.resolve()
    matches = [
        metadata
        for metadata in iter_thread_metadata(sessions_root)
        if metadata.thread_id not in known_ids
        and metadata.cwd == resolved_cwd
        and metadata.started_at >= launched_at
    ]
    if len(matches) > 1:
        raise AmbiguousThreadError(f"Found {len(matches)} matching new Codex threads")
    return matches[0] if matches else None


def wait_for_new_thread(
    sessions_root: Path,
    known_ids: set[str],
    cwd: Path,
    launched_at: datetime,
    *,
    timeout: float = 30.0,
    interval: float = 0.25,
    monotonic=time.monotonic,
    sleep=time.sleep,
) -> ThreadMetadata:
    deadline = monotonic() + timeout
    while True:
        match = find_new_thread(sessions_root, known_ids, cwd, launched_at)
        if match is not None:
            return match
        if monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for a new Codex thread")
        sleep(interval)


class CodexRuntime:
    def __init__(
        self,
        *,
        codex_home: Path | None = None,
        launch_dir: Path | None = None,
        runner=subprocess.run,
        codex_executable: Path | str | None = None,
    ):
        self.codex_home = (codex_home or Path.home() / ".codex").expanduser()
        self.launch_dir = (launch_dir or Path.home() / ".acc" / "codex" / "sessions").expanduser()
        self.runner = runner
        self.codex_executable = Path(codex_executable).expanduser() if codex_executable is not None else None

    def _resolve_executable(self) -> Path:
        return self.codex_executable or resolve_codex_executable()

    def preview(self, story: dict, project_dir: Path) -> str:
        return build_story_prompt(story, project_dir)

    def start(self, story: dict, project_dir: Path) -> dict:
        launched_at = datetime.now(timezone.utc)
        known_ids = snapshot_thread_ids(self.codex_home / "sessions")
        prompt = self.preview(story, project_dir)
        script = write_launch_script(
            prompt, project_dir, story.get("display_text") or story.get("text", "story"), self.launch_dir,
            codex_executable=self._resolve_executable(),
        )
        command = f'tell application "Terminal" to do script {json.dumps(str(script))}'
        completed = self.runner(["osascript", "-e", 'tell application "Terminal" to activate', "-e", command], check=False)
        if getattr(completed, "returncode", 0):
            raise RuntimeError("Terminal could not launch Codex")
        return {"known_ids": known_ids, "launched_at": launched_at, "project_dir": project_dir, "script": script}

    def wait(self, context: dict) -> ThreadMetadata:
        return wait_for_new_thread(
            self.codex_home / "sessions",
            context["known_ids"],
            Path(context["project_dir"]),
            context["launched_at"],
        )

    def resume(self, session_id: str, cwd: Path) -> None:
        shell = (
            f"cd {shlex.quote(str(cwd))} && "
            f"exec {shlex.quote(str(self._resolve_executable()))} resume {shlex.quote(session_id)}"
        )
        command = f'tell application "Terminal" to do script {json.dumps(shell)}'
        completed = self.runner(["osascript", "-e", 'tell application "Terminal" to activate', "-e", command], check=False)
        if getattr(completed, "returncode", 0):
            raise RuntimeError("Terminal could not resume Codex")
