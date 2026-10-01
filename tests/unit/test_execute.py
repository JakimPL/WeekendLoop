from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from tests.support.fakes import install_fake
from tests.unit.conftest import (
    ELIGIBLE,
    build_assessment,
    build_run_state,
    build_task,
    delivery_payload,
    issue_payload,
    usage_event,
    window_closing_soon,
    worker_plan,
    worker_result,
    write_execute_policy,
    write_github_data,
    write_probe_plan,
    write_probe_sequence,
    write_worker_plan,
)
from tests.unit.test_prefilter import build_issue
from weekend_loop.briefing import append_note, record_answer
from weekend_loop.cli import EXIT_BLOCKED, main
from weekend_loop.execute import (
    PROPOSAL_REASON,
    consent_for,
    refusal_reason,
    selectable,
    within_worker_limits,
)
from weekend_loop.mailbox import (
    answer_question,
    approve_issue,
    read_inbox,
    request_stop,
    skip_issue,
)
from weekend_loop.models import (
    Blocker,
    Consent,
    Effort,
    Policy,
    RepoMode,
    Risk,
    StopReason,
    TaskStatus,
    Verdict,
    Workspace,
)
from weekend_loop.policy import policy_at
from weekend_loop.runs import (
    create_run_directory,
    load_run_state,
    open_run_directory,
    save_run_state,
)

RUN_ID = "20260918-210000-demo"
ISSUE_TITLE = "Empty speed field"
ISSUE_BODY = "## Business requirement\nThe parser crashes.\n\n## Goal\nTreat it as unknown.\n"
BUGGY_RECORDS = "def speed(value: str) -> float:\n    return float(value)\n"
FIXED_RECORDS = (
    "def speed(value: str) -> float | None:\n"
    "    stripped = value.strip()\n"
    "    return float(stripped) if stripped else None\n"
)
ACCEPTANCE_SCRIPT = (
    'from pathlib import Path\n\nassert "strip()" in Path("logbook/records.py").read_text()\n'
)
ACCEPTANCE_COMMAND = "python3 {test_file}"
GENERATED_DIRECTORY = "generated"
GENERATED_MARKER = f"{GENERATED_DIRECTORY}/marker.txt"
MAKE_GENERATED = f"""#!/usr/bin/env python3
from pathlib import Path

marker = Path.cwd() / "{GENERATED_MARKER}"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text("built by setup")
"""
FIXED_ON_DISK_CHECK = (
    'python3 -c "import pathlib, sys; '
    "sys.exit(0 if 'strip()' in pathlib.Path('logbook/records.py').read_text() else 1)\""
)
GENERATED_CHECK = (
    'python3 -c "import pathlib, sys; '
    f"sys.exit(0 if pathlib.Path('{GENERATED_MARKER}').is_file() else 1)\""
)


def run_git(arguments: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout


def build_origin(tmp_path: Path) -> str:
    origin = tmp_path / "origin" / "logbook"
    origin.mkdir(parents=True)
    (origin / "records.py").write_text(BUGGY_RECORDS)
    root = origin.parent
    run_git(["init", "-b", "main"], root)
    run_git(["add", "--all"], root)
    run_git(
        ["-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-m", "initial"],
        root,
    )
    return str(root)


def seed_run(policy: Policy, issue_numbers: list[int]) -> None:
    tasks = [
        build_task(
            number,
            ISSUE_TITLE,
            TaskStatus.ASSESSED,
            ELIGIBLE,
            build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
        )
        for number in issue_numbers
    ]
    run_directory = create_run_directory(policy.state_dir, RUN_ID)
    save_run_state(run_directory, build_run_state(tasks, 0.0, [], RUN_ID, "demo", RepoMode.EXECUTE))


def prepare(
    tmp_path: Path,
    fake_binaries: Path,
    labels: list[str],
    files: dict[str, str],
    delivery: dict[str, object],
    max_tasks: int,
    issue_numbers: list[int],
) -> tuple[Policy, Path]:
    policy_path = write_execute_policy(
        tmp_path, build_origin(tmp_path), ACCEPTANCE_COMMAND, max_tasks
    )
    write_github_data(
        fake_binaries,
        issues=[
            issue_payload(number, ISSUE_TITLE, ISSUE_BODY, labels, []) for number in issue_numbers
        ],
        pull_requests=[],
        comments={},
        push=True,
    )
    write_worker_plan(fake_binaries, files, delivery, 1.2, None)
    policy = policy_at(policy_path)
    acceptance = tmp_path / "check.py"
    acceptance.write_text(ACCEPTANCE_SCRIPT)
    policy.state_dir.mkdir(parents=True, exist_ok=True)
    (policy.state_dir / "acceptance.json").write_text(
        json.dumps({str(number): str(acceptance) for number in issue_numbers})
    )
    seed_run(policy, issue_numbers)
    return policy, policy_path


def ignore_in_origin(tmp_path: Path, pattern: str) -> None:
    root = tmp_path / "origin"
    (root / ".gitignore").write_text(f"{pattern}/\n")
    run_git(["add", ".gitignore"], root)
    run_git(
        ["-c", "user.name=Seed", "-c", "user.email=seed@example.com", "commit", "-m", "ignore"],
        root,
    )


def amend_demo_repo(policy_path: Path, changes: dict[str, Any]) -> None:
    config = Workspace(root=policy_path).config_path
    raw = yaml.safe_load(config.read_text())
    raw["repos"]["demo"].update(changes)
    config.write_text(yaml.safe_dump(raw))


def build_on_setup(tmp_path: Path, fake_binaries: Path, policy_path: Path) -> None:
    ignore_in_origin(tmp_path, GENERATED_DIRECTORY)
    install_fake(fake_binaries, "make-generated", MAKE_GENERATED)
    amend_demo_repo(
        policy_path, {"setup_commands": ["make-generated"], "gate_commands": [GENERATED_CHECK]}
    )


def execute(policy_path: Path) -> int:
    arguments = ["--home", str(policy_path), "execute", "--repo-key", "demo", "--run-id", RUN_ID]
    return main(arguments)


def test_consent_comes_from_the_labels_a_human_wrote(workspace_policy: Policy) -> None:
    labels_policy = workspace_policy.labels
    cases = {
        "weekend:auto": Consent.AUTO,
        "weekend:approved": Consent.APPROVED,
        "weekend:never": Consent.NEVER,
        "bug": Consent.NEEDS_APPROVAL,
    }
    for label, expected in cases.items():
        issue = build_issue(1, ISSUE_TITLE, ISSUE_BODY, [label], [], [], None)
        assert consent_for(issue, labels_policy) is expected


def test_only_small_low_risk_unblocked_work_reaches_the_worker(workspace_policy: Policy) -> None:
    policy = workspace_policy
    allowed = build_task(
        1,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    )
    assert within_worker_limits(allowed, policy, False)
    for assessment in (
        build_assessment(Verdict.PROPOSE, Effort.XS, Risk.TESTS, [], []),
        build_assessment(Verdict.EXECUTE, Effort.M, Risk.TESTS, [], []),
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.INTERFACE, [], []),
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [Blocker.NEEDS_HUMAN], []),
    ):
        task = build_task(1, ISSUE_TITLE, TaskStatus.ASSESSED, ELIGIBLE, assessment)
        assert not within_worker_limits(task, policy, False)


def test_a_proposal_reaches_the_worker_once_the_operator_approves_it(
    workspace_policy: Policy,
) -> None:
    policy = workspace_policy
    proposed = build_task(
        1,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.PROPOSE, Effort.M, Risk.INTERFACE, [], []),
    )
    blocked = build_task(
        2,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.PROPOSE, Effort.M, Risk.INTERFACE, [Blocker.NEEDS_HUMAN], []),
    )

    assert not within_worker_limits(proposed, policy, False)
    assert refusal_reason(proposed, False) == PROPOSAL_REASON
    assert within_worker_limits(proposed, policy, True)
    assert not within_worker_limits(blocked, policy, True)


def test_an_issue_that_closed_since_triage_is_not_selectable(workspace_policy: Policy) -> None:
    policy = workspace_policy
    task = build_task(
        1,
        ISSUE_TITLE,
        TaskStatus.ASSESSED,
        ELIGIBLE,
        build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], []),
    )
    allowed, reason = selectable(task, None, policy, read_inbox(Path("state/absent-inbox")))
    assert not allowed
    assert "no longer open" in reason


def workbench_of(policy: Policy) -> Path:
    return policy.workspace.workbench_path("demo")


def approved_delivery() -> dict[str, object]:
    return delivery_payload("done", "fix(records): treat an empty speed as unknown", [])


def test_an_approved_issue_becomes_a_branch_a_reviewer_can_take(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    assert execute(policy_path) == 0
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    task = load_run_state(run_directory).tasks[0]
    assert task.status is TaskStatus.REVIEW
    assert task.branch == "weekend/1-empty-speed-field"
    assert task.gate is not None
    assert task.gate.passed
    assert task.cost_usd == 1.3
    identity = policy.identity
    assert task.branch is not None
    log = run_git(["log", "-1", "--format=%an <%ae>%n%s", task.branch], workbench_of(policy))
    assert f"{identity.git_author_name} <{identity.git_author_email}>" in log
    assert "fix(records): treat an empty speed as unknown" in log


def test_the_attempt_leaves_a_complete_record_and_a_ledger_line(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    execute(policy_path)
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    task_directory = run_directory.task_directory(1)
    for name in ("delivery.json", "gate.json", "diff.patch", "TASK.md", "acceptance.json"):
        assert (task_directory / name).is_file(), name
    assert "strip()" in (task_directory / "diff.patch").read_text()
    assert "weekend/1-empty-speed-field" in (task_directory / "TASK.md").read_text()
    assert json.loads((task_directory / "acceptance.json").read_text())["exit_code"] == 0
    ledger = json.loads((policy.state_dir / "ledger.jsonl").read_text().splitlines()[0])
    assert ledger["issue_number"] == 1
    assert ledger["outcome"] == "review"
    assert ledger["actual_usd"] == 1.3
    events = [
        json.loads(line)["event"] for line in run_directory.events_path.read_text().splitlines()
    ]
    assert events == [
        "baseline_finished",
        "task_started",
        "worker_finished",
        "gate_finished",
        "acceptance_finished",
        "task_finished",
    ]


def test_what_setup_builds_on_the_task_branch_reaches_the_gate(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        3,
        [1],
    )
    build_on_setup(tmp_path, fake_binaries, policy_path)
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert [task.status for task in state.tasks] == [TaskStatus.REVIEW]
    gate = state.tasks[0].gate
    assert gate is not None and GENERATED_MARKER not in gate.changed_paths


def worker_call_count(fake_binaries: Path) -> int:
    log = fake_binaries / "claude-calls.jsonl"
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
    return sum(1 for call in calls if "acceptEdits" in call)


def scope_calls(fake_binaries: Path) -> list[list[str]]:
    calls = (fake_binaries / "systemd-run-calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in calls]


def test_a_base_branch_that_fails_its_own_gate_stops_the_run_before_any_worker(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        3,
        [1],
    )
    amend_demo_repo(policy_path, {"gate_commands": [FIXED_ON_DISK_CHECK]})
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.stop_reason is StopReason.BASELINE_FAILED
    assert state.baseline is not None and not state.baseline.passed
    assert [task.status for task in state.tasks] == [TaskStatus.ASSESSED]
    assert worker_call_count(fake_binaries) == 0


def test_every_command_of_a_task_runs_in_a_scope_sized_for_its_step(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        3,
        [1],
    )
    amend_demo_repo(policy_path, {"setup_commands": ["true"]})
    assert execute(policy_path) == 0
    scopes = {
        next(argument for argument in call if argument.startswith("--unit=")): call
        for call in scope_calls(fake_binaries)
        if any(argument.startswith("--unit=wl-") for argument in call)
    }
    steps = {unit.split("-")[-2]: call for unit, call in scopes.items()}
    resources = policy.resources
    assert f"MemoryMax={int(resources.task_memory_gb * 1024**3)}" in steps["worker"]
    assert "OOMPolicy=continue" in steps["worker"]
    assert f"MemoryMax={int(resources.gate_memory_gb * 1024**3)}" in steps["gate"]
    assert "OOMPolicy=kill" in steps["gate"]
    assert {"setup", "worker", "gate", "acceptance"} <= set(steps)


def test_a_forbidden_path_fails_the_gate_and_the_branch_is_not_offered(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {".github/workflows/evil.yml": "on: push\n"},
        approved_delivery(),
        3,
        [1],
    )
    assert execute(policy_path) == 0
    task = load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks[0]
    assert task.status is TaskStatus.ABANDONED
    assert task.gate is not None
    assert task.gate.forbidden_paths_touched == [".github/workflows/evil.yml"]


def test_a_worker_that_asks_instead_of_guessing_leaves_the_tree_clean(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:approved"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("needs_input", "", ["Which unit does the feed use?"]),
        3,
        [1],
    )
    assert execute(policy_path) == 0
    task = load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks[0]
    assert task.status is TaskStatus.NEEDS_INPUT
    assert task.gate is None
    assert task.delivery is not None
    assert task.delivery.questions == ["Which unit does the feed use?"]
    assert run_git(["status", "--porcelain"], workbench_of(policy)).strip() == ""
    assert run_git(["log", "-1", "--format=%s"], workbench_of(policy)).strip() == "initial"


def test_an_unlabelled_issue_is_left_alone(tmp_path: Path, fake_binaries: Path) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["bug"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    assert execute(policy_path) == 0
    task = load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks[0]
    assert task.status is TaskStatus.ASSESSED
    assert task.branch is None
    calls = (fake_binaries / "claude-calls.jsonl").read_text()
    assert "acceptEdits" not in calls
    events = (open_run_directory(policy.state_dir, RUN_ID)).events_path.read_text()
    assert "consent is needs_approval" in events


def test_the_task_budget_of_the_reviewer_caps_the_run(tmp_path: Path, fake_binaries: Path) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        1,
        [1, 2],
    )
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    worked = [task for task in state.tasks if task.branch is not None]
    assert len(worked) == 1
    assert "1 approved tasks left for next time" in state.notes


def test_the_panel_can_approve_work_the_labels_left_alone(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["bug"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    approve_issue(open_run_directory(policy.state_dir, RUN_ID).inbox, 1, "panel")
    assert execute(policy_path) == 0
    task = load_run_state(open_run_directory(policy.state_dir, RUN_ID)).tasks[0]
    assert task.status is TaskStatus.REVIEW


def test_the_panel_can_take_pre_consented_work_off_the_queue(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    skip_issue(open_run_directory(policy.state_dir, RUN_ID).inbox, 1, "panel")
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].branch is None
    assert (
        "the reviewer skipped it"
        in open_run_directory(policy.state_dir, RUN_ID).events_path.read_text()
    )


def test_a_stop_request_ends_the_phase_before_the_next_task(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    request_stop(open_run_directory(policy.state_dir, RUN_ID).inbox, "the reviewer said so")
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].branch is None
    assert "execution stopped early: stop requested" in state.notes
    assert "acceptEdits" not in (fake_binaries / "claude-calls.jsonl").read_text()


def test_an_answer_written_before_the_run_reaches_the_worker(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    record_answer(policy.state_dir, "demo", 1, "Which unit does the feed use?", "Knots.")
    append_note(policy.state_dir, "demo", 1, "Reuse the helper in records.py.")

    assert execute(policy_path) == 0

    task_file = open_run_directory(policy.state_dir, RUN_ID).task_directory(1) / "TASK.md"
    body = task_file.read_text()
    assert "Which unit does the feed use? — Knots." in body
    assert "Standing note: Reuse the helper in records.py." in body


def test_an_answer_from_the_panel_reaches_the_next_attempt(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    answer_question(
        open_run_directory(policy.state_dir, RUN_ID).inbox,
        1,
        0,
        "Which unit does the feed use?",
        "Knots.",
    )
    assert execute(policy_path) == 0
    task_file = open_run_directory(policy.state_dir, RUN_ID).task_directory(1) / "TASK.md"
    assert "Which unit does the feed use? — Knots." in task_file.read_text()


def test_a_spent_weekly_allowance_refuses_the_run_before_the_first_task(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    write_probe_plan(
        fake_binaries,
        usage_event(
            0.10,
            0.99,
            datetime.now(UTC) + timedelta(hours=2),
            datetime.now(UTC) + timedelta(days=2),
        ),
        0.001,
    )
    assert execute(policy_path) == EXIT_BLOCKED
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].branch is None
    assert "acceptEdits" not in (fake_binaries / "claude-calls.jsonl").read_text()


def test_a_short_window_that_resets_after_the_deadline_ends_the_phase(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    seeded = load_run_state(run_directory)
    deadline = datetime.now(UTC) + timedelta(hours=1)
    save_run_state(run_directory, seeded.model_copy(update={"deadline_at": deadline}))
    write_probe_plan(
        fake_binaries,
        usage_event(
            0.99,
            0.10,
            datetime.now(UTC) + timedelta(hours=9),
            datetime.now(UTC) + timedelta(days=2),
        ),
        0.001,
    )
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].branch is None
    assert state.stop_reason is StopReason.FIVE_HOUR_LIMIT
    assert any("five-hour" in note for note in state.notes)
    assert "acceptEdits" not in (fake_binaries / "claude-calls.jsonl").read_text()


def test_a_short_window_about_to_reset_is_waited_out_and_the_task_still_runs(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        approved_delivery(),
        3,
        [1],
    )
    write_probe_sequence(fake_binaries, window_closing_soon(), 0.001)
    assert execute(policy_path) == 0
    state = load_run_state(open_run_directory(policy.state_dir, RUN_ID))
    assert state.tasks[0].branch is not None
    events = (open_run_directory(policy.state_dir, RUN_ID)).events_path.read_text()
    assert "usage_parked" in events


def test_a_dependency_the_assessor_names_is_no_blocker_of_its_own(workspace_policy: Policy) -> None:
    waiting = build_assessment(
        Verdict.EXECUTE, Effort.XS, Risk.TESTS, [Blocker.DEPENDS_ON_OPEN_ISSUE], []
    )
    unnamed = build_task(2, ISSUE_TITLE, TaskStatus.ASSESSED, ELIGIBLE, waiting)
    named = unnamed.model_copy(
        update={"assessment": waiting.model_copy(update={"depends_on": [1]})}
    )
    assert not within_worker_limits(unnamed, workspace_policy, False)
    assert within_worker_limits(named, workspace_policy, False)


def test_an_issue_that_builds_on_another_of_the_run_goes_after_it_one_task_at_a_time(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy, policy_path = prepare(
        tmp_path,
        fake_binaries,
        ["weekend:auto"],
        {"logbook/records.py": FIXED_RECORDS},
        delivery_payload("done", "fix(records): treat an empty speed as unknown", []),
        3,
        [1, 2],
    )
    run_directory = open_run_directory(policy.state_dir, RUN_ID)
    state = load_run_state(run_directory)
    parent, child = state.tasks
    assert child.assessment is not None
    built_on = child.model_copy(
        update={"assessment": child.assessment.model_copy(update={"depends_on": [1]})}
    )
    save_run_state(run_directory, state.model_copy(update={"tasks": [built_on, parent]}))
    delivery = delivery_payload("done", "docs: name the speed field", [])
    readme = worker_plan({"README.md": "# Logbook\n"}, [worker_result(delivery, 1.2, "done")], 0)
    fix = worker_plan(
        {"logbook/records.py": FIXED_RECORDS}, [worker_result(delivery, 1.2, "done")], 0
    )
    (fake_binaries / "claude-worker.json").write_text(
        json.dumps({"1": fix, "2": readme, "default": fix})
    )
    assert execute(policy_path) == 0
    finished = load_run_state(run_directory)
    statuses = {task.issue_number: task.status for task in finished.tasks}
    assert statuses == {1: TaskStatus.REVIEW, 2: TaskStatus.REVIEW}
    started = [
        json.loads(line)["issue_number"]
        for line in run_directory.events_path.read_text().splitlines()
        if json.loads(line)["event"] == "task_started"
    ]
    assert started == [1, 2]
    assert {task.issue_number: task.stacked_on for task in finished.tasks}[2] == 1
