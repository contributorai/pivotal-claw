"""Read, validate, and write portfolio.json — the epic-to-portfolio grouping.

Portfolios sit one level above epics: each portfolio names a set of epic tags and
carries a `focus` flag marking it as current work. The board's portfolio filter and
the /portfolio page both read this file, so the invariants enforced here (unique ids,
known epic tags, one owner per epic) are what keep those surfaces coherent.

Deliberately Flask-free so the /pivotal_portfolio skill works whether or not the
server is running — same property `add_story.py` relies on. That is why the atomic
write is reimplemented here instead of importing `server.write_json_atomic`.
"""
from __future__ import annotations

from pathlib import Path
import fcntl
import json
import os
import tempfile

from generate_todo_viewer import normalize_epic_color, parse_epics_md
from sync_changes import backup_file

TODO_DIR = Path(__file__).resolve().parent
PORTFOLIO_FILENAME = "portfolio.json"

# Written for every new portfolio so hand-editing the file stays predictable.
FIELD_ORDER = ("id", "name", "shortcut", "color", "description", "epic_tags", "focus", "archived")

# The board switches portfolio scope on a bare digit keypress; 0 is reserved for
# "all portfolios", so a portfolio may claim 1-9.
SHORTCUT_KEYS = tuple("123456789")


def portfolio_path(todo_dir: Path = TODO_DIR) -> Path:
    return Path(todo_dir) / PORTFOLIO_FILENAME


def read_portfolios(todo_dir: Path = TODO_DIR) -> list[dict]:
    path = portfolio_path(todo_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    items = data.get("items") if isinstance(data, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def write_portfolios(items: list[dict], todo_dir: Path = TODO_DIR) -> None:
    """Back up, then replace portfolio.json atomically under an exclusive lock.

    The backup matters more than usual here: cc/todo/*.json is gitignored, so a bad
    write is not recoverable from version control.
    """
    todo_dir = Path(todo_dir)
    path = portfolio_path(todo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_file(path, todo_dir)

    path.touch(exist_ok=True)
    with open(path, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    json.dump({"items": [_ordered(item) for item in items]}, tmp, indent=2, ensure_ascii=False)
                    tmp.write("\n")
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _ordered(item: dict) -> dict:
    """Known fields first in a stable order; anything else preserved after."""
    ordered = {key: item[key] for key in FIELD_ORDER if key in item}
    ordered.update({key: value for key, value in item.items() if key not in ordered})
    return ordered


def _clone(items: list[dict]) -> list[dict]:
    return [dict(item, epic_tags=list(item.get("epic_tags", []))) for item in items]


def _find(items: list[dict], portfolio_id: str) -> dict | None:
    return next((item for item in items if item.get("id") == portfolio_id), None)


def _require(items: list[dict], portfolio_id: str) -> dict:
    found = _find(items, portfolio_id)
    if found is None:
        known = ", ".join(item.get("id", "?") for item in items) or "none defined"
        raise ValueError(f"no portfolio with id {portfolio_id!r} (known: {known})")
    return found


def normalize_tag(tag: str) -> str:
    return str(tag).lstrip("#").strip()


# --- validation -------------------------------------------------------------


def validate(items: list[dict], epic_meta: dict | None = None) -> list[str]:
    """Return human-readable problems; an empty list means the file is coherent."""
    errors: list[str] = []
    known_epics = set(epic_meta or {})
    seen_ids: set[str] = set()
    owner: dict[str, str] = {}
    shortcut_owner: dict[str, str] = {}

    for index, item in enumerate(items):
        label = item.get("id") or f"item #{index + 1}"

        portfolio_id = item.get("id")
        if not portfolio_id:
            errors.append(f"{label}: missing id")
        elif portfolio_id in seen_ids:
            errors.append(f"{label}: duplicate id")
        else:
            seen_ids.add(portfolio_id)

        if not str(item.get("name") or "").strip():
            errors.append(f"{label}: missing name")

        color = item.get("color")
        if color and not normalize_epic_color(str(color)):
            errors.append(f"{label}: color {color!r} is not 6 hex digits")

        # Archived portfolios keep their digit reserved: a clash must not appear the
        # moment one is unarchived.
        shortcut = str(item.get("shortcut") or "")
        if shortcut and shortcut not in SHORTCUT_KEYS:
            errors.append(f"{label}: shortcut must be a digit 1-9")
        elif shortcut and shortcut in shortcut_owner:
            errors.append(f"shortcut {shortcut} is claimed by both {shortcut_owner[shortcut]} and {label}")
        elif shortcut:
            shortcut_owner[shortcut] = label

        if not isinstance(item.get("focus", False), bool):
            errors.append(f"{label}: focus must be true or false")

        if not isinstance(item.get("archived", False), bool):
            errors.append(f"{label}: archived must be true or false")
        elif item.get("archived") and item.get("focus"):
            errors.append(f"{label}: cannot be archived and in focus at once")

        tags = item.get("epic_tags")
        if not isinstance(tags, list):
            errors.append(f"{label}: epic_tags must be a list")
            continue

        for tag in tags:
            tag = normalize_tag(tag)
            if known_epics and tag not in known_epics:
                errors.append(f"{label}: #{tag} is not an epic in epics.md")
            if tag in owner:
                errors.append(f"#{tag} is claimed by both {owner[tag]} and {label}")
            else:
                owner[tag] = label

    return errors


def unassigned_epics(items: list[dict], epic_meta: dict) -> list[str]:
    claimed = {normalize_tag(tag) for item in items for tag in item.get("epic_tags", [])}
    return sorted(tag for tag in epic_meta if tag not in claimed)


# --- mutators (pure: take items, return new items) --------------------------


def create_portfolio(
    items: list[dict],
    portfolio_id: str,
    name: str,
    color: str = "",
    description: str = "",
    focus: bool = False,
    shortcut: str = "",
) -> list[dict]:
    portfolio_id = str(portfolio_id).strip()
    if not portfolio_id:
        raise ValueError("portfolio id is required")
    if _find(items, portfolio_id) is not None:
        raise ValueError(f"portfolio {portfolio_id!r} already exists")

    updated = _clone(items)
    updated.append(
        {
            "id": portfolio_id,
            "name": str(name).strip(),
            "shortcut": str(shortcut or "").strip(),
            "color": normalize_epic_color(str(color)) if color else "",
            "description": description or "",
            "epic_tags": [],
            "focus": bool(focus),
        }
    )
    return updated


def update_portfolio(items: list[dict], portfolio_id: str, **fields) -> list[dict]:
    updated = _clone(items)
    target = _require(updated, portfolio_id)
    for key in ("name", "description"):
        if fields.get(key) is not None:
            target[key] = str(fields[key])
    if fields.get("shortcut") is not None:
        target["shortcut"] = str(fields["shortcut"]).strip()
    if fields.get("color") is not None:
        target["color"] = normalize_epic_color(str(fields["color"])) if fields["color"] else ""
    if fields.get("focus") is not None:
        target["focus"] = bool(fields["focus"])
    return updated


def delete_portfolio(items: list[dict], portfolio_id: str) -> list[dict]:
    """Remove a portfolio; its epics simply become unassigned."""
    _require(items, portfolio_id)
    return [item for item in _clone(items) if item.get("id") != portfolio_id]


def assign_epics(items: list[dict], tags: list[str], to: str | None = None) -> list[dict]:
    """Move epics into a portfolio, or out of every portfolio when `to` is None.

    An epic belongs to exactly one portfolio, so it is removed from its current
    owner before being added.
    """
    updated = _clone(items)
    target = _require(updated, to) if to is not None else None
    wanted = [normalize_tag(tag) for tag in tags]

    for item in updated:
        item["epic_tags"] = [tag for tag in item["epic_tags"] if normalize_tag(tag) not in wanted]

    if target is not None:
        for tag in wanted:
            if tag not in target["epic_tags"]:
                target["epic_tags"].append(tag)

    return updated


def set_focus(items: list[dict], ids: list[str]) -> list[dict]:
    """Focus exactly these portfolios; everything else is cleared."""
    updated = _clone(items)
    wanted = set(ids)
    for portfolio_id in wanted:
        target = _require(updated, portfolio_id)
        if target.get("archived"):
            raise ValueError(f"{portfolio_id!r} is archived — unarchive it before focusing it")
    for item in updated:
        item["focus"] = item.get("id") in wanted
    return updated


def set_archived(items: list[dict], ids: list[str], archived: bool = True) -> list[dict]:
    """Archive or restore the named portfolios, leaving the rest alone.

    Archiving clears focus: a portfolio can't be both retired and current. The
    portfolio keeps its epics either way, so archiving never orphans them.
    """
    updated = _clone(items)
    for portfolio_id in ids:
        target = _require(updated, portfolio_id)
        target["archived"] = bool(archived)
        if archived:
            target["focus"] = False
    return updated


def _reordered_slots(entries: list, keys: list, wanted: list, label: str) -> list:
    """Rearrange only the entries the caller named, leaving the rest in place.

    The stack rank UI drags active portfolios (or one portfolio's epics); anything
    it never showed — an archived portfolio, say — must not drift just because it
    was absent from the payload. So the named entries are re-slotted into exactly
    the positions they already occupied, in the requested order.
    """
    slots = [index for index, key in enumerate(keys) if key in wanted]
    if len(slots) != len(wanted):
        missing = [key for key in wanted if key not in keys]
        raise ValueError(f"unknown {label}: {', '.join(missing)}")

    by_key = {key: entries[index] for index, key in enumerate(keys) if key in wanted}
    result = list(entries)
    for slot, key in zip(slots, wanted):
        result[slot] = by_key[key]
    return result


def reorder_portfolios(items: list[dict], ids: list[str]) -> list[dict]:
    """Rank portfolios: array order in portfolio.json is the priority order."""
    wanted = [str(portfolio_id) for portfolio_id in ids]
    if len(set(wanted)) != len(wanted):
        raise ValueError("a portfolio can only appear once in a rank order")

    updated = _clone(items)
    return _reordered_slots(updated, [item.get("id") for item in updated], wanted, "portfolio id")


def reorder_epics(items: list[dict], portfolio_id: str, tags: list[str]) -> list[dict]:
    """Rank the epics inside one portfolio, without moving them between portfolios."""
    wanted = [normalize_tag(tag) for tag in tags]
    if len(set(wanted)) != len(wanted):
        raise ValueError("an epic can only appear once in a rank order")

    updated = _clone(items)
    target = _require(updated, portfolio_id)
    target["epic_tags"] = _reordered_slots(
        target["epic_tags"],
        [normalize_tag(tag) for tag in target["epic_tags"]],
        wanted,
        f"epic in {portfolio_id!r}",
    )
    return updated


def is_archived(item: dict) -> bool:
    return bool(item.get("archived"))


def active_portfolios(items: list[dict]) -> list[dict]:
    return [item for item in items if not is_archived(item)]


def archived_portfolios(items: list[dict]) -> list[dict]:
    return [item for item in items if is_archived(item)]


# --- reporting --------------------------------------------------------------


def summarize(items: list[dict], todo_data: dict) -> dict:
    """Per-portfolio epic/story rollups plus the epics no portfolio claims.

    A story tagged with epics from two portfolios counts toward both, matching
    countTasksByPortfolio in pivotalData.js.
    """
    epic_meta = todo_data.get("epic_meta") or {}
    tasks = todo_data.get("tasks") or []

    owner: dict[str, str] = {}
    for item in items:
        for tag in item.get("epic_tags", []):
            owner.setdefault(normalize_tag(tag), item.get("id"))

    rollups = {
        item.get("id"): {
            "id": item.get("id"),
            "name": item.get("name"),
            "focus": bool(item.get("focus")),
            "archived": is_archived(item),
            "epics": [normalize_tag(tag) for tag in item.get("epic_tags", [])],
            "total": 0,
            "done": 0,
        }
        for item in items
    }
    unowned = {"total": 0, "done": 0}

    for task in _flatten(tasks):
        is_done = (task.get("status") or "").lower() == "done"
        owners = {owner[normalize_tag(tag)] for tag in task.get("tags", []) if normalize_tag(tag) in owner}
        if not owners:
            unowned["total"] += 1
            unowned["done"] += 1 if is_done else 0
            continue
        for portfolio_id in owners:
            rollups[portfolio_id]["total"] += 1
            rollups[portfolio_id]["done"] += 1 if is_done else 0

    return {
        "portfolios": list(rollups.values()),
        "unassigned_epics": unassigned_epics(items, epic_meta),
        "unassigned_stories": unowned,
    }


def _flatten(tasks: list[dict]):
    for task in tasks:
        yield task
        yield from _flatten(task.get("children") or [])


def load_todo_data(todo_dir: Path = TODO_DIR) -> dict:
    """epic_meta + tasks for reporting, without importing the Flask server."""
    import generate_todo_viewer as gtv

    return gtv.build_todo_data(Path(todo_dir))


def load_epic_meta(todo_dir: Path = TODO_DIR) -> dict:
    return parse_epics_md(Path(todo_dir) / "epics.md")
