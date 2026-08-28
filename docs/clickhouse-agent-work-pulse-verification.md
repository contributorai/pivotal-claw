# ClickHouse Agent Work Pulse verification

Date: 2026-08-28  
Stories: `t:wkns9xx`, `t:trfy2sw`, `t:ted6kh8`  
Epic: `#clickhouse_postgres_agent_work_pulse`

## Architecture and safety

- The Markdown and JSON board remains authoritative.
- Postgres is a best-effort operational mirror.
- A TLS-verified ClickPipe replicates the two pulse tables into ClickHouse.
- The application queries ClickHouse with a dedicated user granted `SELECT`
  only on those two tables.
- Credentials are supplied through environment variables or a mode-0600
  password file and are not stored in Git.
- Missing configuration and query failures return an unavailable pulse; they do
  not block board reads or writes.

## Managed CDC acceptance

The managed integration was exercised with clearly labeled synthetic rows.

| Check | Result |
| --- | --- |
| ClickPipe state | Running |
| Initial snapshots | Completed for both destination tables |
| Insert replication | Active sessions `1`; completed stories (24h) `1`; median cycle `90` seconds |
| Update replication | A changed Postgres session value became current in ClickHouse after approximately 52 seconds |
| Current-row semantics | Queries use `FINAL` for ClickPipe `ReplacingMergeTree` destinations |

The observed update delay is normal asynchronous CDC behavior and is useful to
show in the demo: metrics are live, but not transactionally synchronous with a
board write.

## Application acceptance

- `GET /api/agent-work-pulse` exposes only aggregate metric values and status,
  never credentials or upstream error text.
- `/sessions_history` renders active sessions, completed stories in the last 24
  hours, and median Doing-to-Done time.
- A live isolated browser smoke against the managed ClickHouse replica rendered
  `1`, `1`, and `2m` with the `Live from ClickHouse` state.
- The browser console had no JavaScript errors or warnings. The pre-existing
  missing `/favicon.ico` request observed during this smoke was subsequently
  hardened to return an empty 204 response.
- At a 390 x 844 viewport, the pulse cards collapsed to one column and remained
  within the viewport. Existing session-table rows can still require horizontal
  scrolling on narrow screens; that behavior predates and is outside the pulse
  card change.

## Automated checks

The final slice passed the focused metrics and endpoint tests, JavaScript syntax
check, and whitespace check. The full Flask suite passed with `336 passed`,
`1 skipped`, and `255 subtests passed`.
