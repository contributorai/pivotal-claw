import json

try:
    from cc.pivotal import terminal_focus
except ModuleNotFoundError:
    import terminal_focus


class FakeCompleted:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def make_runner(ps_output="", osascript_output="focused", calls=None):
    def runner(argv, **kwargs):
        if calls is not None:
            calls.append(argv)
        if argv[0] == "ps":
            return FakeCompleted(stdout=ps_output)
        if argv[0] == "osascript":
            return FakeCompleted(stdout=osascript_output)
        raise AssertionError(f"unexpected command: {argv}")
    return runner


def write_session_file(claude_home, pid, session_id, cwd="/tmp/project"):
    sessions_dir = claude_home / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "sessionId": session_id, "cwd": cwd}), encoding="utf-8"
    )


def test_list_running_claude_sessions_maps_pid_to_tty(tmp_path):
    write_session_file(tmp_path, 111, "aaaa-bbbb")
    write_session_file(tmp_path, 222, "cccc-dddd")
    ps = "111 ttys003 claude\n222 ?? claude\n333 ttys009 vim notes.txt\n"
    running = terminal_focus.list_running_claude_sessions(tmp_path, make_runner(ps))
    assert running == {"aaaa-bbbb": {"pid": 111, "tty": "ttys003", "cwd": "/tmp/project"}}


def test_list_running_claude_sessions_ignores_stale_pid_reuse(tmp_path):
    write_session_file(tmp_path, 111, "aaaa-bbbb")
    ps = "111 ttys003 vim notes.txt\n"
    assert terminal_focus.list_running_claude_sessions(tmp_path, make_runner(ps)) == {}


def test_list_running_codex_sessions_matches_uuid_in_argv():
    session_id = "0198c5c8-7d31-7abc-8def-0123456789ab"
    ps = f"444 ttys005 codex resume {session_id}\n555 ttys006 codex\n666 ?? codex resume {session_id}\n"
    running = terminal_focus.list_running_codex_sessions(make_runner(ps))
    assert running == {session_id: {"pid": 444, "tty": "ttys005"}}


def test_focus_session_runs_osascript_with_tty_device(tmp_path):
    write_session_file(tmp_path, 111, "aaaa-bbbb")
    calls = []
    runner = make_runner("111 ttys003 claude\n", calls=calls)
    assert terminal_focus.focus_session("claude", "aaaa-bbbb", tmp_path, runner) is True
    osascript_calls = [argv for argv in calls if argv[0] == "osascript"]
    assert len(osascript_calls) == 1
    assert osascript_calls[0][-1] == "/dev/ttys003"


def test_focus_session_returns_false_when_not_running(tmp_path):
    runner = make_runner("")
    assert terminal_focus.focus_session("claude", "aaaa-bbbb", tmp_path, runner) is False


def test_focus_session_returns_false_when_tab_not_found(tmp_path):
    write_session_file(tmp_path, 111, "aaaa-bbbb")
    runner = make_runner("111 ttys003 claude\n", osascript_output="not-found")
    assert terminal_focus.focus_session("claude", "aaaa-bbbb", tmp_path, runner) is False
