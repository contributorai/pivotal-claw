from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

from flask import Flask, Response, jsonify, request, send_file

try:
    from cc.pivotal import clickhouse_metrics as clickhouse_metrics_module
    from cc.pivotal import event_store as event_store_module
    from cc.pivotal import session_scanner
    from cc.pivotal import terminal_focus
except ModuleNotFoundError:
    import clickhouse_metrics as clickhouse_metrics_module
    import event_store as event_store_module
    import session_scanner
    import terminal_focus


DEFAULT_TODO_DIR = Path(os.environ.get("PIVOTAL_TODO_DIR") or Path(__file__).resolve().parent.parent / "todo")
MAIN_LOOP_EXECUTIVE_OVERVIEW = Path(
    os.environ.get("MAIN_LOOP_EXECUTIVE_OVERVIEW")
    or Path.home() / "Documents" / "main_loop_for_all_subsystems" / "project-executive-overview.html"
)
EXPORT_FILENAMES = (
    "todo.md", "doing.md", "done.md", "icebox.md", "epics.md",
    "portfolio.json", "story_sessions.json", "history_events.json", "pins.json",
)
EVENT_POLL_INTERVAL_SECONDS = 1.0
sync_lock = threading.Lock()
story_sessions_lock = threading.Lock()
pins_lock = threading.Lock()
history_events_lock = threading.Lock()


def read_json_file(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2, ensure_ascii=False)
            tmp.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def normalize_story_sessions(data) -> dict:
    if not isinstance(data, dict):
        return {}
    normalized = {}
    for story_id, entry in data.items():
        if story_id in {"pin", "pinned_epics"}:
            normalized[story_id] = entry
        elif isinstance(entry, list):
            normalized[story_id] = {"sessions": entry, "notes": []}
        elif isinstance(entry, dict):
            normalized_entry = dict(entry)
            if not isinstance(normalized_entry.get("sessions"), list):
                normalized_entry["sessions"] = []
            if not isinstance(normalized_entry.get("notes"), list):
                normalized_entry["notes"] = []
            normalized[story_id] = normalized_entry
        else:
            normalized[story_id] = {"sessions": [], "notes": []}
    return normalized


def read_story_sessions(path: Path) -> dict:
    return normalize_story_sessions(read_json_file(path, {}))


def write_story_sessions(path: Path, data: dict) -> None:
    write_json_atomic(path, normalize_story_sessions(data))


def update_story_sessions(path: Path, modifier_fn) -> dict:
    with story_sessions_lock:
        data = read_story_sessions(path)
        resolved = modifier_fn(data)
        if resolved is None:
            resolved = data
        resolved = normalize_story_sessions(resolved)
        write_story_sessions(path, resolved)
        return resolved


def annotate_running_sessions(story_sessions: dict) -> dict:
    try:
        running_keys = set(terminal_focus.list_running_sessions().keys())
    except Exception:
        running_keys = set()
    for story_id, entry in story_sessions.items():
        if story_id in {"pin", "pinned_epics"} or not isinstance(entry, dict):
            continue
        for session in entry.get("sessions", []):
            if not isinstance(session, dict):
                continue
            provider = session.get("provider")
            session_id = session.get("session_id")
            if provider in PIN_PROVIDERS and isinstance(session_id, str) and session_id:
                session["running"] = (provider, session_id) in running_keys
    return story_sessions


PIN_PROVIDERS = {"claude", "codex"}


def normalize_pins(data) -> dict:
    """Coerce anything on disk into {"sessions": [record], "stories": [id]}.

    A pinned session is stored whole so it survives falling out of the recent
    scan window; only records with a usable provider and id are kept, and both
    lists are de-duped in first-pinned order.
    """
    if not isinstance(data, dict):
        data = {}
    sessions = []
    seen_sessions = set()
    for record in data.get("sessions", []) if isinstance(data.get("sessions"), list) else []:
        if not isinstance(record, dict):
            continue
        provider = str(record.get("provider", ""))
        session_id = str(record.get("session_id", ""))
        if provider not in PIN_PROVIDERS or not session_id:
            continue
        key = (provider, session_id)
        if key in seen_sessions:
            continue
        seen_sessions.add(key)
        normalized = dict(record)
        normalized["provider"] = provider
        normalized["session_id"] = session_id
        normalized.pop("running", None)
        normalized.pop("project", None)
        sessions.append(normalized)
    stories = []
    seen_stories = set()
    for story_id in data.get("stories", []) if isinstance(data.get("stories"), list) else []:
        story_id = str(story_id).strip()
        if not story_id or story_id in seen_stories or story_id in {"pin", "pinned_epics"}:
            continue
        seen_stories.add(story_id)
        stories.append(story_id)
    return {"sessions": sessions, "stories": stories}


def read_pins(path: Path) -> dict:
    return normalize_pins(read_json_file(path, {}))


def write_pins(path: Path, data: dict) -> None:
    write_json_atomic(path, normalize_pins(data))


def update_pins(path: Path, modifier_fn) -> dict:
    with pins_lock:
        data = read_pins(path)
        resolved = modifier_fn(data)
        if resolved is None:
            resolved = data
        resolved = normalize_pins(resolved)
        write_pins(path, resolved)
        return resolved


def annotate_pins(pins: dict) -> dict:
    """Add the fields derived at read time rather than stored: running, project."""
    try:
        running_keys = set(terminal_focus.list_running_sessions().keys())
    except Exception:
        running_keys = set()
    for record in pins.get("sessions", []):
        record["running"] = (record.get("provider"), record.get("session_id")) in running_keys
        cwd = record.get("cwd") or ""
        record["project"] = Path(cwd).name if cwd else ""
    return pins


def read_history_events(path: Path) -> list:
    events = read_json_file(path, [])
    return events if isinstance(events, list) else []


def update_history_events(path: Path, modifier_fn) -> list:
    with history_events_lock:
        events = read_history_events(path)
        resolved = modifier_fn(events)
        if resolved is None:
            resolved = events
        if not isinstance(resolved, list):
            resolved = []
        write_json_atomic(path, resolved)
        return resolved


def clean_subprocess_env() -> dict:
    env = os.environ.copy()
    blocked = {"ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY"}
    for key in list(env):
        if key.upper() in blocked or key.startswith("APIClogCode") or key.startswith("ClogCode"):
            env.pop(key, None)
    return env


def create_app(todo_dir: Path = DEFAULT_TODO_DIR, codex_runtime=None, claude_runtime=None, server_label: str | None = None, event_store_instance=None, clickhouse_metrics_instance=None) -> Flask:
    todo_dir = Path(todo_dir)
    history_path = todo_dir / "history_events.json"
    story_sessions_path = todo_dir / "story_sessions.json"
    pins_path = todo_dir / "pins.json"
    local_todo = Path(__file__).resolve().parents[1] / "todo"
    cloud_mode = os.environ.get("CLOUD_DEPLOYMENT_NOT_ON_LAPTOP") == "1"

    tree_viewer_dir = todo_dir / "tree_viewer"
    if not (tree_viewer_dir / "pivotal_tasks.html").exists():
        tree_viewer_dir = local_todo / "tree_viewer"

    for import_dir in [todo_dir, local_todo]:
        import_path = str(import_dir)
        if import_path in sys.path:
            sys.path.remove(import_path)
        sys.path.insert(0, import_path)
    try:
        import generate_todo_viewer as gtv
        import sync_changes
        import portfolio_store
    except ModuleNotFoundError:
        sys.path.insert(0, str(local_todo))
        import generate_todo_viewer as gtv
        import sync_changes
        import portfolio_store

    try:
        from cc.pivotal.codex_sessions import CodexRuntime
        from cc.pivotal.claude_sessions import ClaudeRuntime
    except ModuleNotFoundError:
        from codex_sessions import CodexRuntime
        from claude_sessions import ClaudeRuntime
    if cloud_mode:
        # Runtimes resolve local CLI executables at construction; none exist in
        # a cloud deployment and every route that uses them is gated anyway.
        RUNTIMES = {}
    else:
        codex_runtime = codex_runtime or CodexRuntime()
        claude_runtime = claude_runtime or ClaudeRuntime()
        RUNTIMES = {"codex": codex_runtime, "claude": claude_runtime}
    RESUME_CMD = {
        "codex": lambda session_id: f"codex resume {session_id}",
        "claude": lambda session_id: f"claude --resume {session_id}",
    }
    session_launches = {}
    session_launches_lock = threading.Lock()

    app = Flask(__name__)
    data_state = {"version": 0}

    # Secondary OLTP ledger for Agent Work Pulse. Inert unless a Postgres DSN is
    # configured; every call through mirror_to_event_store is best-effort so a
    # database problem can never fail a board write.
    events = event_store_instance or event_store_module.EventStore.from_env()
    app.config["EVENT_STORE"] = events
    pulse_metrics = clickhouse_metrics_instance or clickhouse_metrics_module.ClickHouseMetrics.from_env()
    app.config["CLICKHOUSE_METRICS"] = pulse_metrics
    if events.enabled:
        try:
            events.migrate()
        except Exception as error:
            app.logger.warning("event store migration failed: %s", error)

    def mirror_to_event_store(action, description):
        if not events.enabled:
            return
        try:
            action()
        except Exception as error:
            app.logger.warning("event store mirror failed (%s): %s", description, error)

    def build_todo_data() -> dict:
        data = gtv.build_todo_data(todo_dir)
        portfolio_data = read_json_file(todo_dir / "portfolio.json", {})
        story_sessions = annotate_running_sessions(read_story_sessions(story_sessions_path))
        data["portfolio_data"] = portfolio_data or {}
        data["story_sessions"] = story_sessions or {}
        if server_label:
            data["server_label"] = server_label
        return data

    def flatten_task_dicts(tasks):
        result = []
        for task in tasks:
            result.append(task)
            result.extend(flatten_task_dicts(task.get("children", [])))
        return result

    def resolve_story(story_id: str):
        data = build_todo_data()
        story = next((item for item in flatten_task_dicts(data["tasks"]) if item.get("task_id") == story_id), None)
        if story is None:
            return None, None
        project_dir = None
        for tag in story.get("tags", []):
            value = data.get("epic_meta", {}).get(tag, {}).get("dir")
            if value:
                project_dir = Path(value)
                break
        if project_dir is None:
            project_dir = Path(story["source_file"]).parent
        return story, project_dir

    def register_session_launch(token, story, provider, session_id, resume_cmd):
        now = datetime.now(timezone.utc)
        session = {
            "session_id": session_id, "provider": provider, "started_at": now.isoformat(),
            "resume_cmd": resume_cmd, "disposition": "active", "closed_at": None,
            "artifact_links": {"commit_sha": [], "stories_done": [], "retro_paths": [], "docs_written": []},
        }
        note = {"type": "session_start", "text": f"{provider.capitalize()} session started", "ts": int(now.timestamp() * 1000),
                "at": now.date().isoformat(), "session_id": session_id, "provider": provider}
        def add_session(data):
            entry = data.setdefault(story["task_id"], {"sessions": [], "notes": []})
            if session_id not in {item.get("session_id") for item in entry["sessions"]}:
                entry["sessions"].insert(0, session)
                entry["notes"].insert(0, note)
            return data
        update_story_sessions(story_sessions_path, add_session)
        mirror_to_event_store(
            lambda: events.record_session_start(
                provider=provider, session_id=session_id, story_id=story["task_id"],
                started_at=now, resume_cmd=resume_cmd,
            ),
            "session_start",
        )
        if story.get("status") in {"Pending", "On schedule"}:
            with sync_lock:
                sync_changes.apply_sync({"version": 1, "statusChanges": [{
                    "task_id": story["task_id"], "source_file": story["source_file"],
                    "line_number": story["line_number"], "expected_text": story["text"],
                    "new_status": "In progress",
                }]}, todo_dir=todo_dir)
        with session_launches_lock:
            session_launches[token] = {"status": "registered", "session_id": session_id}

    def finish_launch(token, story, context, provider):
        try:
            metadata = RUNTIMES[provider].wait(context)
            session_id = getattr(metadata, "thread_id", None) or getattr(metadata, "session_id", None)
            register_session_launch(token, story, provider, session_id, RESUME_CMD[provider](session_id))
        except Exception as error:
            with session_launches_lock:
                session_launches[token] = {"status": "failed", "error": str(error)}

    @app.route("/")
    def index():
        return send_file(tree_viewer_dir / "pivotal_tasks.html")

    @app.route("/portfolio")
    def portfolio_page():
        return send_file(tree_viewer_dir / "portfolio.html")

    @app.route("/welcome")
    def welcome_page():
        return send_file(tree_viewer_dir / "welcome.html")

    @app.route("/welcome-a")
    def welcome_a_page():
        # Preserved onboarding prototype (direction A), kept for later
        # cherry-picking. Not linked from the shipped /welcome.
        return send_file(tree_viewer_dir / "welcome-a.html")

    @app.route("/sessions_history")
    def sessions_history_page():
        return send_file(tree_viewer_dir / "sessions_history.html")

    @app.route("/main-loop-executive-overview")
    def main_loop_executive_overview():
        if cloud_mode:
            return jsonify({"error": "not available in cloud deployment"}), 404
        return send_file(MAIN_LOOP_EXECUTIVE_OVERVIEW)

    @app.route("/todo_data.js")
    def todo_data_js():
        payload = json.dumps(build_todo_data(), ensure_ascii=False)
        return Response(f"window.todoData = {payload};\n", mimetype="application/javascript")

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        payload = request.get_json(silent=True) or {}
        with sync_lock:
            receipt = sync_changes.apply_sync(payload, todo_dir=todo_dir)
            if any(
                receipt.get(key)
                for key in ("appliedStatus", "appliedTexts", "appliedTags", "appliedTagRemovals", "appliedNewItems")
            ):
                data_state["version"] += 1
            now = datetime.now(timezone.utc).isoformat()
            applied = set(receipt.get("appliedStatus", []))

            def add_transitions(events):
                for change in payload.get("statusChanges", []):
                    if change.get("task_id") not in applied:
                        continue
                    events.insert(
                        0,
                        {
                            "type": "transition",
                            "story_id": change.get("task_id"),
                            "story_text": change.get("expected_text", ""),
                            "from_status": change.get("from_status", ""),
                            "to_status": change.get("new_status", ""),
                            "ts": now,
                            "at": now[:10],
                        },
                    )
                return events[:1000]

            update_history_events(history_path, add_transitions)

            for change in payload.get("statusChanges", []):
                if change.get("task_id") not in applied:
                    continue
                mirror_to_event_store(
                    lambda change=change: events.record_transition(
                        story_id=change.get("task_id"),
                        story_text=change.get("expected_text", ""),
                        from_status=change.get("from_status", ""),
                        to_status=change.get("new_status", ""),
                        occurred_at=now,
                        source="api/sync",
                    ),
                    "transition",
                )
        return jsonify(receipt)

    @app.route("/api/epics", methods=["POST"])
    def api_create_epic():
        payload = request.get_json(silent=True) or {}
        tag = str(payload.get("tag") or "").strip()
        dir_value = str(payload.get("dir") or "").strip() or None
        color = str(payload.get("color") or "").strip() or None
        portfolio_id = str(payload.get("portfolio_id") or "").strip() or None
        with sync_lock:
            ok, reason = sync_changes.create_epic(tag, dir=dir_value, color=color, todo_dir=todo_dir)
            if not ok:
                return jsonify({"error": reason or "could not create epic"}), 400
            clean_tag = tag.lstrip("#").strip()
            if portfolio_id:
                try:
                    items = portfolio_store.read_portfolios(todo_dir)
                    items = portfolio_store.assign_epics(items, [clean_tag], to=portfolio_id)
                    portfolio_store.write_portfolios(items, todo_dir)
                except ValueError:
                    pass
            data_state["version"] += 1
        return jsonify({"status": "ok", "tag": clean_tag})

    @app.route("/api/portfolio/reorder", methods=["POST"])
    def api_reorder_portfolios():
        """Persist the stack rank: portfolio order, epic order within a portfolio, or both."""
        payload = request.get_json(silent=True) or {}
        order = payload.get("order")
        portfolio_id = str(payload.get("portfolio_id") or "").strip() or None
        epic_tags = payload.get("epic_tags")

        wants_portfolios = isinstance(order, list)
        wants_epics = portfolio_id is not None and isinstance(epic_tags, list)
        if not wants_portfolios and not wants_epics:
            return jsonify({"error": "nothing to reorder"}), 400

        with sync_lock:
            try:
                items = portfolio_store.read_portfolios(todo_dir)
                if wants_portfolios:
                    items = portfolio_store.reorder_portfolios(items, order)
                if wants_epics:
                    items = portfolio_store.reorder_epics(items, portfolio_id, epic_tags)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            portfolio_store.write_portfolios(items, todo_dir)
            data_state["version"] += 1
        return jsonify({"status": "ok"})

    @app.route("/api/export")
    def api_export():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in EXPORT_FILENAMES:
                path = todo_dir / name
                if path.exists():
                    zf.write(path, arcname=name)
        buffer.seek(0)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return send_file(
            buffer, mimetype="application/zip", as_attachment=True,
            download_name=f"pivotal-backup-{stamp}.zip",
        )

    @app.route("/api/import", methods=["POST"])
    def api_import():
        uploaded = request.files.get("file")
        if uploaded is None:
            return jsonify({"error": "missing file"}), 400
        try:
            with zipfile.ZipFile(uploaded) as zf:
                names = zf.namelist()
                unexpected = [name for name in names if name not in EXPORT_FILENAMES]
                if unexpected:
                    return jsonify({"error": "unexpected files in archive", "files": unexpected}), 400
                for name in names:
                    with sync_lock:
                        (todo_dir / name).write_bytes(zf.read(name))
        except zipfile.BadZipFile:
            return jsonify({"error": "invalid zip file"}), 400
        data_state["version"] += 1
        return jsonify({"status": "ok", "imported": names})

    @app.route("/api/history")
    def api_history():
        days = min(max(int(request.args.get("days", 30)), 1), 365)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        events = read_history_events(history_path)
        filtered = []
        for event in events:
            try:
                ts = datetime.fromisoformat(str(event.get("ts", "")).replace("Z", "+00:00"))
            except ValueError:
                filtered.append(event)
                continue
            if ts >= cutoff:
                filtered.append(event)
        return jsonify(filtered)

    @app.route("/api/events")
    def api_events():
        def generate():
            last_sent = None
            while True:
                current = data_state["version"]
                if current != last_sent:
                    yield f"data: {current}\n\n"
                    last_sent = current
                time.sleep(EVENT_POLL_INTERVAL_SECONDS)

        return Response(generate(), mimetype="text/event-stream")

    @app.route("/api/story-sessions/<story_id>")
    def api_story_sessions(story_id: str):
        data = read_story_sessions(story_sessions_path)
        entry = data.get(story_id, {"sessions": [], "notes": []})
        annotate_running_sessions({story_id: entry})
        return jsonify(entry)

    @app.route("/api/story-note", methods=["POST"])
    def api_story_note():
        payload = request.get_json(silent=True) or {}
        story_id = str(payload.get("story_id", "")).strip()
        text = str(payload.get("text", "")).strip()
        if not story_id or not text:
            return jsonify({"error": "story_id and text are required"}), 400

        now = datetime.now(timezone.utc)
        note = {
            "type": "note",
            "text": text,
            "ts": int(now.timestamp() * 1000),
            "at": now.date().isoformat(),
        }

        def append_note(data):
            entry = data.setdefault(story_id, {"sessions": [], "notes": []})
            entry["notes"].insert(0, note)
            return data

        update_story_sessions(story_sessions_path, append_note)
        return jsonify({"ok": True})

    @app.route("/api/open-file", methods=["POST"])
    def api_open_file():
        payload = request.get_json(silent=True) or {}
        path = str(payload.get("path", "")).strip()
        if not path:
            return jsonify({"error": "path is required"}), 400
        if not os.path.exists(path):
            return jsonify({"error": "path does not exist"}), 404
        subprocess.run(["open", path], check=False)
        return jsonify({"ok": True})

    def build_linked_story_index() -> dict:
        linked = {}
        for story_id, entry in read_story_sessions(story_sessions_path).items():
            if story_id in {"pin", "pinned_epics"}:
                continue
            for record in entry.get("sessions", []):
                if not isinstance(record, dict):
                    continue
                key = (str(record.get("provider", "")), str(record.get("session_id", "")))
                linked.setdefault(key, []).append(story_id)
        return linked

    @app.route("/api/sessions")
    def api_sessions():
        if cloud_mode:
            return jsonify({"sessions": []})
        try:
            limit = int(request.args.get("limit", 25))
        except ValueError:
            limit = 25
        limit = min(max(limit, 1), 100)
        linked = build_linked_story_index()
        try:
            running = terminal_focus.list_running_sessions()
        except Exception:
            running = {}
        pinned_keys = {
            (record["provider"], record["session_id"]) for record in read_pins(pins_path)["sessions"]
        }
        sessions = session_scanner.list_recent_sessions(limit)
        for record in sessions:
            key = (record["provider"], record["session_id"])
            record["linked_story_ids"] = linked.get(key, [])
            record["running"] = key in running
            record["pinned"] = key in pinned_keys
        return jsonify({"sessions": sessions})

    @app.route("/api/agent-work-pulse")
    def api_agent_work_pulse():
        return jsonify(pulse_metrics.snapshot())

    @app.route("/api/pins")
    def api_pins():
        if cloud_mode:
            return jsonify({"sessions": [], "stories": []})
        return jsonify(annotate_pins(read_pins(pins_path)))

    @app.route("/api/pins/toggle", methods=["POST"])
    def api_pins_toggle():
        if cloud_mode:
            return jsonify({"error": "pinning is only available on the laptop"}), 501
        payload = request.get_json(silent=True) or {}
        kind = str(payload.get("kind", "")).strip()

        if kind == "session":
            provider = str(payload.get("provider", "")).strip()
            session_id = str(payload.get("session_id", "")).strip()
            if provider not in PIN_PROVIDERS or not session_scanner.SESSION_ID_RE.match(session_id):
                return jsonify({"error": "a valid provider and session_id are required"}), 400
            record = {
                "provider": provider,
                "session_id": session_id,
                "title": str(payload.get("title", "")),
                "cwd": str(payload.get("cwd", "")),
                "started_at": str(payload.get("started_at", "")),
                "pinned_at": datetime.now(timezone.utc).isoformat(),
            }
            state = {}

            def toggle_session(data):
                kept = [
                    item
                    for item in data["sessions"]
                    if (item["provider"], item["session_id"]) != (provider, session_id)
                ]
                state["pinned"] = len(kept) == len(data["sessions"])
                data["sessions"] = [record] + kept if state["pinned"] else kept
                return data

            pins = update_pins(pins_path, toggle_session)
            return jsonify({"ok": True, "pinned": state["pinned"], "pins": annotate_pins(pins)})

        if kind == "story":
            story_id = str(payload.get("story_id", "")).strip()
            if not story_id or story_id in {"pin", "pinned_epics"}:
                return jsonify({"error": "a valid story_id is required"}), 400
            state = {}

            def toggle_story(data):
                kept = [item for item in data["stories"] if item != story_id]
                state["pinned"] = len(kept) == len(data["stories"])
                data["stories"] = [story_id] + kept if state["pinned"] else kept
                return data

            pins = update_pins(pins_path, toggle_story)
            return jsonify({"ok": True, "pinned": state["pinned"], "pins": annotate_pins(pins)})

        return jsonify({"error": "kind must be 'session' or 'story'"}), 400

    @app.route("/api/sessions/<provider>/<session_id>/preview")
    def api_session_preview(provider: str, session_id: str):
        if cloud_mode:
            return jsonify({"error": "not available in cloud deployment"}), 404
        if provider not in {"claude", "codex"}:
            return jsonify({"error": "unknown provider"}), 400
        preview = session_scanner.read_session_preview(provider, session_id)
        if preview is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(preview)

    @app.route("/api/sessions/link", methods=["POST"])
    def api_sessions_link():
        payload = request.get_json(silent=True) or {}
        story_id = str(payload.get("story_id", "")).strip()
        session_id = str(payload.get("session_id", "")).strip()
        provider = str(payload.get("provider", "")).strip()
        if not story_id or not session_id or provider not in {"claude", "codex"}:
            return jsonify({"error": "story_id, session_id, and a valid provider are required"}), 400
        if story_id in {"pin", "pinned_epics"}:
            return jsonify({"error": "invalid story_id"}), 400

        record = {
            "session_id": session_id,
            "provider": provider,
            "started_at": str(payload.get("started_at", "")),
            "title": str(payload.get("title", "")),
            "cwd": str(payload.get("cwd", "")),
            "linked_at": datetime.now(timezone.utc).isoformat(),
        }
        result = {}

        def append_session(data):
            entry = data.setdefault(story_id, {"sessions": [], "notes": []})
            existing = {
                (str(item.get("provider", "")), str(item.get("session_id", "")))
                for item in entry["sessions"]
                if isinstance(item, dict)
            }
            if (provider, session_id) not in existing:
                entry["sessions"].insert(0, record)
            result["entry"] = entry
            return data

        update_story_sessions(story_sessions_path, append_session)
        return jsonify({"ok": True, "entry": result["entry"]})

    @app.route("/api/sessions/resume", methods=["POST"])
    def api_sessions_resume():
        if cloud_mode:
            return jsonify({"error": "session launch is only available on the laptop"}), 501
        payload = request.get_json(silent=True) or {}
        provider = str(payload.get("provider", "")).strip()
        session_id = str(payload.get("session_id", "")).strip()
        cwd = str(payload.get("cwd", "")).strip()
        runtime = RUNTIMES.get(provider)
        if runtime is None or not session_scanner.SESSION_ID_RE.match(session_id):
            return jsonify({"error": "a valid provider and session_id are required"}), 400
        cwd_path = Path(cwd) if cwd else Path.home()
        if not cwd_path.is_dir():
            cwd_path = Path.home()
        try:
            if terminal_focus.focus_session(provider, session_id):
                return jsonify({"ok": True, "focused": True})
        except Exception:
            pass
        try:
            runtime.resume(session_id, cwd_path)
        except Exception as error:
            return jsonify({"error": str(error)}), 502
        return jsonify({"ok": True, "focused": False})

    @app.route("/api/codex/prompt-preview", methods=["POST"])
    def api_codex_prompt_preview():
        payload = request.get_json(silent=True) or {}
        story_id = str(payload.get("story_id", "")).strip()
        provider = str(payload.get("provider") or "codex").strip()
        runtime = RUNTIMES.get(provider)
        if runtime is None:
            return jsonify({"error": f"unknown provider: {provider}"}), 400
        story, project_dir = resolve_story(story_id)
        if story is None:
            return jsonify({"error": "story not found"}), 404
        return jsonify({"prompt": runtime.preview(story, project_dir)})

    @app.route("/api/codex/launch", methods=["POST"])
    def api_codex_launch():
        if cloud_mode:
            return jsonify({"error": "session launch is only available on the laptop"}), 501
        payload = request.get_json(silent=True) or {}
        story_id = str(payload.get("story_id", "")).strip()
        provider = str(payload.get("provider") or "codex").strip()
        runtime = RUNTIMES.get(provider)
        if runtime is None:
            return jsonify({"error": f"unknown provider: {provider}"}), 400
        story, project_dir = resolve_story(story_id)
        if story is None:
            return jsonify({"error": "story not found"}), 404
        if not project_dir.is_dir():
            return jsonify({"error": "project directory not found"}), 400
        try:
            context = runtime.start(story, project_dir)
        except Exception as error:
            return jsonify({"error": str(error)}), 502
        token = uuid.uuid4().hex
        with session_launches_lock:
            session_launches[token] = {"status": "pending"}
        threading.Thread(target=finish_launch, args=(token, story, context, provider), daemon=True).start()
        return jsonify({"token": token, "status": "pending"}), 202

    @app.route("/api/codex/launch/<token>")
    def api_codex_launch_status(token):
        with session_launches_lock:
            status = session_launches.get(token)
        if status is None:
            return jsonify({"error": "launch not found"}), 404
        return jsonify(status)

    @app.route("/favicon.ico")
    def favicon():
        return Response(status=204)

    @app.route("/<path:filename>")
    def serve_static(filename: str):
        return send_file(tree_viewer_dir / filename)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5056, debug=False)
