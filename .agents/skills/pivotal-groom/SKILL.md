---
name: pivotal-groom
description: >
  Review the Pivotal Claw board and suggest grooming: which stories are being ignored,
  which titles need rewording for clarity, and which stories are in the wrong epic or
  no epic at all. Trigger this skill when the user runs /pivotal-groom or asks to
  "groom the board", "review my stories", "clean up the backlog", "what's stale",
  "which stories are duplicates", "are my stories in the right epics", or "grooming
  session". For adding a story use the pivotal skill; for moving epics between
  portfolios use pivotal_portfolio.
---

# Pivotal Story Groomer

Grooming is a **conversation**, not a batch job. The script produces an inventory and a
set of exact findings; you supply the judgement; the user approves each change. Never
apply an edit the user has not seen and agreed to.

```bash
python3 .agents/skills/pivotal-groom/scripts/groom.py scan
python3 .agents/skills/pivotal-groom/scripts/groom.py scan --epic resume_wiki
python3 .agents/skills/pivotal-groom/scripts/groom.py scan --portfolio "Job Search"
python3 .agents/skills/pivotal-groom/scripts/groom.py scan --json          # full inventory
python3 .agents/skills/pivotal-groom/scripts/groom.py scan --html /tmp/review.html
```

`--todo-dir` points at another board (`--todo-dir demo-data`); `--include-done` adds the
done lane, which is worth doing when hunting duplicates — a story duplicating something
already shipped is the easiest close on the board.

## The Division Of Labour That Matters

**The script only reports what is checkable**: duplicate clusters, junk rows, stories with
no epic, hashtags never declared in `epics.md`, epics owned by no portfolio, epics with no
open stories, stories whose only portfolio is archived, and what is in Doing right now.

**You do everything that needs reading and judgement.** There is deliberately no
"importance score". An earlier version ranked stories by lane depth, last-touched date and
focus-portfolio membership; measured on the real board those fire on 40–64% of stories, and
lane depth is *anti*-correlated with attention because lane moves append to the bottom of
the file. It produced separators and shopping reminders at the top. It was removed. If the
user asks which stories are being neglected, read the inventory (`scan --json` gives every
open story) and answer with judgement, naming the reason.

## Working A Grooming Session

1. **Scan first.** Never propose an edit for a story you have not seen in scan output.
2. **Start where the findings are exact** — duplicates and junk. They need a decision, not
   an opinion, and clearing them makes the rest of the board readable.
3. **Then categorization.** Stories with no epic are invisible to every epic pane and
   portfolio filter, so they are the highest-value fix per unit of effort.
4. **Then clarity, on a handful of stories.** Never rewrite the board wholesale.
5. **Present, then apply.** Show the current title and the proposed one side by side and
   wait. Apply one story at a time.

## Rewording Rules

- Keep the user's meaning and voice. Fix the *shape* — lead with a verb, name one outcome —
  do not invent scope, and do not inflate a one-liner into a paragraph.
- Titles stay short and scannable. Longer context belongs in a note
  (`.agents/skills/pivotal/scripts/add_note.py`), never crammed into the title.
- A typo in a title is worth fixing on its own; a title that is merely terse but clear is not.
- Leave deliberate shorthand alone. `terse` in the scan output is a shape flag, not a verdict.

## Categorization Rules

- **Epic tags must be copied from `cc/todo/epics.md` verbatim**, typos included — real tags
  include `#commited_code`. `apply` rejects a tag that is not declared there.
- **A hashtag that is not in `epics.md` is not an epic**, and `--drop-epic` refuses it. This
  is a safety rule, not a limitation: removal is a regex over the whole line, and the board
  uses hashtags inside sentences. Dropping `#icebox` from *"addition should go into #icebox"*
  deletes the word and destroys the sentence. Report such tags to the user and let them
  decide whether to declare the epic or reword the story by hand.
- **One epic per story is the norm.** A story spanning two portfolios is usually two stories.
- **Portfolio-level moves are not this skill's job.** When the finding is "this epic belongs
  to no portfolio", hand off to `pivotal_portfolio`; do not try to fix it here.

## Applying A Change

```bash
python3 .agents/skills/pivotal-groom/scripts/groom.py apply t:8f2n1qz \
  --retitle "Fix the resume exporter dropping the last page" \
  --add-epic resume_visual_formatting --drop-epic board_ux --lane todo --dry-run
```

- One story per invocation. All edits land in a single write, so a retitle plus an epic
  swap cannot half-apply.
- `--dry-run` prints the before and after line and writes nothing. Use it whenever the user
  has not seen the exact resulting line yet.
- `--lane` is `todo`, `doing`, `icebox`, or `done`.

## Safety

- **Board data is gitignored — there is no git undo.** The only recovery is
  `cc/todo/.sync_backups/`, which keeps **one backup per file per day and overwrites it**.
  A long grooming session therefore has effectively one restore point. Say so before a
  run of edits, and prefer working through a handful of changes the user has approved over
  a sweep.
- Deleting or merging duplicate stories is **not supported** — the board has no delete
  primitive. Report the cluster and let the user remove the extras on the board.
- The script writes directly to the Markdown files, bypassing the server, so tell the user
  to reload `http://localhost:5056` afterwards.
- To rehearse, copy the board to a temp directory and pass `--todo-dir`. Do not rehearse in
  `demo-data/` — it is tracked in git and is what the public demo serves.

## Reporting Back

- Always name a story as `t:xxxxxxx "Its Title"`, never the bare ID.
- Refer to epics and portfolios by name.
- When you propose a categorization change, say which portfolio the new epic pulls the story
  into — that is usually what the user actually cares about.
