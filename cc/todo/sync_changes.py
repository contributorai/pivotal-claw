from __future__ import annotations

from datetime import date
from pathlib import Path
import fcntl
import os
import random
import re
import shutil
import string
import tempfile


TODO_DIR = Path(__file__).resolve().parent
BACKUP_DIR_NAME = ".sync_backups"
STATUS_FILES = {
    "pending": "todo.md",
    "in progress": "doing.md",
    "done": "done.md",
    "on schedule": "icebox.md",
    "backlog": "icebox.md",
    "later": "icebox.md",
    "someday": "icebox.md",
}
ID_RE = re.compile(r"\bID:\s*(t:[a-z0-9]{7})\b")
BROAD_ID_RE = re.compile(r"\bID:\s*(?:t:[a-z0-9]{7}|T[-A-Za-z0-9]+)\b")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]+)\b")
TASK_RE = re.compile(r"^(\s*)([-*+]\s+\[[ xX/\-]\]\s+|[-*+]\s+|[✓✅]\s*)(.*?)(\n?)$")
COMPLETED_RE = re.compile(r"\s*(?:✓|Completed)\s*\d{4}-\d{2}-\d{2}\s*")


def normalize(text: str) -> str:
    text = TASK_RE.sub(r"\3", text.strip())
    text = BROAD_ID_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = COMPLETED_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def find_line_by_id(lines: list[str], task_id: str) -> int:
    if not task_id:
        return -1
    pattern = re.compile(rf"\bID:\s*{re.escape(task_id)}\b")
    for idx, line in enumerate(lines):
        if pattern.search(line):
            return idx
    return -1


def locate(lines: list[str], task_id: str, expected_text: str) -> tuple[int, bool]:
    """Find a task's line by its stable ID and check it hasn't drifted.

    Returns (index, conflict). index is -1 if the ID isn't present in this
    file at all (moved/deleted elsewhere). conflict is True if the ID was
    found but its current normalized text no longer matches expected_text
    (i.e. it changed since the client last saw it).
    """
    idx = find_line_by_id(lines, task_id)
    if idx == -1:
        return -1, False
    expected = normalize(expected_text or "")
    if expected and normalize(lines[idx]) != expected:
        return idx, True
    return idx, False


def parse_line_parts(line: str) -> tuple[str, str, str, str]:
    match = TASK_RE.match(line)
    if not match:
        return "", "- [ ] ", line.rstrip("\n"), "\n" if line.endswith("\n") else ""
    return match.group(1), match.group(2), match.group(3).strip(), match.group(4) or "\n"


def status_marker(status: str | None) -> str:
    normalized = (status or "Pending").lower()
    if normalized == "done":
        return "- [x] "
    if normalized == "in progress":
        return "- [/] "
    return "- [ ] "


def rebuild_line(
    original: str,
    new_status: str | None = None,
    new_text: str | None = None,
    added_tags: list[str] | None = None,
    today: str | None = None,
    removed_tags: list[str] | None = None,
) -> str:
    indent, marker, rest, newline = parse_line_parts(original)
    task_id_match = ID_RE.search(rest)
    task_id = task_id_match.group(1) if task_id_match else ""
    dropped = {tag.lstrip("#") for tag in removed_tags or [] if tag}
    old_tags = [tag for tag in TAG_RE.findall(rest) if tag not in dropped]
    text_body = new_text.strip() if new_text is not None else rest
    if new_text is not None:
        text_body = BROAD_ID_RE.sub("", text_body)
    text_body = COMPLETED_RE.sub("", text_body)
    for tag in dropped:
        text_body = re.sub(rf"(?<!\w)#{re.escape(tag)}\b", "", text_body)
    if dropped:
        text_body = re.sub(r"\s+", " ", text_body).strip()
    for tag in old_tags:
        if not re.search(rf"(?<!\w)#{re.escape(tag)}\b", text_body):
            text_body = f"{text_body.rstrip()} #{tag}"
    for tag in added_tags or []:
        clean = tag.lstrip("#")
        if clean and clean not in TAG_RE.findall(text_body):
            if ID_RE.search(text_body):
                text_body = ID_RE.sub(lambda match: f"{match.group(0)} #{clean}", text_body, count=1)
            else:
                text_body = f"{text_body.rstrip()} #{clean}"
    if task_id:
        if new_text is not None:
            text_body = f"{text_body.rstrip()} ID: {task_id}"
        elif not ID_RE.search(text_body):
            text_body = f"{text_body.rstrip()} ID: {task_id}"
    if new_status and new_status.lower() == "done":
        text_body = f"{text_body.rstrip()} Completed {today or date.today().isoformat()}"
    return f"{indent}{status_marker(new_status) if new_status else marker}{re.sub(r'\\s+', ' ', text_body).strip()}{newline}"


def generate_task_id(existing: set[str] | None = None) -> str:
    existing = existing or set()
    alphabet = string.ascii_lowercase + string.digits
    while True:
        task_id = "t:" + "".join(random.choices(alphabet, k=7))
        if task_id not in existing:
            return task_id


def collect_existing_ids(todo_dir: Path) -> set[str]:
    existing: set[str] = set()
    for name in ("todo.md", "doing.md", "done.md", "icebox.md"):
        path = Path(todo_dir) / name
        if path.exists():
            existing.update(ID_RE.findall(path.read_text(encoding="utf-8")))
    return existing


def build_new_item_line(text: str, status: str, tags: list[str] | None = None, existing: set[str] | None = None) -> str:
    line = text.strip()
    for tag in tags or []:
        clean = tag.lstrip("#")
        if clean and clean not in TAG_RE.findall(line):
            line = f"{line} #{clean}"
    if not ID_RE.search(line):
        line = f"{line} ID: {generate_task_id(existing)}"
    return f"{status_marker(status)}{line}\n"


def resolve_source(path: str | Path, todo_dir: Path) -> Path:
    source = Path(path)
    if not source.is_absolute():
        source = todo_dir / source
    source = source.resolve()
    todo_root = todo_dir.resolve()
    try:
        source.relative_to(todo_root)
    except ValueError as exc:
        raise ValueError(f"source outside todo_dir: {source}") from exc
    return source


def destination_for(status: str, todo_dir: Path) -> Path:
    return todo_dir / STATUS_FILES.get((status or "Pending").lower(), "todo.md")


def backup_file(path: Path, todo_dir: Path) -> None:
    backup_dir = todo_dir / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, backup_dir / f"{path.name}.{date.today().isoformat()}.bak")


def write_lines_locked(path: Path, mutate) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    with open(path, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            lines = handle.readlines()
            result = mutate(lines)
            if isinstance(result, tuple):
                ok, reason = result
            else:
                ok, reason = bool(result), None
            if ok:
                fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                        tmp.writelines(lines)
                    os.replace(temp_name, path)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
            return ok, reason
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append_locked(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.write(line)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def prepend_locked(path: Path, line: str) -> tuple[bool, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)

    def mutate(lines: list[str]):
        idx = next((i for i, l in enumerate(lines) if TASK_RE.match(l)), len(lines))
        lines.insert(idx, line)
        return True, None

    return write_lines_locked(path, mutate)


EPIC_HEADING_RE = re.compile(r"^#+\s+#([A-Za-z0-9_]+)\s*$")
EPIC_TAG_VALID_RE = re.compile(r"^[A-Za-z0-9_]+$")


def create_epic(tag: str, dir: str | None = None, color: str | None = None, todo_dir: Path = TODO_DIR) -> tuple[bool, str | None]:
    clean_tag = (tag or "").strip().lstrip("#").strip()
    if not clean_tag or not EPIC_TAG_VALID_RE.match(clean_tag):
        return False, "invalid tag"
    todo_dir = Path(todo_dir)
    path = todo_dir / "epics.md"

    def mutate(lines: list[str]):
        for line in lines:
            match = EPIC_HEADING_RE.match(line.strip())
            if match and match.group(1) == clean_tag:
                return False, "tag already exists"
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        header = "" if lines else "# Epics\n"
        block = f"{header}\n## #{clean_tag}\n\n"
        if dir:
            block += f"dir: {dir}\n"
        if color:
            block += f"color: {color}\n"
        lines.append(block)
        return True, None

    backup_file(path, todo_dir)
    return write_lines_locked(path, mutate)


def move_line_between_files(source: Path, destination: Path, task_id: str, expected_text: str, new_line: str, todo_dir: Path, to_top: bool = False) -> tuple[bool, str | None, str]:
    removed = {"ok": False, "reason": None, "line": "", "idx": 0, "current_text": ""}

    def remove(lines: list[str]):
        idx, conflict = locate(lines, task_id, expected_text)
        if idx == -1:
            removed["reason"] = "line not found"
            return False, removed["reason"]
        if conflict:
            removed["current_text"] = parse_line_parts(lines[idx])[2]
            return False, "conflict"
        removed["line"] = lines.pop(idx)
        removed["idx"] = idx
        removed["ok"] = True
        return True, None

    def restore(lines: list[str]):
        idx = min(int(removed["idx"]), len(lines))
        lines.insert(idx, str(removed["line"]))
        return True, None

    backup_file(source, todo_dir)
    ok, reason = write_lines_locked(source, remove)
    if not ok:
        return False, reason, removed["current_text"]
    backup_file(destination, todo_dir)
    try:
        line_to_write = new_line if new_line.endswith("\n") else new_line + "\n"
        if to_top:
            ok, reason = prepend_locked(destination, line_to_write)
            if not ok:
                raise RuntimeError(reason or "prepend failed")
        else:
            append_locked(destination, line_to_write)
    except Exception:
        write_lines_locked(source, restore)
        raise
    return True, None, ""


def blank_receipt() -> dict:
    return {
        "appliedStatus": [],
        "appliedTexts": [],
        "appliedTags": [],
        "appliedTagRemovals": [],
        "appliedNewItems": [],
        "skipped": [],
        "conflicts": [],
        "errors": [],
    }


def apply_sync(payload: dict, todo_dir: Path = TODO_DIR, today: str | None = None) -> dict:
    receipt = blank_receipt()
    todo_dir = Path(todo_dir)
    text_edits_by_id = {edit.get("task_id", ""): edit for edit in payload.get("textEdits", [])}
    tag_additions_by_id = {addition.get("task_id", ""): addition for addition in payload.get("tagAdditions", [])}
    tag_removals_by_id = {removal.get("task_id", ""): removal for removal in payload.get("tagRemovals", [])}
    consumed_text_ids: set[str] = set()
    consumed_tag_ids: set[str] = set()
    consumed_removal_ids: set[str] = set()

    for change in payload.get("statusChanges", []):
        task_id = change.get("task_id", "")
        try:
            source = resolve_source(change.get("source_file", ""), todo_dir)
            destination = destination_for(change.get("new_status", "Pending"), todo_dir)
            expected = change.get("expected_text", "")
            lines = source.read_text(encoding="utf-8").splitlines(keepends=True) if source.exists() else []
            idx, conflict = locate(lines, task_id, expected)
            if idx == -1:
                receipt["skipped"].append({"task_id": task_id, "reason": "line not found"})
                continue
            if conflict:
                receipt["conflicts"].append({"task_id": task_id, "current_text": parse_line_parts(lines[idx])[2]})
                continue
            edit = text_edits_by_id.get(task_id)
            addition = tag_additions_by_id.get(task_id)
            removal = tag_removals_by_id.get(task_id)
            existing_tags = set(TAG_RE.findall(lines[idx]))
            added_tags = []
            if addition:
                added_tags = [tag.lstrip("#") for tag in addition.get("tags", []) if tag and tag.lstrip("#") not in existing_tags]
            removed_tags = []
            if removal:
                removed_tags = [tag.lstrip("#") for tag in removal.get("tags", []) if tag and tag.lstrip("#") in existing_tags]
            new_line = rebuild_line(
                lines[idx],
                new_status=change.get("new_status"),
                new_text=edit.get("new_text", "") if edit else None,
                added_tags=added_tags,
                today=today,
                removed_tags=removed_tags,
            )
            holder: dict = {}
            if source.resolve() == destination.resolve():
                backup_file(source, todo_dir)

                def replace(lines_: list[str]):
                    found, found_conflict = locate(lines_, task_id, expected)
                    if found == -1:
                        return False, "line not found"
                    if found_conflict:
                        holder["current_text"] = parse_line_parts(lines_[found])[2]
                        return False, "conflict"
                    lines_[found] = new_line
                    return True, None

                ok, reason = write_lines_locked(source, replace)
            else:
                to_top = (change.get("new_status") or "Pending").strip().lower() == "in progress"
                ok, reason, move_current_text = move_line_between_files(
                    source, destination, task_id, expected, new_line, todo_dir, to_top=to_top
                )
                holder["current_text"] = move_current_text
            if ok:
                receipt["appliedStatus"].append(task_id)
                if edit:
                    receipt["appliedTexts"].append(task_id)
                    consumed_text_ids.add(task_id)
                if addition:
                    if added_tags:
                        receipt["appliedTags"].append(task_id)
                    else:
                        receipt["skipped"].append({"task_id": task_id, "reason": "tags already present"})
                    consumed_tag_ids.add(task_id)
                if removal:
                    if removed_tags:
                        receipt["appliedTagRemovals"].append(task_id)
                    else:
                        receipt["skipped"].append({"task_id": task_id, "reason": "tags not present"})
                    consumed_removal_ids.add(task_id)
            elif reason == "conflict":
                receipt["conflicts"].append({"task_id": task_id, "current_text": holder.get("current_text", "")})
            else:
                receipt["skipped"].append({"task_id": task_id, "reason": reason or "not applied"})
        except Exception as exc:
            receipt["errors"].append({"task_id": task_id, "error": str(exc)})

    for edit in payload.get("textEdits", []):
        task_id = edit.get("task_id", "")
        if task_id in consumed_text_ids:
            continue
        try:
            source = resolve_source(edit.get("source_file", ""), todo_dir)
            backup_file(source, todo_dir)
            holder: dict = {}

            def replace(lines: list[str]):
                idx, conflict = locate(lines, task_id, edit.get("expected_text", ""))
                if idx == -1:
                    return False, "line not found"
                if conflict:
                    holder["current_text"] = parse_line_parts(lines[idx])[2]
                    return False, "conflict"
                lines[idx] = rebuild_line(lines[idx], new_text=edit.get("new_text", ""))
                return True, None

            ok, reason = write_lines_locked(source, replace)
            if ok:
                receipt["appliedTexts"].append(task_id)
            elif reason == "conflict":
                receipt["conflicts"].append({"task_id": task_id, "current_text": holder.get("current_text", "")})
            else:
                receipt["skipped"].append({"task_id": task_id, "reason": reason or "line not found"})
        except Exception as exc:
            receipt["errors"].append({"task_id": task_id, "error": str(exc)})

    for addition in payload.get("tagAdditions", []):
        task_id = addition.get("task_id", "")
        if task_id in consumed_tag_ids:
            continue
        try:
            source = resolve_source(addition.get("source_file", ""), todo_dir)
            tags = [tag.lstrip("#") for tag in addition.get("tags", []) if tag]
            backup_file(source, todo_dir)
            holder: dict = {}

            def replace(lines: list[str]):
                idx, conflict = locate(lines, task_id, addition.get("expected_text", ""))
                if idx == -1:
                    return False, "line not found"
                if conflict:
                    holder["current_text"] = parse_line_parts(lines[idx])[2]
                    return False, "conflict"
                existing = set(TAG_RE.findall(lines[idx]))
                new_tags = [tag for tag in tags if tag not in existing]
                if not new_tags:
                    return False, "tags already present"
                lines[idx] = rebuild_line(lines[idx], added_tags=new_tags)
                return True, None

            ok, reason = write_lines_locked(source, replace)
            if ok:
                receipt["appliedTags"].append(task_id)
            elif reason == "conflict":
                receipt["conflicts"].append({"task_id": task_id, "current_text": holder.get("current_text", "")})
            else:
                receipt["skipped"].append({"task_id": task_id, "reason": reason or "not applied"})
        except Exception as exc:
            receipt["errors"].append({"task_id": task_id, "error": str(exc)})

    for removal in payload.get("tagRemovals", []):
        task_id = removal.get("task_id", "")
        if task_id in consumed_removal_ids:
            continue
        try:
            source = resolve_source(removal.get("source_file", ""), todo_dir)
            tags = [tag.lstrip("#") for tag in removal.get("tags", []) if tag]
            backup_file(source, todo_dir)
            holder: dict = {}

            def replace(lines: list[str]):
                idx, conflict = locate(lines, task_id, removal.get("expected_text", ""))
                if idx == -1:
                    return False, "line not found"
                if conflict:
                    holder["current_text"] = parse_line_parts(lines[idx])[2]
                    return False, "conflict"
                existing = set(TAG_RE.findall(lines[idx]))
                gone_tags = [tag for tag in tags if tag in existing]
                if not gone_tags:
                    return False, "tags not present"
                lines[idx] = rebuild_line(lines[idx], removed_tags=gone_tags)
                return True, None

            ok, reason = write_lines_locked(source, replace)
            if ok:
                receipt["appliedTagRemovals"].append(task_id)
            elif reason == "conflict":
                receipt["conflicts"].append({"task_id": task_id, "current_text": holder.get("current_text", "")})
            else:
                receipt["skipped"].append({"task_id": task_id, "reason": reason or "not applied"})
        except Exception as exc:
            receipt["errors"].append({"task_id": task_id, "error": str(exc)})

    new_items = payload.get("newItems", [])
    existing_ids = collect_existing_ids(todo_dir) if new_items else set()
    for item in new_items:
        text = item.get("text", "").strip()
        try:
            if not text:
                receipt["skipped"].append({"text": text, "reason": "empty text"})
                continue
            destination = destination_for(item.get("status", "Pending"), todo_dir)
            backup_file(destination, todo_dir)
            new_line = build_new_item_line(text, item.get("status", "Pending"), item.get("tags", []), existing_ids)
            existing_id_match = ID_RE.search(new_line)
            if existing_id_match:
                existing_ids.add(existing_id_match.group(1))
            ok, reason = prepend_locked(destination, new_line)
            if ok:
                receipt["appliedNewItems"].append(text)
            else:
                receipt["errors"].append({"text": text, "error": reason or "prepend failed"})
        except Exception as exc:
            receipt["errors"].append({"text": text, "error": str(exc)})

    return receipt


if __name__ == "__main__":
    import json

    print(json.dumps(apply_sync({"version": 1, "timestamp": 0}), indent=2))
    print("sync_changes.py OK")
