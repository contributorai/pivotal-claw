from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import os
import random
import re
import shutil
import string
import tempfile


TODO_DIR = Path(__file__).resolve().parent
STATUS_FILES = {
    "todo.md": "Pending",
    "doing.md": "In progress",
    "done.md": "Done",
    "icebox.md": "On schedule",
}
SEED_FILES = {
    "todo.md": "# Todo\n",
    "doing.md": "# Doing\n",
    "done.md": "# Done\n",
    "icebox.md": "# Icebox\n",
    "epics.md": "# Epics\n\n",
}
TASK_RE = re.compile(r"^(\s*)(?:[-*+]\s+\[([ xX/\-])\]\s+|[-*+]\s+|[✓✅]\s*)(.+?)\s*$")
ID_RE = re.compile(r"\bID:\s*(t:[a-z0-9]{7})\b")
BROAD_ID_RE = re.compile(r"\bID:\s*(?:t:[a-z0-9]{7}|T[-A-Za-z0-9]+)\b")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]+)\b")
COMPLETION_RE = re.compile(r"(?:✓|Completed)\s*(\d{4}-\d{2}-\d{2})")


@dataclass
class Task:
    text: str
    status: str
    tags: list[str]
    source_file: str
    project: str
    section: str
    completion_date: str
    task_id: str
    line_number: int
    children: list["Task"] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["id"] = self.task_id
        data["guid"] = self.task_id
        data["display_text"] = clean_display_text(self.text)
        return data


def ensure_seed_files(todo_dir: Path = TODO_DIR) -> None:
    todo_dir.mkdir(parents=True, exist_ok=True)
    (todo_dir / "tree_viewer").mkdir(parents=True, exist_ok=True)
    for name, content in SEED_FILES.items():
        path = todo_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def extract_tags(text: str) -> list[str]:
    return TAG_RE.findall(text)


def extract_task_id(text: str) -> tuple[str, str]:
    match = ID_RE.search(text)
    if not match:
        return "", text
    return match.group(1), (text[: match.start()] + text[match.end() :]).strip()


def extract_completion_date(text: str) -> str:
    match = COMPLETION_RE.search(text)
    return match.group(1) if match else ""


def normalize_epic_color(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:]
    if re.fullmatch(r"[0-9a-fA-F]{6}", cleaned):
        return cleaned.upper()
    return ""


def generate_task_id(existing: set[str] | None = None) -> str:
    existing = existing or set()
    alphabet = string.ascii_lowercase + string.digits
    while True:
        task_id = "t:" + "".join(random.choices(alphabet, k=7))
        if task_id not in existing:
            return task_id


def parse_status(filename: str, checkbox: str | None, raw_line: str) -> str:
    if raw_line.lstrip().startswith(("✓", "✅")):
        return "Done"
    if checkbox in {"x", "X"}:
        return "Done"
    if checkbox in {"/", "-"}:
        return "In progress"
    return STATUS_FILES.get(filename, "Pending")


def clean_display_text(text: str) -> str:
    text = BROAD_ID_RE.sub("", text)
    text = COMPLETION_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(text: str) -> str:
    text = TASK_RE.sub(r"\3", text.strip())
    text = BROAD_ID_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = COMPLETION_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def parse_todo_file(filepath: str | Path) -> list[Task]:
    path = Path(filepath)
    tasks: list[Task] = []
    stack: list[tuple[int, Task]] = []
    section = ""
    in_fence = False
    fallback_status = STATUS_FILES.get(path.name, "Pending")

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
            continue

        match = TASK_RE.match(line)
        if not match:
            continue

        indent = len(match.group(1).replace("\t", "  "))
        checkbox = match.group(2)
        rest = match.group(3).strip()
        status = parse_status(path.name, checkbox, line) or fallback_status
        task_id, _ = extract_task_id(rest)
        task = Task(
            text=rest,
            status=status,
            tags=extract_tags(rest),
            source_file=str(path.resolve()),
            project=path.parent.name,
            section=section,
            completion_date=extract_completion_date(rest),
            task_id=task_id,
            line_number=line_number,
        )

        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1].children.append(task)
        else:
            tasks.append(task)
        stack.append((indent, task))

    return tasks


def flatten_tasks(tasks: list[Task]) -> list[Task]:
    flat: list[Task] = []
    for task in tasks:
        flat.append(task)
        flat.extend(flatten_tasks(task.children))
    return flat


def discover_todo_files(todo_dir: Path = TODO_DIR) -> list[str]:
    ensure_seed_files(todo_dir)
    names = ["todo.md", "doing.md", "done.md", "icebox.md"]
    return [str((todo_dir / name).resolve()) for name in names if (todo_dir / name).exists()]


def backup_todo_files(filepaths: list[str]) -> None:
    for filepath in filepaths:
        path = Path(filepath)
        backup = path.with_suffix(path.suffix + ".back")
        if not backup.exists():
            shutil.copy2(path, backup)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def assign_ids_to_files(tasks: list[Task]) -> int:
    flat = flatten_tasks(tasks)
    missing = [task for task in flat if not task.task_id]
    if not missing:
        return 0

    existing = {task.task_id for task in flat if task.task_id}
    by_file: dict[str, list[Task]] = {}
    for task in missing:
        by_file.setdefault(task.source_file, []).append(task)
    backup_todo_files(list(by_file))

    assigned = 0
    for filename, file_tasks in by_file.items():
        path = Path(filename)
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        for task in sorted(file_tasks, key=lambda item: item.line_number):
            idx = task.line_number - 1
            if idx < 0 or idx >= len(lines) or ID_RE.search(lines[idx]):
                continue
            new_id = generate_task_id(existing)
            existing.add(new_id)
            newline = "\n" if lines[idx].endswith("\n") else ""
            body = lines[idx].rstrip("\n")
            if BROAD_ID_RE.search(body):
                body = BROAD_ID_RE.sub(f"ID: {new_id}", body, count=1)
                lines[idx] = f"{body}{newline}"
                assigned += 1
                continue
            lines[idx] = f"{body} ID: {new_id}{newline}"
            assigned += 1
        atomic_write(path, "".join(lines))
    return assigned


def deduplicate_tasks(tasks: list[Task]) -> list[Task]:
    priority = {"doing.md": 0, "todo.md": 1, "icebox.md": 2, "done.md": 3}
    selected: dict[str, tuple[int, int, Task]] = {}
    order = 0
    for task in tasks:
        fingerprint = normalize_text(task.text)
        source_priority = priority.get(Path(task.source_file).name, 99)
        current = selected.get(fingerprint)
        if current is None or (source_priority, order) < (current[0], current[1]):
            selected[fingerprint] = (source_priority, order, task)
        order += 1
    return [item[2] for item in sorted(selected.values(), key=lambda item: item[1])]


def parse_epics_md(epics_path: str | Path | None = None) -> dict:
    path = Path(epics_path) if epics_path else TODO_DIR / "epics.md"
    if not path.exists():
        return {}

    meta: dict[str, dict] = {}
    current_tag = ""
    description: list[str] = []

    def flush() -> None:
        nonlocal description
        if current_tag:
            meta[current_tag].setdefault("description", "\n".join(description).strip())
            meta[current_tag]["sessions"].sort(key=lambda session: session["date"], reverse=True)
            description = []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        heading = re.match(r"^#+\s+#([A-Za-z0-9_]+)\s*$", line)
        if heading:
            flush()
            current_tag = heading.group(1)
            meta[current_tag] = {"tag": current_tag, "sessions": []}
            continue
        if not current_tag:
            continue
        kv = re.match(r"^(dir|resume|color|session_log|log):\s*(.+)$", line, re.I)
        if kv:
            key = kv.group(1).lower()
            if key == "log":
                key = "session_log"
            value = kv.group(2).strip()
            if key == "color":
                normalized_color = normalize_epic_color(value)
                if normalized_color:
                    meta[current_tag][key] = normalized_color
                continue
            meta[current_tag][key] = value
            continue
        session = re.match(r"^(?:session|sessions):\s*(.+?)\s+([0-9a-fA-F-]{12,})(?:\s+(.+))?$", line, re.I)
        if session:
            meta[current_tag]["sessions"].append(
                {"date": session.group(1).strip(), "uuid": session.group(2), "summary": (session.group(3) or "").strip()}
            )
            continue
        if line:
            description.append(line)
    flush()
    return meta


def build_todo_data(todo_dir: Path = TODO_DIR) -> dict:
    ensure_seed_files(todo_dir)
    filepaths = discover_todo_files(todo_dir)
    parsed: list[Task] = []
    for filepath in filepaths:
        parsed.extend(parse_todo_file(filepath))
    if assign_ids_to_files(parsed):
        parsed = []
        for filepath in filepaths:
            parsed.extend(parse_todo_file(filepath))

    deduped = deduplicate_tasks(parsed)
    counts: dict[str, int] = {}
    for task in deduped:
        counts[task.status] = counts.get(task.status, 0) + 1

    return {
        "tasks": [task.to_dict() for task in deduped],
        "epic_meta": parse_epics_md(todo_dir / "epics.md"),
        "counts": counts,
        "story_sessions": {},
        "portfolio_data": {},
    }


if __name__ == "__main__":
    data = build_todo_data()
    by_file: dict[str, int] = {}
    for task in data["tasks"]:
        by_file[task["source_file"]] = by_file.get(task["source_file"], 0) + 1
    for filename, count in sorted(by_file.items()):
        print(f"{filename}: {count}")
