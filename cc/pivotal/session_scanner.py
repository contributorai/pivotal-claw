from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
HEAD_BYTE_CAP = 2_097_152
TAIL_BYTE_CAP = 524_288
TITLE_MAX_CHARS = 60
FIRST_PROMPT_MAX_CHARS = 200
MESSAGE_MAX_CHARS = 2000

SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
CLAUDE_NOISE_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<system-reminder>",
    "<task-notification>",
)
CODEX_NOISE_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions",
    "<turn_context",
    "<system-reminder>",
    "# AGENTS.md instructions",
    "# Files mentioned by the user",
    "The following is the Codex agent history",
)


def _truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _iter_head_json(path: Path, cap: int = HEAD_BYTE_CAP):
    consumed = 0
    with path.open("r", encoding="utf-8", errors="replace") as source:
        for line in source:
            consumed += len(line)
            line = line.strip()
            if line:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    record = None
                if isinstance(record, dict):
                    yield record
            if consumed >= cap:
                return


def _read_tail_lines(path: Path, cap: int = TAIL_BYTE_CAP) -> list[str]:
    size = path.stat().st_size
    offset = max(0, size - cap)
    with path.open("rb") as source:
        source.seek(offset)
        chunk = source.read(cap)
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    if offset > 0 and lines:
        lines.pop(0)
    return lines


def _iter_json(lines) -> list[dict]:
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _content_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"text", "input_text", "output_text"}:
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n".join(parts)
    return ""


def _is_noise(text: str, prefixes) -> bool:
    return not text or text.startswith(prefixes)


def _claude_user_text(record: dict) -> str:
    if record.get("type") != "user" or record.get("isMeta") or record.get("isSidechain"):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    text = _content_text(message.get("content"))
    return "" if _is_noise(text, CLAUDE_NOISE_PREFIXES) else text


def _claude_assistant_text(record: dict) -> str:
    if record.get("type") != "assistant" or record.get("isSidechain"):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _content_text(message.get("content"))


def _find_claude_ai_title(records) -> str:
    title = ""
    for record in records:
        if record.get("type") == "ai-title" and isinstance(record.get("aiTitle"), str):
            title = record["aiTitle"].strip()
    return title


def _parse_claude_head(path: Path) -> dict:
    title = ""
    first_prompt = ""
    cwd = ""
    git_branch = ""
    started_at = ""
    for record in _iter_head_json(path):
        if not title and record.get("type") == "ai-title" and isinstance(record.get("aiTitle"), str):
            title = record["aiTitle"].strip()
        if not started_at and isinstance(record.get("timestamp"), str):
            started_at = record["timestamp"]
        if not cwd and isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        if not git_branch and isinstance(record.get("gitBranch"), str):
            git_branch = record["gitBranch"]
        if not first_prompt:
            first_prompt = _claude_user_text(record)
        if title and first_prompt and cwd and started_at and git_branch:
            break
    return {
        "title": title,
        "first_prompt": first_prompt,
        "cwd": cwd,
        "git_branch": git_branch,
        "started_at": started_at,
    }


def _parse_claude_file(path: Path) -> dict | None:
    head = _parse_claude_head(path)
    title = head["title"]
    if not title:
        title = _find_claude_ai_title(_iter_json(_read_tail_lines(path, 16_384)))
    if not title and not head["first_prompt"]:
        return None
    return {
        "session_id": path.stem,
        "provider": "claude",
        "title": title or _truncate(head["first_prompt"], TITLE_MAX_CHARS),
        "first_prompt": _truncate(head["first_prompt"], FIRST_PROMPT_MAX_CHARS),
        "cwd": head["cwd"],
        "git_branch": head["git_branch"],
        "started_at": head["started_at"],
    }


def _codex_message_text(record: dict, roles=("user", "assistant")) -> tuple[str, str]:
    if record.get("type") != "response_item":
        return "", ""
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return "", ""
    role = payload.get("role", "")
    if role not in roles:
        return "", ""
    text = _content_text(payload.get("content"))
    if role == "user" and _is_noise(text, CODEX_NOISE_PREFIXES):
        return "", ""
    return role, text


def _parse_codex_file(path: Path) -> dict | None:
    payload = None
    first_prompt = ""
    for index, record in enumerate(_iter_head_json(path)):
        if index == 0:
            if record.get("type") != "session_meta" or not isinstance(record.get("payload"), dict):
                return None
            payload = record["payload"]
            meta_record = record
            continue
        role, text = _codex_message_text(record, roles=("user",))
        if role and text:
            first_prompt = text
            break
    if payload is None:
        return None
    session_id = str(payload.get("id") or payload.get("session_id") or "").strip()
    if not session_id:
        return None
    return {
        "session_id": session_id,
        "provider": "codex",
        "title": _truncate(first_prompt, TITLE_MAX_CHARS) or "(untitled)",
        "first_prompt": _truncate(first_prompt, FIRST_PROMPT_MAX_CHARS),
        "cwd": str(payload.get("cwd", "")),
        "git_branch": "",
        "started_at": str(payload.get("timestamp") or meta_record.get("timestamp") or ""),
    }


def _parse_session_file(provider: str, path: Path) -> dict | None:
    try:
        if provider == "claude":
            return _parse_claude_file(path)
        return _parse_codex_file(path)
    except OSError:
        return None


def _candidate_files(claude_dir: Path, codex_dir: Path) -> list[tuple[float, str, Path]]:
    candidates = []
    if claude_dir.is_dir():
        for path in claude_dir.glob("*/*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, "claude", path))
            except OSError:
                continue
    if codex_dir.is_dir():
        for path in codex_dir.rglob("*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, "codex", path))
            except OSError:
                continue
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates


def list_recent_sessions(limit: int = 25, claude_dir: Path | None = None, codex_dir: Path | None = None) -> list[dict]:
    claude_dir = Path(claude_dir or CLAUDE_PROJECTS_DIR)
    codex_dir = Path(codex_dir or CODEX_SESSIONS_DIR)
    sessions = []
    for mtime, provider, path in _candidate_files(claude_dir, codex_dir):
        if len(sessions) >= limit:
            break
        record = _parse_session_file(provider, path)
        if record is None:
            continue
        record["project"] = Path(record["cwd"]).name if record["cwd"] else ""
        record["modified_at"] = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        sessions.append(record)
    return sessions


def _find_session_file(provider: str, session_id: str, claude_dir: Path, codex_dir: Path) -> Path | None:
    if provider == "claude":
        matches = claude_dir.glob(f"*/{session_id}.jsonl") if claude_dir.is_dir() else []
    else:
        matches = codex_dir.rglob(f"*{session_id}.jsonl") if codex_dir.is_dir() else []
    for match in matches:
        return match
    return None


def read_session_preview(
    provider: str,
    session_id: str,
    claude_dir: Path | None = None,
    codex_dir: Path | None = None,
    max_messages: int = 10,
) -> dict | None:
    if provider not in {"claude", "codex"} or not SESSION_ID_RE.match(session_id or ""):
        return None
    claude_dir = Path(claude_dir or CLAUDE_PROJECTS_DIR)
    codex_dir = Path(codex_dir or CODEX_SESSIONS_DIR)
    path = _find_session_file(provider, session_id, claude_dir, codex_dir)
    if path is None:
        return None
    meta = _parse_session_file(provider, path)
    if meta is None:
        meta = {"session_id": session_id, "provider": provider, "title": "(untitled)", "first_prompt": ""}
    messages = []
    for record in _iter_json(_read_tail_lines(path)):
        if provider == "claude":
            text = _claude_user_text(record)
            role = "user" if text else ""
            if not text:
                text = _claude_assistant_text(record)
                role = "assistant" if text else ""
        else:
            role, text = _codex_message_text(record)
        if role and text:
            messages.append(
                {
                    "role": role,
                    "text": _truncate(text, MESSAGE_MAX_CHARS),
                    "timestamp": str(record.get("timestamp", "")),
                }
            )
    return {
        "session_id": meta["session_id"],
        "provider": provider,
        "title": meta["title"],
        "first_prompt": meta["first_prompt"],
        "messages": messages[-max_messages:],
    }
