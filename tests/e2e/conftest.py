from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.fakes import (
    FAKE_CLAUDE,
    FAKE_SANDBOX_TOOL,
    install_confinement_fakes,
    install_fake,
)
from weekend_loop.cli import EXIT_OK, main
from weekend_loop.models import Workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY_ROOT / "examples"
REPO_KEY = "demo"
CHEAP_GATE = "python3 -m compileall -q {changed_python_files}"
OAUTH_TOKEN = "fake-oauth-token"
SECRET_MODE = 0o600
ASSESSOR_COST = 0.05
WORKER_COST = 0.4
EXECUTE_ISSUES = (1, 2)
CHAT_PATHS = ["pocketchat/chat.py", "pocketchat/routes.py"]
README_PATHS = ["README.md"]
PARALLEL_WORKERS = 2


def assessment(verdict: str, questions: list[str], touched_paths: list[str]) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "effort": "XS",
        "risk": "tests",
        "blockers": [],
        "plan": "Read the file the issue names and make the smallest change that satisfies it.",
        "touched_paths": touched_paths,
        "questions": questions,
        "confidence": "high",
    }


def result(structured_output: dict[str, Any], cost_usd: float) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 1000,
        "num_turns": 2,
        "total_cost_usd": cost_usd,
        "session_id": "session",
        "structured_output": structured_output,
        "result": "done",
    }


def delivery(summary: str, questions: list[str], status: str) -> dict[str, Any]:
    return {
        "status": status,
        "commit_subject": "fix(chat): start a new chat from the greeting",
        "summary": summary,
        "verification": "python3 -m compileall",
        "judgement_calls": [],
        "questions": questions,
        "files_changed": ["pocketchat/chat.py"],
        "confidence": "high",
    }


def write_assistant_plans(binaries: Path) -> None:
    verdicts = {
        "1": result(assessment("execute", [], CHAT_PATHS), ASSESSOR_COST),
        "2": result(assessment("execute", [], README_PATHS), ASSESSOR_COST),
        "3": result(assessment("needs_input", ["Which rating scale?"], CHAT_PATHS), ASSESSOR_COST),
        "default": result(assessment("skip", [], []), ASSESSOR_COST),
    }
    (binaries / "claude-responses.json").write_text(json.dumps(verdicts))
    restarted = "GREETING = 'Hello'\n\n\ndef restart() -> str:\n    return GREETING\n"
    worker = {
        "files": {"pocketchat/chat.py": restarted},
        "stream": [result(delivery("started the chat over", [], "done"), WORKER_COST)],
        "exit_code": 0,
    }
    (binaries / "claude-worker.json").write_text(json.dumps([worker]))
    (binaries / "claude-probe.json").write_text(
        json.dumps({"stream": [result({}, 0.01)], "exit_code": 0})
    )


@pytest.fixture
def binaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    install_fake(directory, "claude", FAKE_CLAUDE)
    install_fake(directory, "socat", FAKE_SANDBOX_TOOL)
    install_confinement_fakes(directory)
    install_fake(directory, "bwrap", FAKE_SANDBOX_TOOL)
    monkeypatch.setenv("PATH", f"{directory}:/usr/bin:/bin")
    write_assistant_plans(directory)
    return directory


def affordable(document: dict[str, Any]) -> dict[str, Any]:
    repo = {
        **document["repos"][REPO_KEY],
        "setup_commands": [],
        "gate_commands": [CHEAP_GATE],
        "acceptance_command": None,
    }
    return {
        **document,
        "repos": {**document["repos"], REPO_KEY: repo},
        "budget": {**document.get("budget", {}), "max_tasks": 1},
    }


def in_parallel(document: dict[str, Any]) -> dict[str, Any]:
    cheap = affordable(document)
    return {
        **cheap,
        "budget": {**cheap["budget"], "max_tasks": PARALLEL_WORKERS},
        "worker": {**cheap.get("worker", {}), "parallel": PARALLEL_WORKERS},
    }


def prepared_workspace(
    tmp_path: Path, adjust: Callable[[dict[str, Any]], dict[str, Any]]
) -> Workspace:
    root = tmp_path / "workspace"
    assert main(["--home", str(root), "init", "--demo", "--example", str(EXAMPLES)]) == EXIT_OK
    workspace = Workspace(root=root)
    workspace.config_path.write_text(
        yaml.safe_dump(adjust(yaml.safe_load(workspace.config_path.read_text())))
    )
    workspace.oauth_token_path.write_text(OAUTH_TOKEN)
    workspace.oauth_token_path.chmod(SECRET_MODE)
    assert main(["--home", str(root), "demo", "up", "--repo-key", REPO_KEY]) == EXIT_OK
    return workspace


@pytest.fixture
def workspace(tmp_path: Path, binaries: Path) -> Workspace:
    return prepared_workspace(tmp_path, affordable)


@pytest.fixture
def parallel_workspace(tmp_path: Path, binaries: Path) -> Workspace:
    return prepared_workspace(tmp_path, in_parallel)
