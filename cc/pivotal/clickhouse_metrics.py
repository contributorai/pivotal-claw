"""Read Agent Work Pulse analytics from ClickHouse without coupling the board to it."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from urllib import request as urllib_request


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str, fallback: str) -> str:
    value = str(value or fallback).strip()
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid ClickHouse identifier: {value!r}")
    return value


class ClickHouseMetrics:
    """Environment-gated, read-only ClickHouse HTTP client for the pulse API."""

    def __init__(
        self,
        *,
        url=None,
        username=None,
        password=None,
        database="default",
        activity_table="public_pivotal_activity_events",
        sessions_table="public_pivotal_agent_sessions",
        request=None,
    ):
        self.url = str(url or "").strip().rstrip("/") or None
        self.username = str(username or "").strip() or None
        self.password = str(password or "") or None
        database = _identifier(database, "default")
        activity_table = _identifier(activity_table, "public_pivotal_activity_events")
        sessions_table = _identifier(sessions_table, "public_pivotal_agent_sessions")
        self.activity_table = f"{database}.{activity_table}"
        self.sessions_table = f"{database}.{sessions_table}"
        self._request = request or urllib_request.urlopen
        self.last_error = None

    @classmethod
    def from_env(cls, env=None, request=None):
        env = os.environ if env is None else env
        password = env.get("PIVOTAL_CLICKHOUSE_PASSWORD")
        password_file = str(env.get("PIVOTAL_CLICKHOUSE_PASSWORD_FILE") or "").strip()
        if not password and password_file:
            try:
                password = Path(password_file).read_text(encoding="utf-8").strip()
            except OSError:
                password = None
        return cls(
            url=env.get("PIVOTAL_CLICKHOUSE_URL"),
            username=env.get("PIVOTAL_CLICKHOUSE_USER"),
            password=password,
            database=env.get("PIVOTAL_CLICKHOUSE_DATABASE") or "default",
            activity_table=env.get("PIVOTAL_CLICKHOUSE_ACTIVITY_TABLE")
            or "public_pivotal_activity_events",
            sessions_table=env.get("PIVOTAL_CLICKHOUSE_SESSIONS_TABLE")
            or "public_pivotal_agent_sessions",
            request=request,
        )

    @property
    def enabled(self):
        return all((self.url, self.username, self.password))

    def _query(self):
        return f"""
SELECT
    (
        SELECT count()
        FROM {self.sessions_table} FINAL
        WHERE disposition = 'active'
    ) AS active_sessions,
    (
        SELECT countDistinct(story_id)
        FROM {self.activity_table} FINAL
        WHERE event_type = 'transition'
          AND lowerUTF8(ifNull(to_status, '')) = 'done'
          AND occurred_at >= now() - INTERVAL 24 HOUR
    ) AS stories_completed_24h,
    (
        SELECT medianOrNull(toFloat64(dateDiff('second', doing_at, done_at)))
        FROM
        (
            SELECT
                story_id,
                maxIf(occurred_at, lowerUTF8(ifNull(to_status, '')) = 'in progress') AS doing_at,
                maxIf(occurred_at, lowerUTF8(ifNull(to_status, '')) = 'done') AS done_at
            FROM {self.activity_table} FINAL
            WHERE event_type = 'transition' AND story_id IS NOT NULL
            GROUP BY story_id
            HAVING doing_at > toDateTime64(0, 6, 'UTC') AND done_at >= doing_at
        )
    ) AS median_cycle_seconds
FORMAT JSONEachRow
""".strip()

    def snapshot(self):
        if not self.enabled:
            return {
                "status": "disabled",
                "message": "ClickHouse metrics are not configured",
                "active_sessions": None,
                "stories_completed_24h": None,
                "median_cycle_seconds": None,
            }
        try:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            request = urllib_request.Request(
                self.url + "/",
                data=self._query().encode("utf-8"),
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                method="POST",
            )
            with self._request(request, timeout=5) as response:
                body = response.read().decode("utf-8").strip()
            row = json.loads(body) if body else {}
            self.last_error = None
            return {
                "status": "available",
                "active_sessions": int(row.get("active_sessions") or 0),
                "stories_completed_24h": int(row.get("stories_completed_24h") or 0),
                "median_cycle_seconds": (
                    float(row["median_cycle_seconds"])
                    if row.get("median_cycle_seconds") is not None
                    else None
                ),
            }
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            return {
                "status": "unavailable",
                "message": "ClickHouse metrics are temporarily unavailable",
                "active_sessions": None,
                "stories_completed_24h": None,
                "median_cycle_seconds": None,
            }
