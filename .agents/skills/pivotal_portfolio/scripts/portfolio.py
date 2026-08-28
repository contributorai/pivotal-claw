#!/usr/bin/env python3
"""Inspect and edit the Pivotal Claw portfolio grouping (portfolio.json).

Portfolios group epics; stories are grouped by epic. So this operates on epics and
portfolios only — use /pivotal for story-level work.

Usage:
    portfolio.py show
    portfolio.py audit
    portfolio.py create --id board_ux --name "Board UX" [--color F39C12] [--focus]
    portfolio.py edit board_ux --name "Board UX" [--color ...] [--focus|--no-focus]
    portfolio.py delete board_ux
    portfolio.py assign ui_optimization new_user_onboarding --to board_ux
    portfolio.py assign ui_optimization --unassign
    portfolio.py focus board_core board_ux
    portfolio.py archive housekeeping
    portfolio.py unarchive housekeeping

Every mutating command validates before writing and backs the file up into
.sync_backups/ first. Writes go straight to disk, so the running board needs a
reload to pick them up.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
TODO_DIR = REPO_ROOT / "cc" / "todo"

sys.path.insert(0, str(TODO_DIR))
import portfolio_store as store  # noqa: E402


RELOAD_HINT = "Reload the board (localhost:5056) to see this — external writes don't push to open tabs."


def resolve_todo_dir(raw: str | None) -> Path:
    if not raw:
        return TODO_DIR
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    if not candidate.is_dir():
        raise SystemExit(f"error: --todo-dir {raw} is not a directory")
    return candidate


def load(todo_dir: Path) -> list[dict]:
    try:
        return store.read_portfolios(todo_dir)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")


def save(items: list[dict], todo_dir: Path) -> None:
    """Refuse to write a file that would break the board."""
    errors = store.validate(items, store.load_epic_meta(todo_dir))
    if errors:
        print("Refusing to write — this would leave portfolio.json inconsistent:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)
    store.write_portfolios(items, todo_dir)


def cmd_show(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    if not items:
        print(f"No portfolios defined in {store.portfolio_path(todo_dir)}")
        return 0

    summary = store.summarize(items, store.load_todo_data(todo_dir))
    by_id = {entry["id"]: entry for entry in summary["portfolios"]}
    width = max(len(str(item.get("name", ""))) for item in items)

    active = store.active_portfolios(items)
    archived = store.archived_portfolios(items)

    def print_row(item):
        entry = by_id[item["id"]]
        epics = len(entry["epics"])
        shortcut = item.get("shortcut") or ""
        print(
            f"  {f'[{shortcut}]' if shortcut else '   '} {entry['name']:<{width}}  {entry['done']:>3}/{entry['total']:<3} done"
            f"  {epics} epic{'s' if epics != 1 else ''}"
            f"  ({item['id']})"
        )
        if args.verbose:
            for tag in entry["epics"]:
                print(f"      #{tag}")

    groups = [
        ("In focus", [item for item in active if item.get("focus")]),
        ("Other", [item for item in active if not item.get("focus")]),
    ]
    if args.archived:
        groups.append(("Archived", archived))

    for label, rows in groups:
        if not rows:
            continue
        print(f"\n{label}")
        for item in rows:
            print_row(item)

    # Archived portfolios still own their epics, so they're only collapsed, not dropped.
    if archived and not args.archived:
        names = ", ".join(str(item.get("name") or item["id"]) for item in archived)
        print(f"\nArchived ({len(archived)}): {names}   — `show --archived` for details")

    orphans = summary["unassigned_epics"]
    if orphans:
        print(f"\nUnassigned epics ({len(orphans)})")
        for tag in orphans:
            print(f"  #{tag}")
    else:
        print("\nEvery epic belongs to a portfolio.")

    loose = summary["unassigned_stories"]
    if loose["total"]:
        print(f"\n{loose['total']} stories carry no epic, so no portfolio claims them.")
    return 0


def cmd_audit(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    errors = store.validate(items, store.load_epic_meta(todo_dir))
    if errors:
        print(f"{len(errors)} problem(s) in {store.portfolio_path(todo_dir)}:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"{len(items)} portfolios, no problems found.")
    return 0


def cmd_create(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    try:
        items = store.create_portfolio(
            items,
            args.id,
            args.name,
            color=args.color or "",
            description=args.description or "",
            focus=args.focus,
            shortcut=args.shortcut or "",
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    save(items, todo_dir)
    focus_note = " and focused it" if args.focus else ""
    print(f"Created portfolio {args.name!r}{focus_note}. It has no epics yet — use `assign ... --to {args.id}`.")
    print(RELOAD_HINT)
    return 0


def cmd_edit(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    focus = True if args.focus else (False if args.no_focus else None)
    if (
        args.name is None
        and args.color is None
        and args.description is None
        and args.shortcut is None
        and focus is None
    ):
        raise SystemExit(
            "error: nothing to change — pass --name, --color, --description, --shortcut, --focus or --no-focus"
        )
    try:
        items = store.update_portfolio(
            items,
            args.id,
            name=args.name,
            color=args.color,
            description=args.description,
            focus=focus,
            shortcut=args.shortcut,
        )
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    save(items, todo_dir)
    target = next(item for item in items if item["id"] == args.id)
    print(f"Updated {target['name']!r}.")
    print(RELOAD_HINT)
    return 0


def cmd_delete(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    try:
        doomed = next(item for item in items if item.get("id") == args.id)
    except StopIteration:
        raise SystemExit(f"error: no portfolio with id {args.id!r}")
    freed = [store.normalize_tag(tag) for tag in doomed.get("epic_tags", [])]
    items = store.delete_portfolio(items, args.id)
    save(items, todo_dir)
    print(f"Deleted {doomed.get('name')!r}.")
    if freed:
        print(f"Now unassigned: {', '.join('#' + tag for tag in freed)}")
    print(RELOAD_HINT)
    return 0


def cmd_assign(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    tags = [store.normalize_tag(tag) for tag in args.tags]

    epic_meta = store.load_epic_meta(todo_dir)
    unknown = [tag for tag in tags if tag not in epic_meta]
    if unknown:
        raise SystemExit(
            "error: not epics in epics.md: " + ", ".join("#" + tag for tag in unknown) +
            "\nCheck cc/todo/epics.md for the canonical spelling."
        )

    previous = {
        store.normalize_tag(tag): item.get("name")
        for item in items
        for tag in item.get("epic_tags", [])
        if store.normalize_tag(tag) in tags
    }
    try:
        items = store.assign_epics(items, tags, to=None if args.unassign else args.to)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    save(items, todo_dir)

    for tag in tags:
        came_from = f" (was in {previous[tag]})" if tag in previous else ""
        if args.unassign:
            print(f"#{tag} is no longer in any portfolio{came_from}.")
        else:
            target = next(item for item in items if item["id"] == args.to)
            print(f"#{tag} → {target['name']}{came_from}")
    print(RELOAD_HINT)
    return 0


def cmd_focus(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    try:
        items = store.set_focus(items, args.ids)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    save(items, todo_dir)

    focused = [item["name"] for item in items if item.get("focus")]
    if focused:
        print("In focus: " + ", ".join(focused))
    else:
        print("Nothing is in focus now.")
    if len(focused) > 3:
        print("Note: focusing this many portfolios weakens the signal — 2 or 3 is the point of the flag.")
    print(RELOAD_HINT)
    return 0


def cmd_archive(args) -> int:
    todo_dir = resolve_todo_dir(args.todo_dir)
    items = load(todo_dir)
    archiving = not args.restore

    try:
        targets = [next(item for item in items if item.get("id") == pid) for pid in args.ids]
    except StopIteration:
        known = ", ".join(item.get("id", "?") for item in items)
        raise SystemExit(f"error: unknown portfolio id in {args.ids} (known: {known})")

    unfocused = [item["name"] for item in targets if archiving and item.get("focus")]
    try:
        items = store.set_archived(items, args.ids, archived=archiving)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    save(items, todo_dir)

    names = ", ".join(str(item.get("name") or item["id"]) for item in targets)
    if archiving:
        print(f"Archived: {names}")
        if unfocused:
            print(f"Dropped from focus: {', '.join(unfocused)}")
        print("Their stories stay on the board — only the Portfolio dropdown gets shorter.")
    else:
        print(f"Restored: {names}")
    print(RELOAD_HINT)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--todo-dir", help="board data directory (default: cc/todo)")
    sub = parser.add_subparsers(dest="command")

    show = sub.add_parser("show", help="list portfolios with rollups")
    show.add_argument("-v", "--verbose", action="store_true", help="also list each portfolio's epics")
    show.add_argument("--archived", action="store_true", help="expand archived portfolios into their own section")
    show.set_defaults(func=cmd_show)

    audit = sub.add_parser("audit", help="report inconsistencies; exits non-zero when found")
    audit.set_defaults(func=cmd_audit)

    create = sub.add_parser("create", help="add a portfolio")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--color", help="6 hex digits, no leading #")
    create.add_argument("--description", default="")
    create.add_argument("--shortcut", help="digit 1-9 that switches the board to this portfolio")
    create.add_argument("--focus", action="store_true")
    create.set_defaults(func=cmd_create)

    edit = sub.add_parser("edit", help="change a portfolio's fields")
    edit.add_argument("id")
    edit.add_argument("--name")
    edit.add_argument("--color", help="6 hex digits, no leading #")
    edit.add_argument("--description")
    edit.add_argument("--shortcut", help="digit 1-9 that switches the board to this portfolio; pass '' to clear")
    edit.add_argument("--focus", action="store_true")
    edit.add_argument("--no-focus", action="store_true")
    edit.set_defaults(func=cmd_edit)

    delete = sub.add_parser("delete", help="remove a portfolio; its epics become unassigned")
    delete.add_argument("id")
    delete.set_defaults(func=cmd_delete)

    assign = sub.add_parser("assign", help="move epics into a portfolio, or out of all of them")
    assign.add_argument("tags", nargs="+", help="epic tags, with or without the leading #")
    group = assign.add_mutually_exclusive_group(required=True)
    group.add_argument("--to", help="destination portfolio id")
    group.add_argument("--unassign", action="store_true")
    assign.set_defaults(func=cmd_assign)

    focus = sub.add_parser("focus", help="set the focus set to exactly these portfolios")
    focus.add_argument("ids", nargs="*", help="portfolio ids; pass none to clear focus entirely")
    focus.set_defaults(func=cmd_focus)

    archive = sub.add_parser("archive", help="retire portfolios: hidden from the board dropdown, epics kept")
    archive.add_argument("ids", nargs="+")
    archive.set_defaults(func=cmd_archive, restore=False)

    unarchive = sub.add_parser("unarchive", help="bring archived portfolios back")
    unarchive.add_argument("ids", nargs="+")
    unarchive.set_defaults(func=cmd_archive, restore=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["show"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
