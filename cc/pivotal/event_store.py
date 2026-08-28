"""Environment-gated Postgres operational event store for Agent Work Pulse.

The Markdown lane files and the JSON side-car files remain the board's source of
truth. This module is a *secondary* OLTP ledger: when a Postgres DSN is
configured, board transitions and agent session lifecycle land in
`pivotal_activity_events` and `pivotal_agent_sessions` so downstream OLAP
(ClickHouse, via replication) has a real, transactional source to read from.

Two rules govern everything here:

1. **Gated.** With no DSN configured the store is inert — it never imports a
   driver, never opens a connection, and every write is a no-op.
2. **Never fatal.** A database that is down, slow, or misconfigured degrades the
   pulse, not the board. Write failures are captured in `last_error` and
   swallowed; callers see a falsy return.

Event IDs are deterministic (UUIDv5 over the event's natural key), so replaying
history — a retried request, a backfill of `history_events.json` — converges on
the same rows instead of duplicating them.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DSN_ENV_VARS = ("PIVOTAL_POSTGRES_DSN", "PIVOTAL_DATABASE_URL")

#: Namespace for deterministic event IDs. Changing it re-keys every event.
EVENT_NAMESPACE = uuid.UUID("6f2f2e1a-9f6d-5a3b-8c47-0f0f6b6f1a21")

SESSION_DISPOSITIONS = ("active", "closed", "failed")

ACTIVITY_EVENT_FIELDS = (
    "story_id", "story_text", "from_status", "to_status", "provider", "session_id",
)

#: Fields that make an event *this* event. `story_text` is deliberately absent:
#: a story can be retitled without turning its past transitions into new events.
EVENT_KEY_FIELDS = tuple(name for name in ACTIVITY_EVENT_FIELDS if name != "story_text")

MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS pivotal_activity_events (
        event_id UUID PRIMARY KEY,
        event_type TEXT NOT NULL,
        occurred_at TIMESTAMPTZ NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        story_id TEXT,
        story_text TEXT,
        from_status TEXT,
        to_status TEXT,
        provider TEXT,
        session_id TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        CONSTRAINT pivotal_activity_events_type_not_blank CHECK (btrim(event_type) <> '')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS pivotal_activity_events_occurred_at_idx
        ON pivotal_activity_events (occurred_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS pivotal_activity_events_story_idx
        ON pivotal_activity_events (story_id, occurred_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS pivotal_agent_sessions (
        provider TEXT NOT NULL,
        session_id TEXT NOT NULL,
        story_id TEXT,
        disposition TEXT NOT NULL DEFAULT 'active',
        started_at TIMESTAMPTZ NOT NULL,
        closed_at TIMESTAMPTZ,
        resume_cmd TEXT,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (provider, session_id),
        CONSTRAINT pivotal_agent_sessions_provider_not_blank CHECK (btrim(provider) <> ''),
        CONSTRAINT pivotal_agent_sessions_disposition_known
            CHECK (disposition IN ('active', 'closed', 'failed')),
        CONSTRAINT pivotal_agent_sessions_closed_after_started
            CHECK (closed_at IS NULL OR closed_at >= started_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS pivotal_agent_sessions_story_idx
        ON pivotal_agent_sessions (story_id, started_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS pivotal_agent_sessions_active_idx
        ON pivotal_agent_sessions (disposition, started_at DESC)
    """,
)

INSERT_ACTIVITY_EVENT = """
    INSERT INTO pivotal_activity_events (
        event_id, event_type, occurred_at, story_id, story_text,
        from_status, to_status, provider, session_id, payload
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (event_id) DO NOTHING
"""

UPSERT_AGENT_SESSION = """
    INSERT INTO pivotal_agent_sessions (
        provider, session_id, story_id, disposition, started_at, resume_cmd, updated_at
    )
    VALUES (%s, %s, %s, 'active', %s, %s, now())
    ON CONFLICT (provider, session_id) DO UPDATE SET
        story_id = COALESCE(EXCLUDED.story_id, pivotal_agent_sessions.story_id),
        resume_cmd = COALESCE(EXCLUDED.resume_cmd, pivotal_agent_sessions.resume_cmd),
        started_at = LEAST(pivotal_agent_sessions.started_at, EXCLUDED.started_at),
        updated_at = now()
"""

CLOSE_AGENT_SESSION = """
    UPDATE pivotal_agent_sessions
       SET disposition = %s, closed_at = %s, updated_at = now()
     WHERE provider = %s AND session_id = %s
"""


def resolve_dsn(env=None) -> str | None:
    """Return the configured Postgres DSN, or None when the store is off."""
    env = os.environ if env is None else env
    for name in DSN_ENV_VARS:
        value = (env.get(name) or "").strip()
        if value:
            return value
    return None


def normalize_timestamp(value) -> datetime:
    """Coerce a datetime or ISO-8601 string to an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError(f"not a timestamp: {value!r}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def event_id_for(event_type: str, occurred_at, **fields) -> str:
    """Deterministic ID for one logical event, so replays converge on one row."""
    parts = [str(event_type).strip(), normalize_timestamp(occurred_at).isoformat()]
    parts.extend(f"{name}={fields.get(name) or ''}" for name in EVENT_KEY_FIELDS)
    return str(uuid.uuid5(EVENT_NAMESPACE, "|".join(parts)))


def _default_connect(dsn: str):
    import psycopg  # imported lazily so the driver stays an optional dependency

    return psycopg.connect(dsn)


class EventStore:
    """Writes operational events to Postgres when one is configured."""

    def __init__(self, dsn: str | None = None, connect=None):
        self.dsn = (dsn or "").strip() or None
        self._connect = connect or _default_connect
        self.last_error: str | None = None

    @classmethod
    def from_env(cls, env=None, connect=None) -> "EventStore":
        return cls(dsn=resolve_dsn(env), connect=connect)

    @property
    def enabled(self) -> bool:
        return self.dsn is not None

    # -- plumbing ---------------------------------------------------------

    def _run(self, statements) -> bool:
        """Execute (sql, params) pairs in one transaction. Never raises."""
        if not self.enabled:
            return False
        conn = None
        try:
            conn = self._connect(self.dsn)
            with conn:
                with conn.cursor() as cur:
                    for sql, params in statements:
                        cur.execute(sql, params)
            self.last_error = None
            return True
        except Exception as error:  # a broken database must not break the board
            self.last_error = f"{type(error).__name__}: {error}"
            logger.warning("pivotal event store write failed: %s", self.last_error)
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    # -- schema -----------------------------------------------------------

    def migrate(self) -> bool:
        """Apply the (idempotent) schema. Safe to call on every startup."""
        return self._run([(sql, None) for sql in MIGRATIONS])

    # -- writes -----------------------------------------------------------

    def _activity_event_statement(self, *, event_type, occurred_at, payload=None, **fields):
        event_type = str(event_type or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        occurred_at = normalize_timestamp(occurred_at)
        values = {name: (fields.get(name) or None) for name in ACTIVITY_EVENT_FIELDS}
        event_id = event_id_for(event_type, occurred_at, **values)
        params = (
            event_id, event_type, occurred_at,
            *(values[name] for name in ACTIVITY_EVENT_FIELDS),
            json.dumps(payload or {}, sort_keys=True),
        )
        return event_id, (INSERT_ACTIVITY_EVENT, params)

    def record_activity_event(self, *, event_type, occurred_at, payload=None, **fields) -> str | None:
        """Append one event to the ledger; returns its stable ID, or None."""
        if not self.enabled:
            return None
        try:
            event_id, statement = self._activity_event_statement(
                event_type=event_type, occurred_at=occurred_at, payload=payload, **fields
            )
        except ValueError as error:
            self.last_error = str(error)
            return None
        return event_id if self._run([statement]) else None

    def record_session_start(self, *, provider, session_id, story_id, started_at, resume_cmd=None) -> bool:
        """Upsert current session runtime state and log its start event atomically."""
        if not self.enabled:
            return False
        provider = str(provider or "").strip()
        session_id = str(session_id or "").strip()
        if not provider or not session_id:
            self.last_error = "provider and session_id are required"
            return False
        try:
            started_at = normalize_timestamp(started_at)
            _, event = self._activity_event_statement(
                event_type="session_start", occurred_at=started_at, story_id=story_id,
                provider=provider, session_id=session_id,
            )
        except ValueError as error:
            self.last_error = str(error)
            return False
        upsert = (
            UPSERT_AGENT_SESSION,
            (provider, session_id, story_id or None, started_at, resume_cmd or None),
        )
        return self._run([upsert, event])

    def close_session(self, *, provider, session_id, closed_at, disposition="closed", story_id=None) -> bool:
        """Mark a session finished and log its close event atomically."""
        if disposition not in SESSION_DISPOSITIONS:
            raise ValueError(f"unknown disposition: {disposition!r}")
        if not self.enabled:
            return False
        provider = str(provider or "").strip()
        session_id = str(session_id or "").strip()
        if not provider or not session_id:
            self.last_error = "provider and session_id are required"
            return False
        try:
            closed_at = normalize_timestamp(closed_at)
            _, event = self._activity_event_statement(
                event_type="session_close", occurred_at=closed_at, story_id=story_id,
                provider=provider, session_id=session_id, to_status=disposition,
            )
        except ValueError as error:
            self.last_error = str(error)
            return False
        return self._run([(CLOSE_AGENT_SESSION, (disposition, closed_at, provider, session_id)), event])

    def record_transition(self, *, story_id, story_text, from_status, to_status, occurred_at, source=None) -> str | None:
        """Convenience wrapper for the board's own status-change events."""
        return self.record_activity_event(
            event_type="transition", occurred_at=occurred_at, story_id=story_id,
            story_text=story_text, from_status=from_status, to_status=to_status,
            payload={"source": source} if source else None,
        )

    # -- reads (operational checks / integration tests) --------------------

    def fetch_activity_events(self, story_id: str | None = None, limit: int = 100) -> list:
        if not self.enabled:
            return []
        sql = "SELECT event_id, event_type, occurred_at, story_id, to_status FROM pivotal_activity_events"
        params: tuple = ()
        if story_id:
            sql += " WHERE story_id = %s"
            params = (story_id,)
        sql += " ORDER BY occurred_at DESC LIMIT %s"
        params = params + (int(limit),)
        conn = None
        try:
            conn = self._connect(self.dsn)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                    return list(cur.fetchall() or [])
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            logger.warning("pivotal event store read failed: %s", self.last_error)
            return []
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# -- operator CLI ---------------------------------------------------------


def main(argv=None, store: "EventStore | None" = None, out=print) -> int:
    """`python event_store.py migrate|status` — apply or inspect the schema."""
    import argparse

    parser = argparse.ArgumentParser(prog="event_store", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("migrate", "status"))
    args = parser.parse_args(argv)

    store = store or EventStore.from_env()
    if not store.enabled:
        out(f"event store disabled: set one of {', '.join(DSN_ENV_VARS)}")
        return 1 if args.command == "migrate" else 0

    if args.command == "status":
        out(f"event store enabled ({len(MIGRATIONS)} migration statements)")
        return 0

    if store.migrate():
        out(f"migrated: {len(MIGRATIONS)} statements applied")
        return 0
    out(f"migration failed: {store.last_error}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
