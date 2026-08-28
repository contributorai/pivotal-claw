import tempfile
import unittest
from pathlib import Path

import sys


PIVOTAL_DIR = Path(__file__).resolve().parents[1]
TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
sys.path.insert(0, str(PIVOTAL_DIR))
sys.path.insert(0, str(TODO_MODULE_DIR))

import server


class RecordingMetrics:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return dict(self.payload)


def make_board(root):
    todo_dir = Path(root) / "todo"
    tree = todo_dir / "tree_viewer"
    tree.mkdir(parents=True)
    (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
    for name in ("todo.md", "doing.md", "done.md", "icebox.md"):
        (todo_dir / name).write_text("# Lane\n", encoding="utf-8")
    (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")
    return todo_dir


class AgentWorkPulseApiTests(unittest.TestCase):
    def test_endpoint_returns_the_clickhouse_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            metrics = RecordingMetrics({
                "status": "available",
                "active_sessions": 2,
                "stories_completed_24h": 5,
                "median_cycle_seconds": 120.0,
            })
            app = server.create_app(
                todo_dir=make_board(td), clickhouse_metrics_instance=metrics
            )

            response = app.test_client().get("/api/agent-work-pulse")

            self.assertEqual(200, response.status_code)
            self.assertEqual(2, response.get_json()["active_sessions"])
            self.assertEqual(1, metrics.calls)

    def test_default_endpoint_is_disabled_without_clickhouse_environment(self):
        with tempfile.TemporaryDirectory() as td:
            app = server.create_app(todo_dir=make_board(td))

            response = app.test_client().get("/api/agent-work-pulse")

            self.assertEqual(200, response.status_code)
            self.assertEqual("disabled", response.get_json()["status"])


if __name__ == "__main__":
    unittest.main()
