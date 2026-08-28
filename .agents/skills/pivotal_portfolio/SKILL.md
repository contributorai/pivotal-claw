---
name: pivotal_portfolio
description: >
  Inspect and edit Pivotal Claw portfolios — the named groups of epics that drive
  the board's portfolio filter and the /portfolio page. Trigger this skill when the
  user runs /pivotal_portfolio or asks to "show portfolios", "create a portfolio",
  "assign an epic to a portfolio", "move this epic into X", "what's unassigned",
  "focus on these portfolios", or "rename/delete a portfolio". For adding or editing
  individual stories, use the pivotal skill instead.
---

# Pivotal Portfolio Manager

Portfolios sit one level above epics: each names a set of epic tags and carries a
`focus` flag for current work. Everything here operates on **epics and portfolios**.
Stories belong to epics, and story-level work stays with the `pivotal` skill.

All commands read and write `cc/todo/portfolio.json` through `cc/todo/portfolio_store.py`,
which validates before every write and backs the file up into `cc/todo/.sync_backups/`.
No Flask needed, so it works whether or not the server is running.

## Quick Use

```bash
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py show
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py show -v      # + each portfolio's epics
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py audit

python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py create --id board_ux --name "Board UX" --color F39C12
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py edit board_ux --name "Board Experience" --focus
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py edit board_ux --shortcut 3   # '' clears it
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py delete board_ux

python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py assign ui_optimization new_user_onboarding --to board_ux
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py assign ui_optimization --unassign

python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py focus board_core session_launch

python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py archive housekeeping
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py unarchive housekeeping
python3 .agents/skills/pivotal_portfolio/scripts/portfolio.py show --archived
```

`--todo-dir` points at a different board directory (e.g. `--todo-dir demo-data`) and is
useful for trying something out without touching real data.

## Rules That Matter

- **An epic belongs to exactly one portfolio.** `assign` enforces this: moving an epic
  removes it from its previous portfolio automatically. Report that move to the user —
  the script prints "was in X" for exactly this reason.
- **Epic tags must exist in `cc/todo/epics.md`.** Read that file for the canonical
  spelling rather than guessing; `assign` rejects unknown tags and tells you to check.
  Note real tags include typos like `#commited_code` — use what's actually there.
- **Colors are 6 hex digits, no leading `#`** (e.g. `F39C12`), matching `epics.md`.
- **`shortcut` is the digit key that switches the board to a portfolio.** `1`-`9`, unique
  across the file (archived portfolios keep theirs reserved so unarchiving can't clash);
  `0` is taken by "All portfolios". Optional — a portfolio without one is dropdown-only.
  `show` prints each binding as `[n]`.
- **Keep the focus set small — 2 or 3.** Marking everything as focus defeats the flag's
  purpose. `focus` replaces the whole set rather than adding to it, so pass every
  portfolio that should be focused, and pass none to clear it.
- **Archive is not delete.** An archived portfolio keeps its epics and its stories stay
  on the board under "All portfolios" — it just drops out of the Portfolio dropdown and
  collapses into an "Archived" section on `/portfolio`. Prefer it to `delete` when the
  user says they're done with an area and want less noise.
- **Archiving clears focus**, since a portfolio can't be both retired and current. The
  script says so when it happens. `focus` refuses an archived portfolio outright.
- **Deleting a portfolio does not touch its epics** — they just become unassigned, and
  the script lists which ones.
- **The board does not live-update.** These writes bypass the server, so tell the user
  to reload `localhost:5056` to see the change in the Portfolio dropdown and `/portfolio`.

## When To Use What

| Ask | Command |
|---|---|
| "what portfolios do I have" / "what's the state" | `show` |
| "which epics aren't grouped yet" | `show` (trailing unassigned list) or `audit` |
| "put epic X under Y" / "move X out" | `assign` |
| "I want to work on X and Y now" | `focus` |
| "I'm done with this area" / "too much noise in the dropdown" | `archive` |
| "bring X back" / "what did I archive" | `unarchive` / `show --archived` |
| "add/rename/recolor/remove a portfolio" | `create` / `edit` / `delete` |
| "give X a number key" / "which key switches to X" | `edit --shortcut` / `show` |
| "add a story" / "add a note to a story" | the `pivotal` skill, not this one |

## Notes

- Refer to portfolios and epics by **name**, not by id, when reporting back to the user.
- `audit` exits non-zero when the file is inconsistent, so it doubles as a check after
  hand-edits. It catches unknown epic tags, duplicate ids, epics claimed twice,
  malformed colors, missing names, and clashing or out-of-range shortcuts.
- `show` reports story rollups (done/total). A story tagged with epics from two
  portfolios counts toward both — the same behaviour as the board's filter counts.
- `cc/todo/portfolio.json` is gitignored, so the `.sync_backups/` copy written before
  each change is the only undo. Mention it if a change looks wrong.
