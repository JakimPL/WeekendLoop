from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Final

from tests.unit.conftest import (
    assessment_payload,
    claude_response,
    issue_payload,
    write_claude_responses,
    write_execute_policy,
    write_github_data,
)
from tests.unit.test_execute import build_origin
from tests.unit.test_triage import TEMPLATE_BODY
from tests.unit.test_triage import prepare as seed_dry_run
from weekend_loop.briefing import read_prepared
from weekend_loop.cli import main
from weekend_loop.models import ALERT_WEBHOOK_NAME
from weekend_loop.notify import WEBHOOK_PAYLOAD_KEY
from weekend_loop.policy import policy_at
from weekend_loop.questions import QUESTION_MARKER, is_agent_comment
from weekend_loop.runs import latest_run_id, load_run_state, open_run_directory

QUESTION: Final[str] = "Which column is speed?"


class CapturingHandler(BaseHTTPRequestHandler):
    bodies: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        CapturingHandler.bodies.append(self.rfile.read(length).decode())
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def start_capturing_webhook(secrets_directory: Path) -> HTTPServer:
    CapturingHandler.bodies = []
    server = HTTPServer(("127.0.0.1", 0), CapturingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    secrets_directory.mkdir(parents=True, exist_ok=True)
    (secrets_directory / ALERT_WEBHOOK_NAME).write_text(
        f"http://127.0.0.1:{server.server_port}/hook\n"
    )
    return server


def read_bodies(fake_binaries: Path) -> list[dict[str, Any]]:
    path = fake_binaries / "gh-bodies.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def gh_calls(fake_binaries: Path) -> list[list[str]]:
    return [
        json.loads(line) for line in (fake_binaries / "gh-calls.jsonl").read_text().splitlines()
    ]


def test_a_prepare_pass_assesses_and_leaves_the_triage_for_the_weekend_to_take_up(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_dry_run(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))

    assert main(["--home", str(policy_path), "prepare", "--repo-key", "dryrun"]) == 0

    policy = policy_at(policy_path)
    run_id = latest_run_id(policy.state_dir)
    run_directory = open_run_directory(policy.state_dir, run_id)
    assert QUESTION in run_directory.plan_path.read_text()
    prepared = read_prepared(policy.state_dir, "dryrun")
    assert prepared is not None
    assert prepared.run_id == run_id
    assert prepared.question_count == 1


def test_a_prepare_pass_never_reaches_the_worker(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_dry_run(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))

    main(["--home", str(policy_path), "prepare", "--repo-key", "dryrun"])

    assert "acceptEdits" not in (fake_binaries / "claude-calls.jsonl").read_text()


def test_the_alert_spells_out_the_question_the_operator_has_to_answer(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_dry_run(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))
    policy = policy_at(policy_path)
    server = start_capturing_webhook(policy.workspace.secrets_dir)

    main(["--home", str(policy_path), "prepare", "--repo-key", "dryrun"])
    server.shutdown()

    payload = json.loads(CapturingHandler.bodies[0])
    assert QUESTION in payload[WEBHOOK_PAYLOAD_KEY]
    assert "#2" in payload[WEBHOOK_PAYLOAD_KEY]


def test_a_dry_run_repository_is_asked_nothing_on_github(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_dry_run(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))

    main(["--home", str(policy_path), "prepare", "--repo-key", "dryrun"])

    writes = {("issue", "create"), ("issue", "comment"), ("issue", "edit")}
    assert all(tuple(call[:2]) not in writes for call in gh_calls(fake_binaries))


def test_the_state_the_prepare_pass_saves_is_the_one_a_run_can_open(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_dry_run(tmp_path, fake_binaries, datetime.now(UTC) + timedelta(days=2))

    main(["--home", str(policy_path), "prepare", "--repo-key", "dryrun"])

    policy = policy_at(policy_path)
    run_directory = open_run_directory(policy.state_dir, latest_run_id(policy.state_dir))
    state = load_run_state(run_directory)
    assert state.repo_key == "dryrun"
    assert state.spent_usd == 0.05


def seed_execute_repository(tmp_path: Path, fake_binaries: Path) -> Path:
    policy_path = write_execute_policy(tmp_path, build_origin(tmp_path), None, 3)
    write_github_data(
        fake_binaries,
        issues=[issue_payload(2, "Improve the summary", TEMPLATE_BODY, ["weekend:auto"], [])],
        pull_requests=[],
        comments={},
        push=True,
    )
    write_claude_responses(
        fake_binaries,
        {
            "default": claude_response(
                assessment_payload("needs_input", "S", "behaviour", ["unclear_goal"], [QUESTION]),
                0.03,
            )
        },
    )
    return policy_path


def test_an_execute_repository_is_asked_on_the_issue_itself(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_execute_repository(tmp_path, fake_binaries)

    assert main(["--home", str(policy_path), "prepare", "--repo-key", "demo"]) == 0

    calls = gh_calls(fake_binaries)
    assert all(call[:2] != ["issue", "create"] for call in calls)
    comment = next(
        body for body in read_bodies(fake_binaries) if body["arguments"][:2] == ["issue", "comment"]
    )
    assert comment["arguments"][2] == "2"
    assert QUESTION_MARKER in comment["body"]
    assert f"1. {QUESTION}" in comment["body"]
    assert is_agent_comment(comment["body"])
    label = next(call for call in calls if call[:2] == ["issue", "edit"])
    assert label[2] == "2"
    assert "weekend:needs-input" in label


def test_the_prepared_session_is_stamped_after_the_agent_touched_the_issues(
    tmp_path: Path, fake_binaries: Path, fake_git: Path
) -> None:
    policy_path = seed_execute_repository(tmp_path, fake_binaries)
    started = datetime.now(UTC)

    main(["--home", str(policy_path), "prepare", "--repo-key", "demo"])

    prepared = read_prepared(policy_at(policy_path).state_dir, "demo")
    assert prepared is not None
    assert prepared.prepared_at > started
