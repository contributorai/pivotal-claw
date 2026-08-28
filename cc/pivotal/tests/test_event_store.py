"""Tests for the environment-gated Postgres operational event store.

These run without a live Postgres: the store takes an injectable connection
factory, and the tests assert on the SQL and parameters it emits. An opt-in
integration test runs the same migrations against a real database when
PIVOTAL_POSTGRES_TEST_DSN is set.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PIVOTAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIVOTAL_DIR))

import event_store


class FakeCursor:
    def __init__(self, log, failures):
        self.log = log
        self.failures = failures
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.failures.get("execute"):
            raise RuntimeError("execute exploded")
        self.log.append(("execute", " ".join(sql.split()), params))

    def fetchone(self):
        return None

    def close(self):
        self.log.append(("cursor_close", None, None))


class FakeConnection:
    def __init__(self, log, failures):
        self.log = log
        self.failures = failures
        self.open_transactions = 0

    def cursor(self):
        return FakeCursor(self.log, self.failures)

    def __enter__(self):
        self.open_transactions += 1
        self.log.append(("begin", None, None))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.log.append(("rollback" if exc_type else "commit", None, None))
        return False

    def close(self):
        self.log.append(("close", None, None))


class FakeDriver:
    """Records every statement issued through connections it hands out."""

    def __init__(self, failures=None):
        self.log = []
        self.failures = failures or {}
        self.connections = 0

    def connect(self, dsn):
        self.connections += 1
        if self.failures.get("connect"):
            raise RuntimeError("connection refused")
        self.log.append(("connect", dsn, None))
        return FakeConnection(self.log, self.failures)

    def statements(self):
        return [sql for kind, sql, _ in self.log if kind == "execute"]

    def params(self):
        return [params for kind, _, params in self.log if kind == "execute"]


AT = datetime(2026, 8, 28, 21, 46, 21, tzinfo=timezone.utc)


class DisabledStoreTests(unittest.TestCase):
    """With no DSN configured the store is inert: no driver, no writes, no raises."""

    def test_store_is_disabled_when_no_dsn_is_configured(self):
        store = event_store.EventStore.from_env(env={})
        self.assertFalse(store.enabled)

    def test_disabled_store_never_touches_the_driver(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn=None, connect=driver.connect)

        self.assertFalse(store.migrate())
        self.assertIsNone(
            store.record_activity_event(event_type="transition", occurred_at=AT, story_id="t:abc")
        )
        self.assertFalse(
            store.record_session_start(
                provider="claude", session_id="s1", story_id="t:abc", started_at=AT
            )
        )
        self.assertFalse(store.close_session(provider="claude", session_id="s1", closed_at=AT))
        self.assertEqual(0, driver.connections)
        self.assertEqual([], driver.log)

    def test_blank_dsn_is_treated_as_absent(self):
        store = event_store.EventStore.from_env(env={"PIVOTAL_POSTGRES_DSN": "   "})
        self.assertFalse(store.enabled)

    def test_dsn_is_read_from_the_documented_environment_variables(self):
        primary = event_store.EventStore.from_env(env={"PIVOTAL_POSTGRES_DSN": "postgresql:///one"})
        fallback = event_store.EventStore.from_env(env={"PIVOTAL_DATABASE_URL": "postgresql:///two"})
        self.assertTrue(primary.enabled)
        self.assertEqual("postgresql:///one", primary.dsn)
        self.assertTrue(fallback.enabled)
        self.assertEqual("postgresql:///two", fallback.dsn)


class MigrationTests(unittest.TestCase):
    def _migrated(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertTrue(store.migrate())
        return driver

    def test_migrate_creates_the_activity_event_ledger_and_session_state(self):
        sql = " ".join(self._migrated().statements())
        self.assertIn("CREATE TABLE IF NOT EXISTS pivotal_activity_events", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS pivotal_agent_sessions", sql)

    def test_activity_events_are_keyed_by_a_stable_event_id(self):
        sql = " ".join(self._migrated().statements())
        self.assertIn("event_id UUID PRIMARY KEY", sql)

    def test_migrations_carry_transactional_integrity_constraints(self):
        sql = " ".join(self._migrated().statements())
        self.assertIn("event_type TEXT NOT NULL", sql)
        self.assertIn("occurred_at TIMESTAMPTZ NOT NULL", sql)
        self.assertIn("CHECK", sql)
        self.assertIn("PRIMARY KEY (provider, session_id)", sql)

    def test_every_migration_statement_is_idempotent(self):
        for statement in self._migrated().statements():
            head = statement.upper()
            if head.startswith("CREATE TABLE"):
                self.assertIn("IF NOT EXISTS", head, statement)
            elif head.startswith("CREATE INDEX"):
                self.assertIn("IF NOT EXISTS", head, statement)

    def test_migrations_run_inside_one_transaction_and_close_the_connection(self):
        driver = self._migrated()
        kinds = [kind for kind, _, _ in driver.log]
        self.assertEqual(1, kinds.count("begin"))
        self.assertEqual(1, kinds.count("commit"))
        self.assertEqual("close", kinds[-1])

    def test_migrate_is_safe_to_run_repeatedly(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertTrue(store.migrate())
        first = driver.statements()
        self.assertTrue(store.migrate())
        self.assertEqual(first, driver.statements()[len(first):])


class StableEventIdTests(unittest.TestCase):
    def test_the_same_logical_event_always_gets_the_same_id(self):
        first = event_store.event_id_for("transition", AT, story_id="t:abc", to_status="Done")
        second = event_store.event_id_for("transition", AT, story_id="t:abc", to_status="Done")
        self.assertEqual(first, second)

    def test_different_events_get_different_ids(self):
        base = event_store.event_id_for("transition", AT, story_id="t:abc", to_status="Done")
        other_story = event_store.event_id_for("transition", AT, story_id="t:xyz", to_status="Done")
        other_status = event_store.event_id_for("transition", AT, story_id="t:abc", to_status="Todo")
        other_time = event_store.event_id_for(
            "transition", AT.replace(second=22), story_id="t:abc", to_status="Done"
        )
        self.assertEqual(4, len({base, other_story, other_status, other_time}))

    def test_event_ids_are_uuids(self):
        import uuid

        value = event_store.event_id_for("session_start", AT, provider="claude", session_id="s1")
        self.assertEqual(value, str(uuid.UUID(value)))


class ActivityEventWriteTests(unittest.TestCase):
    def test_recording_an_event_inserts_it_and_returns_the_stable_id(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)

        event_id = store.record_activity_event(
            event_type="transition",
            occurred_at=AT,
            story_id="t:abc",
            story_text="Do the thing ID: t:abc",
            from_status="On schedule",
            to_status="Done",
        )

        self.assertEqual(event_id, event_store.event_id_for(
            "transition", AT, story_id="t:abc", from_status="On schedule", to_status="Done"
        ))
        inserts = [s for s in driver.statements() if "INSERT INTO pivotal_activity_events" in s]
        self.assertEqual(1, len(inserts))
        self.assertIn(event_id, driver.params()[0])

    def test_replaying_an_event_is_a_no_op_at_the_database_level(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        store.record_activity_event(event_type="transition", occurred_at=AT, story_id="t:abc")
        insert = [s for s in driver.statements() if "INSERT INTO pivotal_activity_events" in s][0]
        self.assertIn("ON CONFLICT (event_id) DO NOTHING", insert)

    def test_event_payload_is_serialized_as_json(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        store.record_activity_event(
            event_type="transition", occurred_at=AT, story_id="t:abc", payload={"source": "api/sync"}
        )
        params = driver.params()[0]
        self.assertIn('{"source": "api/sync"}', params)

    def test_naive_timestamps_are_normalized_to_utc(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        store.record_activity_event(
            event_type="transition", occurred_at=datetime(2026, 8, 28, 21, 46, 21), story_id="t:abc"
        )
        stamps = [p for p in driver.params()[0] if isinstance(p, datetime)]
        self.assertTrue(stamps)
        for stamp in stamps:
            self.assertEqual(timezone.utc, stamp.tzinfo)

    def test_iso_timestamp_strings_are_accepted(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        event_id = store.record_activity_event(
            event_type="transition", occurred_at="2026-08-28T21:46:21+00:00", story_id="t:abc"
        )
        self.assertEqual(event_store.event_id_for("transition", AT, story_id="t:abc"), event_id)

    def test_a_blank_event_type_is_rejected_before_reaching_the_database(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertIsNone(store.record_activity_event(event_type="  ", occurred_at=AT))
        self.assertEqual(0, driver.connections)


class SessionStateTests(unittest.TestCase):
    def test_session_start_upserts_runtime_state_and_logs_one_event_atomically(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)

        self.assertTrue(store.record_session_start(
            provider="claude", session_id="s1", story_id="t:abc", started_at=AT,
            resume_cmd="claude --resume s1",
        ))

        statements = driver.statements()
        self.assertTrue(any("INSERT INTO pivotal_agent_sessions" in s for s in statements))
        self.assertTrue(any("INSERT INTO pivotal_activity_events" in s for s in statements))
        kinds = [kind for kind, _, _ in driver.log]
        self.assertEqual(1, kinds.count("begin"))
        self.assertEqual(1, kinds.count("commit"))

    def test_relaunching_the_same_session_updates_rather_than_duplicates(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        store.record_session_start(provider="claude", session_id="s1", story_id="t:abc", started_at=AT)
        upsert = [s for s in driver.statements() if "INSERT INTO pivotal_agent_sessions" in s][0]
        self.assertIn("ON CONFLICT (provider, session_id) DO UPDATE", upsert)

    def test_closing_a_session_records_the_disposition_and_close_time(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertTrue(store.close_session(
            provider="claude", session_id="s1", closed_at=AT, disposition="closed"
        ))
        update = [s for s in driver.statements() if "UPDATE pivotal_agent_sessions" in s][0]
        self.assertIn("disposition", update)
        self.assertIn("closed_at", update)

    def test_an_unknown_disposition_is_rejected(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        with self.assertRaises(ValueError):
            store.close_session(provider="claude", session_id="s1", closed_at=AT, disposition="banana")

    def test_a_session_without_a_provider_or_id_is_ignored(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertFalse(store.record_session_start(provider="", session_id="s1", story_id="t:abc", started_at=AT))
        self.assertFalse(store.record_session_start(provider="claude", session_id=None, story_id="t:abc", started_at=AT))
        self.assertEqual(0, driver.connections)


class FailureIsolationTests(unittest.TestCase):
    """A broken database must degrade the pulse, never the board."""

    def test_a_connection_failure_does_not_propagate(self):
        driver = FakeDriver(failures={"connect": True})
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertFalse(store.migrate())
        self.assertIsNone(store.record_activity_event(event_type="transition", occurred_at=AT))
        self.assertFalse(store.record_session_start(
            provider="claude", session_id="s1", story_id="t:abc", started_at=AT
        ))

    def test_a_statement_failure_rolls_back_and_does_not_propagate(self):
        driver = FakeDriver(failures={"execute": True})
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        self.assertIsNone(store.record_activity_event(event_type="transition", occurred_at=AT))
        kinds = [kind for kind, _, _ in driver.log]
        self.assertIn("rollback", kinds)
        self.assertEqual("close", kinds[-1])

    def test_failures_are_recorded_for_operators(self):
        driver = FakeDriver(failures={"connect": True})
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        store.record_activity_event(event_type="transition", occurred_at=AT)
        self.assertIsNotNone(store.last_error)


class CliTests(unittest.TestCase):
    """`python event_store.py migrate` is how an operator applies the schema."""

    def test_migrate_reports_success(self):
        driver = FakeDriver()
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        lines = []
        self.assertEqual(0, event_store.main(["migrate"], store=store, out=lines.append))
        self.assertTrue(any("CREATE TABLE IF NOT EXISTS" in s for s in driver.statements()))
        self.assertTrue(any("migrated" in line for line in lines))

    def test_migrate_fails_loudly_when_the_store_is_not_configured(self):
        lines = []
        store = event_store.EventStore(dsn=None)
        self.assertEqual(1, event_store.main(["migrate"], store=store, out=lines.append))
        self.assertTrue(any("PIVOTAL_POSTGRES_DSN" in line for line in lines))

    def test_migrate_fails_loudly_when_the_database_is_unreachable(self):
        driver = FakeDriver(failures={"connect": True})
        store = event_store.EventStore(dsn="postgresql:///pulse", connect=driver.connect)
        lines = []
        self.assertEqual(1, event_store.main(["migrate"], store=store, out=lines.append))
        self.assertTrue(any("connection refused" in line for line in lines))

    def test_status_reports_whether_the_store_is_enabled(self):
        lines = []
        event_store.main(["status"], store=event_store.EventStore(dsn=None), out=lines.append)
        self.assertTrue(any("disabled" in line for line in lines))


@unittest.skipUnless(
    os.environ.get("PIVOTAL_POSTGRES_TEST_DSN"),
    "set PIVOTAL_POSTGRES_TEST_DSN to run the live Postgres integration test",
)
class LivePostgresTests(unittest.TestCase):
    def test_migrations_and_writes_apply_against_a_real_database(self):
        store = event_store.EventStore(dsn=os.environ["PIVOTAL_POSTGRES_TEST_DSN"])
        self.assertTrue(store.enabled)
        self.assertTrue(store.migrate())
        self.assertTrue(store.migrate())  # idempotent

        event_id = store.record_activity_event(
            event_type="transition", occurred_at=AT, story_id="t:live01", to_status="Done"
        )
        self.assertIsNotNone(event_id)
        # replaying the same event must not duplicate the row
        self.assertEqual(event_id, store.record_activity_event(
            event_type="transition", occurred_at=AT, story_id="t:live01", to_status="Done"
        ))

        rows = store.fetch_activity_events(story_id="t:live01")
        self.assertEqual(1, len(rows))

        self.assertTrue(store.record_session_start(
            provider="claude", session_id="live-s1", story_id="t:live01", started_at=AT
        ))
        self.assertTrue(store.close_session(provider="claude", session_id="live-s1", closed_at=AT))


if __name__ == "__main__":
    unittest.main()
