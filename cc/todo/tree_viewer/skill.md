---
name: pivotal
description: >
  Work with a Pivotal Kanban board over HTTP — read stories and epics, add
  stories, attach notes, move stories between lanes, and give first-time users
  a guided tour. Trigger this skill when the user runs /pivotal, mentions the
  Pivotal board or the pivotal-kanban-demo site, or asks to add a story,
  show the board, or get a tour.
---

# Pivotal Board (remote/HTTP)

Pivotal is a Pivotal-Tracker-inspired Kanban board backed by plain Markdown
files, served by a small Flask app. This skill drives a running board entirely
over its HTTP API — no repository checkout needed.

## Base URL

- Default: `https://pivotal-kanban-demo.onrender.com` (the public demo).
- If the user runs their own instance, use theirs (locally it is
  `http://localhost:5056`). If the `PIVOTAL_URL` environment variable is set,
  prefer it.

Call the base URL `$PIVOTAL` below.

**Demo etiquette:** the demo board is shared and ephemeral — anyone can see
edits, and the fictional dataset resets on every redeploy or instance restart.
Feel free to add test stories, but keep content friendly; there is no auth.

## Concepts (30 seconds)

- **Stories** are single Markdown checklist lines. Each has an immutable ID of
  the form `t:xxxxxxx` (7 lowercase alphanumerics).
- **Lanes** map to status strings and Markdown files:
  Icebox = `On Schedule` (`icebox.md`), Todo = `Pending` (`todo.md`),
  Doing = `In Progress` (`doing.md`), Done = `Done` (`done.md`).
- **Epics** are hashtag-style tags on stories (`#some_epic`); epic metadata
  (description, color, project dir) lives in `epics.md` and appears in the
  data payload as `epic_meta`.
- **Portfolio** groups epics into higher-level initiatives (`/portfolio` page).

## Read the board

```bash
curl -s "$PIVOTAL/todo_data.js" | sed -e 's/^window.todoData = //' -e 's/;[[:space:]]*$//'
```

That yields one JSON object. The parts you usually need:

- `tasks[]` — stories, each with `task_id`, `text`, `status`, `tags[]`,
  `source_file`, `line_number`, and nested `children[]` (walk recursively).
- `epic_meta` — per-tag epic info (`description`, `color`, `dir`).
- `portfolio_data` — portfolio groupings.
- `story_sessions` — per-story notes and linked work sessions.

The payload can be large; pipe through `python3 -c` or `jq` to filter rather
than dumping it raw into the conversation.

## Add a story

POST to `/api/sync` with a `newItems` entry. The server generates the
`t:xxxxxxx` ID and prepends the story to the right lane file:

```bash
curl -s -X POST "$PIVOTAL/api/sync" -H 'Content-Type: application/json' -d '{
  "version": 1,
  "newItems": [
    {"text": "Story title here", "status": "Pending", "tags": ["some_epic"]}
  ]
}'
```

- `status` picks the lane (see mapping above; default `Pending`). Omit `tags`
  for an untagged story. If the user names an epic, check `epic_meta` for the
  canonical tag spelling instead of guessing.
- Success receipt lists the text under `appliedNewItems`. To learn the
  assigned ID (needed for notes), re-fetch `todo_data.js` and find the story
  whose `text` starts with your title — it is prepended, so it is first in
  its lane.
- If the user gives no lane, default to Icebox (`On Schedule`).
- When confirming to the user, refer to the story by its title, not just the
  cryptic `t:` ID.

## Attach a note to a story

```bash
curl -s -X POST "$PIVOTAL/api/story-note" -H 'Content-Type: application/json' \
  -d '{"story_id": "t:xxxxxxx", "text": "Longer details go here."}'
```

Use a note when the user has more detail than fits a one-line title.

## Move a story between lanes

POST a `statusChanges` entry to `/api/sync`. All four identifying fields come
from the story's entry in `todo_data.js`:

```bash
curl -s -X POST "$PIVOTAL/api/sync" -H 'Content-Type: application/json' -d '{
  "version": 1,
  "statusChanges": [
    {"task_id": "t:xxxxxxx", "source_file": "<source_file>",
     "line_number": <line_number>, "expected_text": "<text>",
     "new_status": "In Progress"}
  ]
}'
```

`expected_text` must match the current stored text — it is an optimistic-lock
check. If the receipt reports a skip, re-fetch `todo_data.js` and retry with
fresh values.

## First-time tour (onboarding)

If the user is new to Pivotal, walk them through it rather than just listing
API calls:

1. Have them open `$PIVOTAL/` — point out the four lanes, the epic filter in
   the header, search, and the dark-mode toggle (🌙).
2. Fetch the board data yourself and give a two-sentence summary of what is
   currently on the board (how many stories per lane, the main epics).
3. Show them `$PIVOTAL/portfolio` — epics grouped into initiatives.
4. Offer to add their first story ("what's something you're working on?"),
   add it to Icebox, then have them refresh the board to see it appear.
5. Mention the project intro page at `$PIVOTAL/welcome` for links and
   background.

## Out of scope

Editing story titles/tags and the laptop-only features (launching Claude/Codex
work sessions from a story) are not exposed through this skill. Session
launching only works on a local instance, not the cloud demo.
