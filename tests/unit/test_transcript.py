from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest

from tests.unit.conftest import rejected_event
from tests.unit.live_run import (
    assistant,
    said,
    thought,
    tool_call,
    tool_results,
    write_transcript,
)
from weekend_loop.transcript import (
    MAX_LINE_WIDTH,
    TranscriptCursor,
    attach_transcript,
    follow_transcript,
    render_line,
    render_message,
    transcript_tail,
)

# Shapes captured from claude 2.1.276 on this host with --output-format stream-json --verbose.
INIT: Final[dict[str, Any]] = {
    "type": "system",
    "subtype": "init",
    "session_id": "5a559473-d30b-4fe4-b990-edc20b2c942e",
    "tools": ["Bash", "Read", "Edit"],
    "model": "claude-haiku-4-5-20251001",
}
THINKING_TOKENS: Final[dict[str, Any]] = {
    "type": "system",
    "subtype": "thinking_tokens",
    "estimated_tokens": 50,
}
RATE_LIMIT: Final[dict[str, Any]] = {
    "type": "rate_limit_event",
    "rate_limit_info": {
        "status": "allowed",
        "resetsAt": 1789731600,
        "rateLimitType": "five_hour",
        "isUsingOverage": False,
        "unifiedWindows": {
            "five_hour": {"utilization": 0.11, "resetsAt": 1789731600},
            "seven_day": {"utilization": 0.12, "resetsAt": 1790244000},
        },
    },
}
RESULT: Final[dict[str, Any]] = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 3,
    "result": "Done.",
    "total_cost_usd": 0.015056000000000002,
}


def test_the_start_of_a_call_names_its_model() -> None:
    assert render_message(INIT) == ["start: claude-haiku-4-5-20251001 with 3 tools"]


def test_thinking_and_speech_each_become_one_line() -> None:
    assert render_message(thought("The parser drops\nempty fields.")) == [
        "thinking: The parser drops empty fields."
    ]
    assert render_message(said("Reading the parser now.")) == ["says: Reading the parser now."]


def test_a_thinking_block_with_only_a_signature_stays_silent() -> None:
    assert render_message(thought("")) == []


@pytest.mark.parametrize(
    ("name", "tool_input", "expected"),
    [
        (
            "Bash",
            {"command": "uv run pytest\n  -q", "description": "Run tests"},
            "Bash: uv run pytest -q",
        ),
        (
            "Edit",
            {"file_path": "src/parser.py", "old_string": "a", "new_string": "b"},
            "Edit: src/parser.py",
        ),
        (
            "Write",
            {"file_path": "tests/test_parser.py", "content": "x"},
            "Write: tests/test_parser.py",
        ),
        ("Read", {"file_path": "README.md"}, "Read: README.md"),
        ("Grep", {"pattern": "def parse", "path": "src"}, "Grep: def parse in src"),
        ("Glob", {"pattern": "**/*.py"}, "Glob: **/*.py"),
        (
            "WebFetch",
            {"url": "https://example.com", "prompt": "summarise"},
            "WebFetch: https://example.com",
        ),
        ("TodoWrite", {"todos": [{"content": "fix"}]}, "TodoWrite"),
    ],
)
def test_each_tool_call_names_what_it_touches(
    name: str, tool_input: dict[str, Any], expected: str
) -> None:
    assert render_message(tool_call(name, tool_input)) == [expected]


def test_only_a_failed_tool_result_is_shown_by_its_first_line() -> None:
    message = tool_results(
        [
            ("file contents that nobody needs to see", False),
            ([{"type": "text", "text": "\nExit code 1\nTraceback (most recent call last)"}], True),
            ("Permission to use Bash was denied", True),
        ]
    )
    assert render_message(message) == [
        "tool error: Exit code 1",
        "tool error: Permission to use Bash was denied",
    ]


def test_an_allowance_reading_shows_both_windows_and_its_status() -> None:
    assert render_message(RATE_LIMIT) == ["allowance: five-hour 11%, seven-day 12% (allowed)"]


def test_a_rejection_without_windows_names_the_limit_it_hit() -> None:
    assert render_message(rejected_event("seven_day", None)) == ["allowance: rejected on seven_day"]


def test_the_result_sums_up_the_call() -> None:
    assert render_message(RESULT) == ["result: success, $0.02, 3 turns"]


def test_a_failed_result_carries_its_reason() -> None:
    message = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "num_turns": 1,
        "result": "You've hit your session limit · resets 11pm",
        "total_cost_usd": 0.0,
    }
    assert render_message(message) == [
        "result: error, $0.00, 1 turn, You've hit your session limit · resets 11pm"
    ]


def test_one_assistant_message_can_think_speak_and_act() -> None:
    message = assistant(
        [
            {"type": "thinking", "thinking": "Check the tests first."},
            {"type": "text", "text": "Running the suite."},
            {"type": "tool_use", "id": "toolu_2", "name": "Bash", "input": {"command": "make"}},
        ]
    )
    assert render_message(message) == [
        "thinking: Check the tests first.",
        "says: Running the suite.",
        "Bash: make",
    ]


@pytest.mark.parametrize(
    "line",
    [
        "",
        "not json at all",
        '{"type": "assistant", "message": {"content": [{"type": "te',
        "[1, 2, 3]",
        json.dumps(THINKING_TOKENS),
        json.dumps({"type": "stream_event", "event": {}}),
        json.dumps({"type": "assistant", "message": "editing"}),
        json.dumps({"type": "user", "message": {"content": "a plain prompt"}}),
    ],
)
def test_garbage_and_unknown_lines_are_skipped(line: str) -> None:
    assert render_line(line) == []


def test_a_long_line_is_clipped_to_the_width() -> None:
    [line] = render_message(said("word " * 100))
    assert len(line) == MAX_LINE_WIDTH
    assert line.endswith("…")


def test_the_tail_keeps_the_last_rendered_lines(tmp_path: Path) -> None:
    transcript = write_transcript(
        tmp_path / "worker-1.jsonl",
        [INIT, RATE_LIMIT, *[said(f"step {index}") for index in range(10)], RESULT],
    )
    assert transcript_tail(transcript, 3) == [
        "says: step 8",
        "says: step 9",
        "result: success, $0.02, 3 turns",
    ]
    assert transcript_tail(transcript, 0) == []


def test_a_transcript_that_does_not_exist_yet_reads_as_empty(tmp_path: Path) -> None:
    assert transcript_tail(tmp_path / "worker-1.jsonl", 5) == []
    cursor = TranscriptCursor(path=tmp_path / "worker-1.jsonl", offset=0)
    assert follow_transcript(cursor) == (cursor, [])


def test_the_follower_renders_a_line_only_once_it_is_complete(tmp_path: Path) -> None:
    transcript = write_transcript(tmp_path / "worker-1.jsonl", [INIT, said("first")])
    partial = json.dumps(said("second"))
    with transcript.open("a") as handle:
        handle.write(partial[:20])
    cursor, backlog = attach_transcript(transcript, 10)
    assert backlog == ["start: claude-haiku-4-5-20251001 with 3 tools", "says: first"]

    cursor, fresh = follow_transcript(cursor)
    assert fresh == []

    with transcript.open("a") as handle:
        handle.write(partial[20:] + "\n" + json.dumps(RESULT) + "\n")
    cursor, fresh = follow_transcript(cursor)
    assert fresh == ["says: second", "result: success, $0.02, 3 turns"]
    assert cursor.offset == transcript.stat().st_size
    assert follow_transcript(cursor) == (cursor, [])
