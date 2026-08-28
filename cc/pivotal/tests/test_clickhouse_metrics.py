import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path


PIVOTAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIVOTAL_DIR))

import clickhouse_metrics


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RecordingRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {}
        self.error = error
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.payload)


class ClickHouseMetricsTests(unittest.TestCase):
    def test_is_disabled_without_complete_environment_settings(self):
        metrics = clickhouse_metrics.ClickHouseMetrics.from_env(env={})

        self.assertFalse(metrics.enabled)
        self.assertEqual("disabled", metrics.snapshot()["status"])

    def test_environment_configuration_uses_the_public_clickpipe_tables(self):
        metrics = clickhouse_metrics.ClickHouseMetrics.from_env(env={
            "PIVOTAL_CLICKHOUSE_URL": "https://example.clickhouse.cloud:8443",
            "PIVOTAL_CLICKHOUSE_USER": "pulse_reader",
            "PIVOTAL_CLICKHOUSE_PASSWORD": "secret",
        })

        self.assertTrue(metrics.enabled)
        self.assertEqual("default.public_pivotal_activity_events", metrics.activity_table)
        self.assertEqual("default.public_pivotal_agent_sessions", metrics.sessions_table)

    def test_environment_can_read_the_password_from_a_secret_file(self):
        with tempfile.TemporaryDirectory() as td:
            password_file = Path(td) / "clickhouse-password"
            password_file.write_text("file-secret\n", encoding="utf-8")

            metrics = clickhouse_metrics.ClickHouseMetrics.from_env(env={
                "PIVOTAL_CLICKHOUSE_URL": "https://example.clickhouse.cloud:8443",
                "PIVOTAL_CLICKHOUSE_USER": "pulse_reader",
                "PIVOTAL_CLICKHOUSE_PASSWORD_FILE": str(password_file),
            })

        self.assertTrue(metrics.enabled)
        self.assertEqual("file-secret", metrics.password)

    def test_snapshot_queries_clickhouse_over_tls_with_basic_auth(self):
        request = RecordingRequest({
            "active_sessions": 3,
            "stories_completed_24h": 7,
            "median_cycle_seconds": 91.5,
        })
        metrics = clickhouse_metrics.ClickHouseMetrics(
            url="https://example.clickhouse.cloud:8443",
            username="pulse_reader",
            password="secret",
            request=request,
        )

        snapshot = metrics.snapshot()

        self.assertEqual({
            "status": "available",
            "active_sessions": 3,
            "stories_completed_24h": 7,
            "median_cycle_seconds": 91.5,
        }, snapshot)
        sent, timeout = request.calls[0]
        self.assertEqual("https://example.clickhouse.cloud:8443/", sent.full_url)
        self.assertEqual("POST", sent.method)
        self.assertEqual(5, timeout)
        self.assertEqual(
            "Basic " + base64.b64encode(b"pulse_reader:secret").decode("ascii"),
            sent.headers["Authorization"],
        )
        sql = sent.data.decode("utf-8")
        self.assertIn("default.public_pivotal_activity_events FINAL", sql)
        self.assertIn("default.public_pivotal_agent_sessions FINAL", sql)
        self.assertIn("stories_completed_24h", sql)
        self.assertIn("median_cycle_seconds", sql)
        self.assertTrue(sql.rstrip().endswith("FORMAT JSONEachRow"))

    def test_connectivity_failure_is_reported_without_leaking_the_error(self):
        metrics = clickhouse_metrics.ClickHouseMetrics(
            url="https://example.clickhouse.cloud:8443",
            username="pulse_reader",
            password="secret",
            request=RecordingRequest(error=RuntimeError("secret token appeared here")),
        )

        snapshot = metrics.snapshot()

        self.assertEqual("unavailable", snapshot["status"])
        self.assertEqual("ClickHouse metrics are temporarily unavailable", snapshot["message"])
        self.assertNotIn("secret", json.dumps(snapshot))
        self.assertIn("RuntimeError", metrics.last_error)


if __name__ == "__main__":
    unittest.main()
