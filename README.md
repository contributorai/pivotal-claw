# Pivotal Claw

Pivotal Claw is a local-first Kanban board for coordinating human and AI-agent
work. Markdown remains the editable source of truth; an optional Postgres event
ledger replicates through ClickPipe into ClickHouse for a live Agent Work Pulse.

The pulse answers three operational questions without putting analytics in the
board's write path:

- How many agent sessions are active?
- How many stories finished in the last 24 hours?
- What is the median Doing-to-Done cycle time?

## Overview

[Watch the overview deck with voiceover](https://youtu.be/6_9XW6UhPx8).

## Quick start

Requires Python 3.12+.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r cc/pivotal/requirements.txt -r cc/pivotal/requirements-postgres.txt -r cc/pivotal/requirements-dev.txt
PIVOTAL_TODO_DIR="$PWD/demo-data" cc/pivotal/start.sh
```

Open [http://localhost:5056](http://localhost:5056) for the board and
[http://localhost:5056/sessions_history](http://localhost:5056/sessions_history)
for the Agent Work Pulse. With no database configuration, the board is fully
functional and the pulse clearly reports that ClickHouse is not configured.

Run the tests:

```sh
python -m pytest cc/pivotal/tests/
```

## Architecture

```text
Markdown + JSON board (source of truth)
        | best-effort event mirror
        v
Postgres (transactional event ledger)
        | ClickPipe CDC
        v
ClickHouse (analytics replica)
        | SELECT-only HTTPS query
        v
/api/agent-work-pulse -> /sessions_history
```

Postgres and ClickHouse are optional at runtime but are both part of the full
hackathon deployment. A failed database write never blocks a board transition;
a failed ClickHouse query disables only the metric cards.

### Full Agent Work Pulse configuration

Install both requirements files, then inject these values through your hosting
platform's secret manager:

| Variable | Purpose |
| --- | --- |
| `PIVOTAL_POSTGRES_DSN` | TLS Postgres connection string for the event ledger |
| `PIVOTAL_CLICKHOUSE_URL` | ClickHouse HTTPS endpoint, including port |
| `PIVOTAL_CLICKHOUSE_USER` | Dedicated read-only pulse user |
| `PIVOTAL_CLICKHOUSE_PASSWORD` | Injected ClickHouse password |
| `PIVOTAL_CLICKHOUSE_PASSWORD_FILE` | Alternative path to a mounted secret file |

The ClickHouse account needs `SELECT` only on the replicated activity-event and
agent-session tables. See [the event-store and CDC guide](docs/postgres-event-store.md)
for schema, table overrides, failure behavior, and setup details.

## Demo flow

1. Open the board and move a fictional story into Doing, then Done.
2. Show the corresponding append-only event rows in Postgres.
3. Open `/sessions_history` and refresh the Agent Work Pulse.
4. Show the replicated ClickHouse rows and explain the normal asynchronous CDC
   delay.
5. Stop or misconfigure the analytics connection and show that the board still
   works while only the pulse becomes unavailable.

The managed acceptance run verified both initial snapshots, INSERT replication,
UPDATE replication, a least-privilege reader, and desktop/mobile rendering.
Exact evidence is in [the verification record](docs/clickhouse-agent-work-pulse-verification.md).

## Public-release safety

Real board Markdown/JSON, sync backups, generated reports, and local worktrees
are ignored. `scripts/build_public_release.py` builds an allowlisted release
tree, and `scripts/audit_public_release.py` rejects personal names, machine home
paths, email addresses, credential-bearing URLs, private keys, and common token
formats. CI runs tests, builds and audits the package, scans history with
Gitleaks, and runs CodeQL.

Build the same clean release locally:

```sh
python scripts/build_public_release.py /tmp/pivotal-claw-public
python scripts/audit_public_release.py /tmp/pivotal-claw-public
```

Publish that tree as a new repository with a fresh root commit. Do not make the
private development repository or its historical commits public.

## License

[MIT](LICENSE)
