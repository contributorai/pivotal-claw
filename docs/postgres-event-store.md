# Postgres operational event store (Agent Work Pulse)

The Markdown lane files and the JSON side-cars in `cc/todo/` remain the board's
source of truth. This is a **secondary OLTP ledger**: when a Postgres DSN is
configured, board status transitions and agent session lifecycle are also
written to Postgres, giving downstream OLAP (ClickHouse, via ClickPipes/PeerDB —
story `t:wkns9xx`) a real, transactional source to replicate.

- Code: `cc/pivotal/event_store.py`
- Wiring: `cc/pivotal/server.py` (`create_app` → `app.config["EVENT_STORE"]`)
- Tests: `cc/pivotal/tests/test_event_store.py`, `cc/pivotal/tests/test_server_event_store.py`

## Enabling it

```sh
pip install -r cc/pivotal/requirements-postgres.txt
export PIVOTAL_POSTGRES_DSN='<injected by your secret manager>'
python3 cc/pivotal/event_store.py migrate   # idempotent; also runs at server startup
cc/pivotal/start.sh
```

`PIVOTAL_DATABASE_URL` works as a fallback name. With neither set the store is
**disabled**: no driver import, no connection, no writes — the board behaves
exactly as it does today. `python3 cc/pivotal/event_store.py status` reports
which of the two it is.

## Schema

`pivotal_activity_events` — the append-only ledger.

| Column | Notes |
|---|---|
| `event_id` | `UUID PRIMARY KEY`, deterministic (see below) |
| `event_type` | `transition`, `session_start`, `session_close`; `CHECK` rejects blanks |
| `occurred_at` / `recorded_at` | when it happened / when Postgres saw it |
| `story_id`, `story_text`, `from_status`, `to_status` | board transition detail |
| `provider`, `session_id` | agent session detail |
| `payload` | `JSONB`, e.g. `{"source": "api/sync"}` |

`pivotal_agent_sessions` — minimal *current* runtime state, one row per live or
finished agent session, keyed `(provider, session_id)`. `disposition` is
constrained to `active` / `closed` / `failed`, and `closed_at` must not precede
`started_at`.

Every migration statement is `IF NOT EXISTS`, applied in one transaction, and
safe to re-run on each startup.

## Stable event IDs

`event_id` is a UUIDv5 over the event's natural key — type, timestamp,
`story_id`, `from_status`, `to_status`, `provider`, `session_id` — so replaying
the same event (a retried request, a backfill of `history_events.json`)
converges on the same row rather than duplicating it. Inserts use
`ON CONFLICT (event_id) DO NOTHING`.

`story_text` is deliberately **not** part of the key: retitling a story must not
turn its past transitions into new events.

## Failure behavior

A database that is down, slow, or misconfigured degrades the pulse, never the
board. Migration and every write are wrapped: failures are logged, recorded in
`EventStore.last_error`, and returned as a falsy value. `/api/sync` still writes
the Markdown files and `history_events.json`; a session launch still registers
in `story_sessions.json`.

## Testing

The unit tests inject a fake connection factory and assert on the SQL and
parameters emitted, so `python3 -m pytest cc/pivotal/tests/` needs no database.
To exercise the real schema against a managed instance:

```sh
PIVOTAL_POSTGRES_TEST_DSN='postgresql://…' \
  python3 -m pytest cc/pivotal/tests/test_event_store.py -k Live
```

That test is skipped when the variable is unset.

## ClickHouse CDC and Agent Work Pulse

The managed Postgres tables are replicated into ClickHouse through a ClickPipe.
ClickHouse remains read-only from the application: it serves aggregate pulse
metrics and never participates in a board write.

```text
Markdown/JSON board (source of truth)
        | best-effort mirror
        v
Managed Postgres (OLTP ledger)
        | ClickPipe CDC
        v
ClickHouse (OLAP replica)
        | SELECT-only HTTPS query
        v
GET /api/agent-work-pulse -> /sessions_history cards
```

Configure the read path without committing credentials:

```sh
export PIVOTAL_CLICKHOUSE_URL='https://<service-host>:8443'
export PIVOTAL_CLICKHOUSE_USER='pivotal_pulse_reader'
export PIVOTAL_CLICKHOUSE_PASSWORD_FILE='/run/secrets/pivotal-clickhouse-password'
cc/pivotal/start.sh
```

`PIVOTAL_CLICKHOUSE_PASSWORD` is also supported for platforms that inject
secrets directly. The password-file option keeps the credential out of command
arguments. Optional `PIVOTAL_CLICKHOUSE_DATABASE`,
`PIVOTAL_CLICKHOUSE_ACTIVITY_TABLE`, and `PIVOTAL_CLICKHOUSE_SESSIONS_TABLE`
variables override the default replicated names.

`GET /api/agent-work-pulse` returns active sessions, stories completed in the
last 24 hours, and median Doing-to-Done cycle seconds. With incomplete
configuration or a query failure it returns an `unavailable` status rather than
breaking the board. The ClickHouse user needs only `SELECT` on the two
replicated tables.

See [ClickHouse Agent Work Pulse verification](clickhouse-agent-work-pulse-verification.md)
for the end-to-end acceptance evidence.
