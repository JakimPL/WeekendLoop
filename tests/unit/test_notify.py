from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Final

import pytest

from tests.unit.conftest import (
    ELIGIBLE,
    build_assessment,
    build_run_state,
    build_task,
    write_test_policy,
)
from weekend_loop.cli import EXIT_BLOCKED, EXIT_OK, main
from weekend_loop.models import (
    ALERT_WEBHOOK_NAME,
    Blocker,
    Effort,
    RepoMode,
    Risk,
    RunState,
    ScheduledCommand,
    TaskStatus,
    Verdict,
)
from weekend_loop.notify import (
    SLACK_PAYLOAD_KEY,
    WEBHOOK_PAYLOAD_KEY,
    payload_key,
    render_exit_alert,
    render_finish_alert,
    render_question_alert,
    send_alert,
)
from weekend_loop.policy import policy_at

RUN_ID: Final[str] = "20260918-210000-demo"
SLACK_URL: Final[str] = "https://hooks.slack.com/services/T000/B000/xxxx"
DISCORD_URL: Final[str] = "https://discord.com/api/webhooks/1/xxxx"
QUESTION: Final[str] = "Which timezone should the parser assume?"
CLOSED_PORT_URL: Final[str] = "http://127.0.0.1:1/hook"


class CapturingHandler(BaseHTTPRequestHandler):
    bodies: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        CapturingHandler.bodies.append(self.rfile.read(length).decode())
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def build_state_with_question() -> RunState:
    task = build_task(
        7,
        "Parse the log timestamps",
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(
            Verdict.NEEDS_INPUT, Effort.S, Risk.TESTS, [Blocker.UNCLEAR_GOAL], [QUESTION]
        ),
    )
    return build_run_state([task], 0.4, [], RUN_ID, "demo", RepoMode.EXECUTE)


def write_webhook(directory: Path, url: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    webhook = directory / ALERT_WEBHOOK_NAME
    webhook.write_text(f"{url}\n")
    return webhook


def test_slack_reads_its_message_from_a_different_field_than_discord() -> None:
    assert payload_key(SLACK_URL) == SLACK_PAYLOAD_KEY
    assert payload_key(DISCORD_URL) == WEBHOOK_PAYLOAD_KEY


def test_the_question_alert_carries_the_run_and_every_question_in_full() -> None:
    text = render_question_alert(build_state_with_question())

    assert RUN_ID in text
    assert QUESTION in text
    assert "#7" in text


def test_the_question_alert_sends_the_operator_to_the_issue_threads() -> None:
    text = render_question_alert(build_state_with_question())

    assert "comment on each issue" in text
    assert "tunnel" not in text


def test_the_finish_alert_names_the_spend_and_the_branches_a_human_can_review() -> None:
    state = build_run_state(
        [],
        4.1,
        ["digest posted at https://example.test/issues/9"],
        RUN_ID,
        "demo",
        RepoMode.EXECUTE,
    )

    text = render_finish_alert(state)

    assert "$4.10 of $15.00" in text
    assert "https://example.test/issues/9" in text


def test_an_alert_reaches_a_webhook_under_the_field_that_service_reads(tmp_path: Path) -> None:
    CapturingHandler.bodies = []
    server = HTTPServer(("127.0.0.1", 0), CapturingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webhook = write_webhook(tmp_path, f"http://127.0.0.1:{server.server_port}/hook")

    delivered = send_alert(webhook, "three questions are open")
    server.shutdown()

    assert delivered
    assert json.loads(CapturingHandler.bodies[0]) == {
        WEBHOOK_PAYLOAD_KEY: "three questions are open"
    }


def test_a_run_without_a_webhook_file_sends_nothing_and_says_so(tmp_path: Path) -> None:
    assert send_alert(tmp_path / ALERT_WEBHOOK_NAME, "three questions are open") is False


def test_an_unreachable_webhook_is_reported_rather_than_raised(tmp_path: Path) -> None:
    webhook = write_webhook(tmp_path, CLOSED_PORT_URL)

    assert send_alert(webhook, "three questions are open") is False


def test_the_webhook_url_never_appears_in_anything_the_run_renders(tmp_path: Path) -> None:
    write_webhook(tmp_path, SLACK_URL)
    state = build_state_with_question()

    rendered = render_question_alert(state) + render_finish_alert(state)

    assert SLACK_URL not in rendered


def serve_webhook(secrets_directory: Path) -> HTTPServer:
    CapturingHandler.bodies = []
    server = HTTPServer(("127.0.0.1", 0), CapturingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    write_webhook(secrets_directory, f"http://127.0.0.1:{server.server_port}/hook")
    return server


def alert_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_result: str,
    exit_code: str,
    exit_status: str,
) -> list[str]:
    policy_path = write_test_policy(tmp_path, None)
    server = serve_webhook(policy_at(policy_path).workspace.secrets_dir)
    monkeypatch.setenv("SERVICE_RESULT", service_result)
    monkeypatch.setenv("EXIT_CODE", exit_code)
    monkeypatch.setenv("EXIT_STATUS", exit_status)

    code = main(["--home", str(policy_path), "alert-exit", "--unit", "weekend"])
    server.shutdown()

    assert code == EXIT_OK
    return [json.loads(body)[WEBHOOK_PAYLOAD_KEY] for body in CapturingHandler.bodies]


def test_the_exit_alert_names_the_signal_and_the_restart_that_follows() -> None:
    text = render_exit_alert(ScheduledCommand.WEEKEND, "signal", "killed", "KILL")

    assert text.startswith("Weekend Loop weekend run ended with signal KILL (signal);")
    assert "systemd restarts it" in text
    assert "resumes from disk" in text
    assert "journalctl --user -u weekend-loop-weekend" in text


def test_a_crashed_prepare_round_waits_for_its_next_timer() -> None:
    text = render_exit_alert(ScheduledCommand.PREPARE, "exit-code", "exited", "1")

    assert "prepare run exited with status 1 (exit-code)" in text
    assert "systemctl --user start weekend-loop-prepare.service" in text
    assert "restarts" not in text


def test_a_run_that_crashed_sends_one_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = alert_exit(tmp_path, monkeypatch, "oom-kill", "killed", "KILL")

    assert len(messages) == 1
    assert "ended with signal KILL (oom-kill)" in messages[0]


@pytest.mark.parametrize(
    ("service_result", "exit_code", "exit_status"),
    [
        ("success", "exited", "0"),
        ("success", "killed", "TERM"),
        ("exit-code", "exited", str(EXIT_BLOCKED)),
    ],
)
def test_a_clean_stop_or_a_blocked_run_stays_silent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_result: str,
    exit_code: str,
    exit_status: str,
) -> None:
    assert alert_exit(tmp_path, monkeypatch, service_result, exit_code, exit_status) == []
