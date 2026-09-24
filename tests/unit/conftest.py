from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.fakes import (
    FAKE_CLAUDE,
    FAKE_GH,
    FAKE_GIT,
    FAKE_SANDBOX_TOOL,
    install_fake,
)
from weekend_loop.models import (
    Assessment,
    Backend,
    Blocker,
    Confidence,
    Effort,
    EligibilityDecision,
    IneligibilityReason,
    Policy,
    RepoMode,
    Risk,
    RunPhase,
    RunState,
    Task,
    TaskStatus,
    Verdict,
    Workspace,
)
from weekend_loop.policy import policy_at
from weekend_loop.workspace_init import initialise_workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPOSITORY_ROOT / "examples" / "config.yaml"
OPERATOR_HOME = Path("/operator-home")
OWNER_LOGIN = "example-operator"
DRY_RUN_REPO_KEY = "dryrun"
WEEK_START = {"day_of_week": "mon", "hour": 0, "minute": 0}


@pytest.fixture
def fake_binaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    install_fake(directory, "claude", FAKE_CLAUDE)
    install_fake(directory, "gh", FAKE_GH)
    install_fake(directory, "socat", FAKE_SANDBOX_TOOL)
    install_fake(directory, "bwrap", FAKE_SANDBOX_TOOL)
    monkeypatch.setenv("PATH", f"{directory}:{Path('/usr/bin')}:{Path('/bin')}")
    return directory


@pytest.fixture
def fake_git(fake_binaries: Path) -> Path:
    install_fake(fake_binaries, "git", FAKE_GIT)
    return fake_binaries


def assessment_payload(
    verdict: str, effort: str, risk: str, blockers: list[str], questions: list[str]
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "effort": effort,
        "risk": risk,
        "blockers": blockers,
        "plan": "Read the failing test, fix the parser, run the suite.",
        "touched_paths": ["src/module.py"],
        "questions": questions,
        "confidence": "high",
    }


def claude_response(structured_output: dict[str, Any], cost_usd: float) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1000,
        "num_turns": 2,
        "result": "done",
        "session_id": "11111111-2222-3333-4444-555555555555",
        "total_cost_usd": cost_usd,
        "structured_output": structured_output,
        "permission_denials": [],
    }


def write_claude_responses(
    directory: Path, responses: dict[str, dict[str, Any] | list[dict[str, Any]]]
) -> None:
    (directory / "claude-responses.json").write_text(json.dumps(responses))


def issue_payload(
    number: int, title: str, body: str, labels: list[str], assignees: list[str]
) -> dict[str, Any]:
    moment = datetime.now(UTC) - timedelta(days=10)
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": name} for name in labels],
        "assignees": [{"login": login} for login in assignees],
        "milestone": None,
        "author": {"login": OWNER_LOGIN},
        "createdAt": moment.isoformat(),
        "updatedAt": moment.isoformat(),
        "url": f"https://github.com/owner/repo/issues/{number}",
    }


def write_github_data(
    directory: Path,
    issues: list[dict[str, Any]],
    pull_requests: list[dict[str, Any]],
    comments: dict[str, list[dict[str, Any]]],
    push: bool,
) -> None:
    payload = {
        "login": OWNER_LOGIN,
        "push": push,
        "issues": issues,
        "pull_requests": pull_requests,
        "comments": comments,
    }
    (directory / "gh-data.json").write_text(json.dumps(payload))


def write_secret(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    path.chmod(0o600)
    return path


def usage_event(
    five_hour_utilization: float,
    seven_day_utilization: float,
    five_hour_resets_at: datetime,
    seven_day_resets_at: datetime,
) -> dict[str, Any]:
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed_warning",
            "resetsAt": int(seven_day_resets_at.timestamp()),
            "rateLimitType": "seven_day",
            "utilization": seven_day_utilization,
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {
                    "utilization": five_hour_utilization,
                    "resetsAt": int(five_hour_resets_at.timestamp()),
                },
                "seven_day": {
                    "utilization": seven_day_utilization,
                    "resetsAt": int(seven_day_resets_at.timestamp()),
                },
            },
        },
    }


# Limit shapes read from the claude 2.1.276 binary and docs; no real limit hit has been captured.
def rejected_event(rate_limit_type: str, resets_at: datetime | None) -> dict[str, Any]:
    info: dict[str, Any] = {"status": "rejected", "rateLimitType": rate_limit_type}
    if resets_at is not None:
        info["resetsAt"] = int(resets_at.timestamp())
    return {"type": "rate_limit_event", "rate_limit_info": info}


def limit_result(text: str, cost_usd: float) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 429,
        "result": text,
        "session_id": "$SESSION",
        "total_cost_usd": cost_usd,
        "permission_denials": [],
    }


def error_result(
    subtype: str, errors: list[str], terminal_reason: str | None, cost_usd: float
) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": True,
        "errors": errors,
        "terminal_reason": terminal_reason,
        "session_id": "$SESSION",
        "total_cost_usd": cost_usd,
        "permission_denials": [],
    }


def write_probe_plan(directory: Path, usage: dict[str, Any] | None, cost_usd: float) -> None:
    plan = {
        "stream": [
            *([] if usage is None else [usage]),
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "ok",
                "total_cost_usd": cost_usd,
                "permission_denials": [],
            },
        ],
        "exit_code": 0,
    }
    (directory / "claude-probe.json").write_text(json.dumps(plan))


def worker_plan(
    files: dict[str, str], stream: list[dict[str, Any]], exit_code: int
) -> dict[str, Any]:
    return {"files": files, "stream": stream, "exit_code": exit_code}


def write_worker_plans(directory: Path, plans: list[dict[str, Any]]) -> None:
    (directory / "claude-worker.json").write_text(json.dumps(plans))


def write_worker_stream(
    directory: Path, files: dict[str, str], stream: list[dict[str, Any]], exit_code: int
) -> None:
    plan = worker_plan(files, stream, exit_code)
    (directory / "claude-worker.json").write_text(json.dumps(plan))


def worker_result(delivery: dict[str, Any], cost_usd: float, text: str) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "session_id": "$SESSION",
        "total_cost_usd": cost_usd,
        "structured_output": delivery,
        "permission_denials": [],
    }


def write_worker_plan(
    directory: Path,
    files: dict[str, str],
    delivery: dict[str, Any],
    cost_usd: float,
    usage: dict[str, Any] | None,
) -> None:
    stream = [
        *([] if usage is None else [usage]),
        {"type": "assistant", "message": "editing"},
        worker_result(delivery, cost_usd, "done"),
    ]
    write_worker_stream(directory, files, stream, 0)


def delivery_payload(status: str, commit_subject: str, questions: list[str]) -> dict[str, Any]:
    return {
        "status": status,
        "commit_subject": commit_subject,
        "summary": "Treated an empty speed field as unknown.",
        "verification": "uv run --no-sync pytest tests",
        "judgement_calls": [],
        "questions": questions,
        "files_changed": ["logbook/records.py"],
        "confidence": "high",
    }


def dry_run_repo() -> dict[str, Any]:
    return {
        "slug": "example-org/example-repo",
        "mode": RepoMode.DRY_RUN.value,
        "backend": Backend.GITHUB.value,
        "token_file": "state/gh-dryrun.token",
        "base_branch": "main",
        "recurse_submodules": True,
        "remote_url": None,
        "setup_commands": ["uv sync"],
        "acceptance_command": None,
        "conventions_prompt": "examples/conventions.md",
        "gate_commands": ["uv run --no-sync pytest tests/unit --tb=short -q"],
        "forbidden_paths": [".github/**", "pyproject.toml", "**/.env"],
    }


def base_policy(tmp_path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(REAL_CONFIG.read_text())
    raw["repos"][DRY_RUN_REPO_KEY] = dry_run_repo()
    raw["repos"]["demo"]["backend"] = Backend.GITHUB.value
    raw["schedule"]["window"] = {"opens": WEEK_START, "closes": WEEK_START}
    for key, repo in raw["repos"].items():
        repo["token_file"] = f"secrets/github-{key}.token"
        repo["conventions_prompt"] = str(REPOSITORY_ROOT / "examples" / "conventions.md")
    return raw


def write_policy(tmp_path: Path, raw: dict[str, Any]) -> Path:
    initialise_workspace(tmp_path, OPERATOR_HOME, force=True, example=None)
    workspace = Workspace(root=tmp_path)
    workspace.config_path.write_text(yaml.safe_dump(raw))
    write_secret(workspace.oauth_token_path, "fake-oauth-token")
    for key in raw["repos"]:
        write_secret(workspace.repository_token_path(key), f"fake-{key}-token")
    return tmp_path


def write_test_policy(tmp_path: Path, weekly_reset_at: datetime | None) -> Path:
    raw = base_policy(tmp_path)
    raw["budget"]["weekly_reset_at"] = (
        weekly_reset_at.isoformat() if weekly_reset_at is not None else None
    )
    return write_policy(tmp_path, raw)


def write_execute_policy(
    tmp_path: Path, remote_url: str, acceptance_command: str | None, max_tasks: int
) -> Path:
    raw = base_policy(tmp_path)
    raw["budget"]["weekly_reset_at"] = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    raw["budget"]["max_tasks"] = max_tasks
    demo = raw["repos"]["demo"]
    demo["remote_url"] = remote_url
    demo["setup_commands"] = []
    demo["acceptance_command"] = acceptance_command
    demo["gate_commands"] = ["python3 -m compileall -q {changed_python_files}"]
    return write_policy(tmp_path, raw)


def build_assessment(
    verdict: Verdict, effort: Effort, risk: Risk, blockers: list[Blocker], questions: list[str]
) -> Assessment:
    return Assessment(
        verdict=verdict,
        effort=effort,
        risk=risk,
        blockers=blockers,
        plan="Fix the parser and extend the test.",
        touched_paths=["src/module.py"],
        questions=questions,
        confidence=Confidence.HIGH,
    )


def build_task(
    issue_number: int,
    title: str,
    status: TaskStatus,
    eligibility: EligibilityDecision,
    assessment: Assessment | None,
) -> Task:
    return Task(
        issue_number=issue_number,
        title=title,
        status=status,
        eligibility=eligibility,
        spec_signals=None,
        assessment=assessment,
        delivery=None,
        gate=None,
        branch=None,
        pull_request_url=None,
        session_id=None,
        attempts=1 if assessment is not None else 0,
        cost_usd=0.1 if assessment is not None else 0.0,
    )


def build_run_state(
    tasks: list[Task],
    spent_usd: float,
    notes: list[str],
    run_id: str,
    repo_key: str,
    mode: RepoMode,
) -> RunState:
    moment = datetime.now(UTC)
    return RunState(
        run_id=run_id,
        repo_key=repo_key,
        mode=mode,
        phase=RunPhase.FINISHED,
        started_at=moment,
        updated_at=moment,
        heartbeat_at=moment,
        envelope_usd=15.0,
        spent_usd=spent_usd,
        usage=None,
        tasks=tasks,
        notes=notes,
    )


ELIGIBLE = EligibilityDecision(eligible=True, reasons=[])
NEVER_LABELLED = EligibilityDecision(eligible=False, reasons=[IneligibilityReason.NEVER_LABEL])


@pytest.fixture
def workspace_policy(tmp_path: Path) -> Policy:
    return policy_at(write_policy(tmp_path, base_policy(tmp_path)))
