#!/usr/bin/env python3
"""Groom the Pivotal Claw board: report exact findings, apply approved edits.

The script reports only things that are *checkable* — duplicate clusters, junk rows,
stories with no epic, hashtags never declared in epics.md, epics owned by no portfolio,
work-in-progress overflow. It deliberately does not score stories for importance or
judge wording: those are Claude's job, reading the inventory this produces.

An earlier draft did score "neglect" from lane depth, last-touched data and focus
membership. Measured on the real board those fire on 40-64% of stories, and lane depth
is anti-correlated with attention because lane moves append to the bottom of a file.
The score was noise and was removed.
"""

from __future__ import annotations

import argparse
import html
import itertools
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "cc" / "todo"))

import sync_changes  # noqa: E402
import portfolio_store  # noqa: E402

LANE_FILES = {"todo": "todo.md", "doing": "doing.md", "icebox": "icebox.md", "done": "done.md"}
OPEN_LANES = ("todo", "doing", "icebox")
LANE_STATUS = {"todo": "Pending", "doing": "In progress", "icebox": "On schedule", "done": "Done"}
EPIC_HEADING_RE = re.compile(r"^#+\s+#([A-Za-z0-9_]+)\s*$", re.M)
URL_RE = re.compile(r"https?://\S+")
WORD_RE = re.compile(r"[a-z0-9]+")
# Calibrated against the real board: 0.8 keeps every genuine duplicate (including
# "descirption"/"description" typo pairs and two differently-worded reports of the same
# bug) while rejecting near-misses that are actually distinct stories — "pull in old
# resumes from google drive" vs "...from hard drive" scores 0.75 and is not a duplicate.
DUPLICATE_THRESHOLD = 0.8
DEFAULT_BOARD_URL = "http://localhost:5056"


# --------------------------------------------------------------------------- read


def read_board(todo_dir: Path, include_done: bool = False) -> list[dict]:
    lanes = OPEN_LANES + (("done",) if include_done else ())
    stories = []
    for lane in lanes:
        path = todo_dir / LANE_FILES[lane]
        if not path.exists():
            continue
        for depth, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
            match = sync_changes.ID_RE.search(raw)
            if not match:
                continue
            _, _, rest, _ = sync_changes.parse_line_parts(raw)
            title = sync_changes.TAG_RE.sub("", sync_changes.ID_RE.sub("", rest))
            title = sync_changes.COMPLETED_RE.sub(" ", title)
            stories.append(
                {
                    "id": match.group(1),
                    "lane": lane,
                    "depth": depth,
                    "title": re.sub(r"\s+", " ", title).strip(),
                    "raw": raw,
                    "tags": sync_changes.TAG_RE.findall(rest),
                    "source_file": str(path),
                }
            )
    return stories


def read_epics(todo_dir: Path) -> list[str]:
    path = todo_dir / "epics.md"
    return EPIC_HEADING_RE.findall(path.read_text(encoding="utf-8")) if path.exists() else []


def read_portfolios(todo_dir: Path) -> list[dict]:
    try:
        return portfolio_store.load_portfolios(todo_dir=todo_dir).get("items", [])
    except Exception:
        path = todo_dir / "portfolio.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8")).get("items", [])


# ------------------------------------------------------------------------ analyse


def words(text: str) -> set[str]:
    return set(WORD_RE.findall(text.lower()))


def duplicate_clusters(stories: list[dict]) -> list[dict]:
    """Union-find over pairwise similarity, so N identical stories give one cluster.

    Reporting pairs instead would turn a six-way duplicate into fifteen rows.
    """
    parent = {s["id"]: s["id"] for s in stories}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in itertools.combinations(stories, 2):
        wa, wb = words(a["title"]), words(b["title"])
        if not wa or not wb:
            continue
        if len(wa & wb) / len(wa | wb) >= DUPLICATE_THRESHOLD:
            parent[find(a["id"])] = find(b["id"])

    groups: dict[str, list[dict]] = {}
    for story in stories:
        groups.setdefault(find(story["id"]), []).append(story)

    clusters = [
        {
            "ids": [s["id"] for s in group],
            "titles": [s["title"] for s in group],
            "lanes": [s["lane"] for s in group],
            "size": len(group),
        }
        for group in groups.values()
        if len(group) > 1
    ]
    return sorted(clusters, key=lambda c: -c["size"])


def shape_flags(story: dict) -> list[str]:
    """Mechanical shape only. Whether a title *reads* well is Claude's call."""
    flags = []
    count = len(story["title"].split())
    if count and count < 5:
        flags.append("terse")
    if count > 25:
        flags.append("overlong")
    if URL_RE.search(story["title"]):
        flags.append("raw-url")
    return flags


def is_junk(story: dict) -> bool:
    return len(words(story["title"])) <= 1


def analyse(todo_dir: Path, include_done=False, epic=None, portfolio=None) -> dict:
    stories = read_board(todo_dir, include_done=include_done)
    epics = read_epics(todo_dir)
    portfolios = read_portfolios(todo_dir)

    owner = {tag: p for p in portfolios for tag in p.get("epic_tags", [])}
    live_owner = {t: p for t, p in owner.items() if not p.get("archived")}

    for story in stories:
        names = [owner[t]["name"] for t in story["tags"] if t in owner]
        story["portfolios"] = sorted(set(names))
        story["focus"] = any(owner[t].get("focus") for t in story["tags"] if t in owner)
        story["undeclared_tags"] = [t for t in story["tags"] if t not in epics]
        story["flags"] = shape_flags(story)

    if epic:
        stories = [s for s in stories if epic.lstrip("#") in s["tags"]]
    if portfolio:
        wanted = portfolio.lower()
        stories = [
            s for s in stories if any(name.lower() == wanted for name in s["portfolios"])
        ]

    scoped = {s["id"] for s in stories}
    junk = [s for s in stories if is_junk(s)]
    junk_ids = {s["id"] for s in junk}
    real = [s for s in stories if s["id"] not in junk_ids]

    undeclared: dict[str, list[str]] = {}
    for story in stories:
        for tag in story["undeclared_tags"]:
            undeclared.setdefault(tag, []).append(story["id"])

    epic_counts = {e: sum(1 for s in stories if e in s["tags"]) for e in epics}

    findings = {
        "duplicates": duplicate_clusters(real),
        "junk": [brief(s) for s in junk],
        "no_epic": [brief(s) for s in stories if not s["tags"]],
        "undeclared_tags": [
            {"tag": tag, "ids": ids, "count": len(ids)}
            for tag, ids in sorted(undeclared.items(), key=lambda kv: -len(kv[1]))
        ],
        "epics_without_portfolio": [
            {"epic": e, "stories": epic_counts[e]}
            for e in sorted(epics, key=lambda e: -epic_counts[e])
            if e not in live_owner and epic_counts[e] > 0
        ],
        "dead_epics": [{"epic": e} for e in sorted(epics) if epic_counts[e] == 0],
        "archived_portfolio": [
            brief(s)
            for s in stories
            if s["tags"] and all(t in owner and owner[t].get("archived") for t in s["tags"])
        ],
        "multi_portfolio": [brief(s) for s in stories if len(s["portfolios"]) > 1],
        "wip": [brief(s) for s in stories if s["lane"] == "doing"],
    }

    return {
        "board": {
            "todo_dir": str(todo_dir),
            "counts": {lane: sum(1 for s in stories if s["lane"] == lane) for lane in LANE_FILES},
            "total": len(stories),
            "epics": len(epics),
            "portfolios": [
                {"name": p["name"], "focus": bool(p.get("focus")), "archived": bool(p.get("archived"))}
                for p in portfolios
            ],
        },
        "stories": [
            {k: v for k, v in s.items() if k not in ("raw", "source_file")} for s in stories
        ],
        "findings": findings,
    }


def brief(story: dict) -> dict:
    return {
        "id": story["id"],
        "title": story["title"],
        "lane": story["lane"],
        "tags": story["tags"],
    }


# ------------------------------------------------------------------------- output


def print_text(report: dict) -> None:
    f = report["findings"]
    b = report["board"]
    counts = ", ".join(f"{n} {lane}" for lane, n in b["counts"].items() if n)
    print(f"BOARD  {b['total']} stories ({counts})  ·  {b['epics']} epics\n")

    print("== NEEDS A DECISION " + "=" * 40)
    if f["duplicates"]:
        print(f"\n  Duplicate clusters ({len(f['duplicates'])}):")
        for cluster in f["duplicates"]:
            print(f"    x{cluster['size']}  {cluster['titles'][0][:78]}")
            print(f"          {' '.join(cluster['ids'])}")
    if f["junk"]:
        print(f"\n  Junk rows ({len(f['junk'])}):")
        for s in f["junk"]:
            print(f"    {s['id']}  \"{s['title']}\"  [{s['lane']}]")
    if f["wip"]:
        print(f"\n  In progress at once ({len(f['wip'])}):")
        for s in f["wip"]:
            print(f"    {s['id']}  {s['title'][:70]}")
    if not (f["duplicates"] or f["junk"] or f["wip"]):
        print("\n  nothing")

    print("\n== CLARITY " + "=" * 49)
    shaped = [s for s in report["stories"] if s["flags"]]
    if shaped:
        for s in shaped:
            print(f"  {s['id']}  [{','.join(s['flags'])}]  {s['title'][:66]}")
    else:
        print("  nothing")
    print("\n  (shape only — read the titles and judge the wording yourself)")

    print("\n== CATEGORIZATION " + "=" * 42)
    if f["no_epic"]:
        print(f"\n  No epic ({len(f['no_epic'])}) — invisible to every epic pane and portfolio filter:")
        for s in f["no_epic"]:
            print(f"    {s['id']}  [{s['lane']}]  {s['title'][:66]}")
    if f["undeclared_tags"]:
        print(f"\n  Hashtags never declared in epics.md ({len(f['undeclared_tags'])}):")
        for u in f["undeclared_tags"]:
            print(f"    #{u['tag']:<28} {u['count']} story(s)  {' '.join(u['ids'][:4])}")
        print("    (these may be labels or prose — --drop-epic refuses them on purpose)")
    if f["epics_without_portfolio"]:
        print(f"\n  Epics owned by no portfolio ({len(f['epics_without_portfolio'])}):")
        for e in f["epics_without_portfolio"]:
            print(f"    #{e['epic']:<28} {e['stories']} story(s) hidden from portfolio filters")
    if f["dead_epics"]:
        print(f"\n  Epics with no open stories ({len(f['dead_epics'])}): "
              + " ".join("#" + e["epic"] for e in f["dead_epics"]))
    if f["archived_portfolio"]:
        print(f"\n  Open stories whose only portfolio is archived ({len(f['archived_portfolio'])}):")
        for s in f["archived_portfolio"]:
            print(f"    {s['id']}  {s['title'][:66]}")
    if f["multi_portfolio"]:
        print(f"\n  Stories spanning several portfolios ({len(f['multi_portfolio'])}):")
        for s in f["multi_portfolio"]:
            print(f"    {s['id']}  {s['title'][:66]}")


CSS = """
:root{--bg:#0e1117;--panel:#161b24;--line:#2a3444;--fg:#e6edf3;--dim:#8b98a9;
--accent:#4da3ff;--good:#3fb950;--warn:#e3b341;--bad:#f85149;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);padding:0 0 5rem;
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{max-width:1000px;margin:0 auto;padding:0 1.5rem}
header{border-bottom:1px solid var(--line);padding:2.5rem 0 1.5rem;margin-bottom:2rem}
h1{font-size:1.9rem;margin:0 0 .3rem;letter-spacing:-.02em}
.sub{color:var(--dim);margin:0}
h2{font-size:1.25rem;margin:2.5rem 0 .2rem}
h3{font-size:1rem;margin:1.5rem 0 .4rem;color:var(--dim);font-weight:600;
text-transform:uppercase;letter-spacing:.05em}
.lede{color:var(--dim);margin:.2rem 0 1rem;max-width:75ch;font-size:.95rem}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.8rem;margin:1.2rem 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem}
.stat .v{font-size:1.8rem;font-weight:600;font-family:var(--mono)}
.stat .k{color:var(--dim);font-size:.8rem;margin-top:.2rem}
.stat.warn .v{color:var(--warn)}.stat.bad .v{color:var(--bad)}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:1rem 1.2rem;margin:.6rem 0}
code{font-family:var(--mono);font-size:.85em}
.id{color:var(--accent);background:#4da3ff14;padding:.05rem .3rem;border-radius:4px}
.tag{font-family:var(--mono);font-size:.75rem;color:var(--good);background:#3fb95014;
padding:.05rem .35rem;border-radius:4px}
.lane{font-family:var(--mono);font-size:.7rem;color:var(--dim);border:1px solid var(--line);
padding:0 .35rem;border-radius:4px;margin-left:.4rem}
.row{padding:.35rem 0;border-bottom:1px solid var(--line);font-size:.92rem}
.row:last-child{border:0}
.cluster{display:flex;gap:.9rem;padding:.6rem 0;border-bottom:1px solid var(--line)}
.cluster:last-child{border:0}
.cnt{font-family:var(--mono);color:var(--bad);font-weight:600;min-width:2rem}
.flag{font-family:var(--mono);font-size:.7rem;color:var(--warn);background:#e3b34114;
padding:0 .3rem;border-radius:4px;margin-right:.3rem}
.empty{color:var(--dim);font-style:italic}
a.id{text-decoration:none}
a.id:hover{background:#4da3ff2e;text-decoration:underline}
.story{padding:.5rem 0;border-bottom:1px solid var(--line);scroll-margin-top:1rem}
.story:last-child{border:0}
.story:target{background:#4da3ff14;border-radius:6px;padding:.5rem .6rem;
box-shadow:inset 3px 0 0 var(--accent)}
.story-h{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin-bottom:.15rem}
.story-t{font-size:.95rem}
.pf{font-family:var(--mono);font-size:.7rem;color:var(--accent);background:#4da3ff14;
padding:0 .35rem;border-radius:4px}
.out{margin-left:auto;font-size:.75rem;color:var(--dim);text-decoration:none;white-space:nowrap}
.out:hover{color:var(--accent)}
.inventory{max-height:none}
footer{color:var(--dim);font-size:.85rem;border-top:1px solid var(--line);
margin-top:3rem;padding-top:1rem}
"""


def render_html(report: dict, board_url: str = DEFAULT_BOARD_URL) -> str:
    f, b, e = report["findings"], report["board"], html.escape
    base = board_url.rstrip("/")

    # The board deep-links already: processDeepLink() in pivotal_tasks.html reads ?id=,
    # force-opens the pane the story's status belongs to, then selects and scroll-highlights
    # the card. The portfolio/epic/dir params matter just as much: each filter is sticky
    # across visits, and a story excluded by one never renders, so the jump silently fails.
    def link(story_id: str) -> str:
        href = f"{base}/?portfolio=all&epic=all&dir=all&id={story_id}"
        return (f'<a class="id" href="{e(href)}" target="_blank" rel="noopener">'
                f'{e(story_id)}</a>')

    def rows(items, extra=lambda s: ""):
        if not items:
            return '<p class="empty">nothing</p>'
        return "".join(
            f'<div class="row">{link(s["id"])} {extra(s)}'
            f'{e(s["title"][:120])}<span class="lane">{e(s["lane"])}</span>'
            + "".join(f' <span class="tag">#{e(t)}</span>' for t in s.get("tags", []))
            + "</div>"
            for s in items
        )

    clusters = "".join(
        f'<div class="cluster"><div class="cnt">x{c["size"]}</div><div>'
        + "".join(f'<div>{e(t[:110])}</div>' for t in c["titles"])
        + '<div style="margin-top:.3rem">'
        + " ".join(link(i) for i in c["ids"])
        + "</div></div></div>"
        for c in f["duplicates"]
    ) or '<p class="empty">nothing</p>'

    undeclared = "".join(
        f'<div class="row"><code class="tag">#{e(u["tag"])}</code> {u["count"]} story(s) — '
        + " ".join(link(i) for i in u["ids"][:6])
        + "</div>"
        for u in f["undeclared_tags"]
    ) or '<p class="empty">nothing</p>'

    orphans = "".join(
        f'<div class="row"><code class="tag">#{e(o["epic"])}</code> — '
        f'{o["stories"]} story(s) hidden from every portfolio filter</div>'
        for o in f["epics_without_portfolio"]
    ) or '<p class="empty">nothing</p>'

    shaped = [s for s in report["stories"] if s["flags"]]
    clarity = "".join(
        f'<div class="row">'
        + "".join(f'<span class="flag">{e(fl)}</span>' for fl in s["flags"])
        + f'{link(s["id"])} {e(s["title"][:110])}</div>'
        for s in shaped
    ) or '<p class="empty">nothing</p>'

    dead = " ".join(f'<code class="tag">#{e(d["epic"])}</code>' for d in f["dead_epics"])

    inventory = "".join(
        f'<div class="story" id="s-{e(s["id"])}">'
        f'<div class="story-h">{link(s["id"])}'
        f'<span class="lane">{e(s["lane"])}</span>'
        + "".join(f'<span class="tag">#{e(t)}</span>' for t in s["tags"])
        + "".join(f'<span class="pf">{e(p)}</span>' for p in s["portfolios"])
        + "".join(f'<span class="flag">{e(fl)}</span>' for fl in s["flags"])
        + f'<a class="out" href="{e(base)}/?portfolio=all&amp;epic=all&amp;dir=all'
        f'&amp;id={e(s["id"])}" target="_blank" rel="noopener">open on board &#8599;</a></div>'
        f'<div class="story-t">{e(s["title"])}</div></div>'
        for s in sorted(report["stories"], key=lambda s: (s["lane"], s["title"].lower()))
    )

    # charset first: the page is written as UTF-8, and without this a browser opening it
    # from file:// falls back to Latin-1 and renders "·" as "Â·".
    return f"""<meta charset="utf-8">
<title>Board Grooming Review</title>
<style>{CSS}</style>
<div class="wrap">
<header>
<h1>Board grooming review</h1>
<p class="sub">{b['total']} stories · {b['epics']} epics · <code>{e(b['todo_dir'])}</code></p>
</header>

<div class="stats">
<div class="stat"><div class="v">{b['total']}</div><div class="k">stories reviewed</div></div>
<div class="stat bad"><div class="v">{sum(c['size'] for c in f['duplicates'])}</div>
<div class="k">in {len(f['duplicates'])} duplicate clusters</div></div>
<div class="stat bad"><div class="v">{len(f['junk'])}</div><div class="k">junk rows</div></div>
<div class="stat warn"><div class="v">{len(f['no_epic'])}</div><div class="k">no epic</div></div>
<div class="stat warn"><div class="v">{len(f['epics_without_portfolio'])}</div>
<div class="k">epics with no portfolio</div></div>
<div class="stat warn"><div class="v">{len(f['wip'])}</div><div class="k">in progress at once</div></div>
</div>

<h2>Needs a decision</h2>
<p class="lede">Exact findings — nothing here is a guess.</p>
<h3>Duplicate clusters</h3><div class="card">{clusters}</div>
<h3>Junk rows</h3><div class="card">{rows(f['junk'])}</div>
<h3>In progress at once</h3><div class="card">{rows(f['wip'])}</div>

<h2>Clarity</h2>
<p class="lede">Shape flags only — whether a title actually reads well is a judgement call,
made by reading it, not by counting words.</p>
<div class="card">{clarity}</div>

<h2>Categorization</h2>
<h3>No epic — invisible to every epic pane and portfolio filter</h3>
<div class="card">{rows(f['no_epic'])}</div>
<h3>Hashtags never declared in epics.md</h3>
<p class="lede">These may be deliberate labels, or prose that happens to start with #.
<code>--drop-epic</code> refuses them on purpose.</p>
<div class="card">{undeclared}</div>
<h3>Epics owned by no portfolio</h3><div class="card">{orphans}</div>
<h3>Epics with no open stories</h3>
<div class="card">{dead or '<p class="empty">nothing</p>'}</div>
<h3>Only portfolio is archived</h3><div class="card">{rows(f['archived_portfolio'])}</div>
<h3>Spanning several portfolios</h3><div class="card">{rows(f['multi_portfolio'])}</div>

<h2>All stories</h2>
<p class="lede">Every story in this review, in full — readable with or without a running
board. Story ids link straight to the story on <code>{e(base)}</code>, which must be running
for the links to resolve.</p>
<div class="card inventory">{inventory}</div>

<footer>Generated by <code>/pivotal-groom</code> — read-only; no board data was written.</footer>
</div>
"""


# -------------------------------------------------------------------------- apply


def apply_changes(args) -> int:
    todo_dir = Path(args.todo_dir)
    stories = {s["id"]: s for s in read_board(todo_dir, include_done=True)}
    story = stories.get(args.task_id)
    if story is None:
        print(f"error: story {args.task_id} not found on the board", file=sys.stderr)
        return 1

    epics = read_epics(todo_dir)
    for tag in list(args.add_epic) + list(args.drop_epic):
        clean = tag.lstrip("#")
        if clean not in epics:
            verb = "add" if tag in args.add_epic else "drop"
            print(
                f"error: #{clean} is not an epic declared in epics.md, refusing to {verb} it.\n"
                f"       Hashtags that are labels or prose must not be edited by this tool "
                f"(dropping #{clean} would delete the word from the story text).\n"
                f"       Declare it in epics.md first, or edit the story on the board.",
                file=sys.stderr,
            )
            return 2

    expected = story["raw"]
    lane = args.lane or story["lane"]
    added = [t.lstrip("#") for t in args.add_epic]
    dropped = [t.lstrip("#") for t in args.drop_epic]

    # Always route through a statusChanges entry — even for a same-lane edit. It is the
    # only path in apply_sync that folds title, tag additions and tag removals into one
    # rebuild_line call. Sending them as separate payload entries half-applies: the text
    # edit lands, then the tag edits conflict because expected_text no longer matches.
    payload = {
        "statusChanges": [
            {
                "task_id": args.task_id,
                "source_file": story["source_file"],
                "expected_text": expected,
                "new_status": LANE_STATUS[lane],
            }
        ]
    }
    if args.retitle:
        payload["textEdits"] = [
            {
                "task_id": args.task_id,
                "source_file": story["source_file"],
                "expected_text": expected,
                "new_text": args.retitle,
            }
        ]
    if added:
        payload["tagAdditions"] = [
            {
                "task_id": args.task_id,
                "source_file": story["source_file"],
                "expected_text": expected,
                "tags": added,
            }
        ]
    if dropped:
        payload["tagRemovals"] = [
            {
                "task_id": args.task_id,
                "source_file": story["source_file"],
                "expected_text": expected,
                "tags": dropped,
            }
        ]

    preview = sync_changes.rebuild_line(
        story["raw"] + "\n",
        new_status=LANE_STATUS[lane] if args.lane else None,
        new_text=args.retitle,
        added_tags=added,
        removed_tags=dropped,
    ).rstrip("\n")

    if args.dry_run:
        print(f"before: {story['raw']}")
        print(f"after : {preview}")
        if args.lane and args.lane != story["lane"]:
            print(f"lane  : {story['lane']} -> {args.lane}")
        print("\n(dry run — nothing written)")
        return 0

    receipt = sync_changes.apply_sync(payload, todo_dir=todo_dir)
    if receipt["errors"] or receipt["conflicts"]:
        print(f"error: {json.dumps(receipt, indent=2)}", file=sys.stderr)
        return 3

    # Read the board back and prove the change actually landed. There is no undo for board
    # data, so "the receipt said ok" is not good enough: a write that silently went
    # somewhere else must fail loudly rather than be reported as success.
    after = {s["id"]: s for s in read_board(todo_dir, include_done=True)}.get(args.task_id)
    problems = []
    if after is None:
        problems.append("story is no longer on the board")
    else:
        if args.retitle and args.retitle.strip() not in after["raw"]:
            problems.append("new title not present")
        for tag in added:
            if tag not in after["tags"]:
                problems.append(f"#{tag} was not added")
        for tag in dropped:
            if tag in after["tags"]:
                problems.append(f"#{tag} was not removed")
        if args.lane and after["lane"] != lane:
            problems.append(f"story is in {after['lane']}, expected {lane}")
    if problems:
        print(
            "error: the write did not land as expected in "
            f"{todo_dir} ({'; '.join(problems)}). Board may be unchanged.",
            file=sys.stderr,
        )
        return 4

    print(f"before: {story['raw']}")
    print(f"after : {preview}")
    if args.lane and args.lane != story["lane"]:
        print(f"moved : {story['lane']} -> {args.lane}")
    print("\nReload http://localhost:5056 to see the change.")
    return 0


# ---------------------------------------------------------------------------- cli


def main(argv=None) -> int:
    default_todo_dir = str(REPO_ROOT / "cc" / "todo")
    # SUPPRESS, not a real default: --todo-dir is accepted both before and after the
    # subcommand, and with an ordinary default the subparser silently overwrites a value
    # given before it — which pointed a write meant for a scratch copy at the real board.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--todo-dir", default=argparse.SUPPRESS,
                        help="board directory (default: the repo's cc/todo)")

    parser = argparse.ArgumentParser(description="Groom the Pivotal Claw board.", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", parents=[common], help="report exact grooming findings")
    scan.add_argument("--json", action="store_true")
    scan.add_argument("--html", metavar="PATH", help="write an HTML review page")
    scan.add_argument("--include-done", action="store_true")
    scan.add_argument("--epic", help="only stories carrying this epic tag")
    scan.add_argument("--portfolio", help="only stories in this portfolio (by name)")
    scan.add_argument("--board-url", default=DEFAULT_BOARD_URL,
                      help="board the HTML story links point at (default: %(default)s)")

    ap = sub.add_parser("apply", parents=[common], help="apply one approved change to one story")
    ap.add_argument("task_id")
    ap.add_argument("--retitle")
    ap.add_argument("--add-epic", action="append", default=[])
    ap.add_argument("--drop-epic", action="append", default=[])
    ap.add_argument("--lane", choices=sorted(LANE_FILES))
    ap.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    args.todo_dir = getattr(args, "todo_dir", default_todo_dir)

    if args.command == "apply":
        return apply_changes(args)

    report = analyse(
        Path(args.todo_dir),
        include_done=args.include_done,
        epic=args.epic,
        portfolio=args.portfolio,
    )
    if args.html:
        Path(args.html).write_text(render_html(report, args.board_url), encoding="utf-8")
        print(f"wrote {args.html}")
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        print()
    elif not args.html:
        print_text(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
