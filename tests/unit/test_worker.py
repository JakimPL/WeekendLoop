from __future__ import annotations

from pathlib import Path

from tests.unit.conftest import build_assessment, delivery_payload
from tests.unit.test_prefilter import build_issue
from weekend_loop.claude_cli import build_command
from weekend_loop.models import (
    Blocker,
    ClaudeOutcome,
    ClaudeResult,
    Confidence,
    DeliveryStatus,
    Effort,
    Policy,
    RepoTarget,
    Risk,
    Verdict,
)
from weekend_loop.policy import repo_target
from weekend_loop.resources import PromptName, prompt_text
from weekend_loop.worker import (
    NO_SHARED_PATHS_TEXT,
    NO_WAVE_PATHS_TEXT,
    RESUME_PROMPT,
    TaskGuidance,
    WorkerCall,
    abandoned_delivery,
    delivery_from_result,
    load_worker_prompts,
    render_task,
    worker_invocation,
)


def pilot_of(policy: Policy) -> RepoTarget:
    return repo_target(policy, "demo")


def build_result(structured_output: dict[str, object] | None) -> ClaudeResult:
    return ClaudeResult(
        outcome=ClaudeOutcome.OK,
        exit_code=0,
        cost_usd=1.5,
        duration_seconds=60.0,
        session_id="session",
        text="done",
        structured_output=structured_output,
        permission_denials=[],
        error_message=None,
        usage=None,
        rejection=None,
    )


def test_the_task_states_the_branch_the_limit_and_the_untrusted_issue(
    workspace_policy: Policy,
) -> None:
    issue = build_issue(3, "Empty speed field", "Fix the parser please.", [], [], [], None)
    assessment = build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], [])
    task = render_task(
        prompt_text(PromptName.TASK_TEMPLATE, None),
        issue,
        assessment,
        pilot_of(workspace_policy),
        "weekend/3-empty",
        400,
        TaskGuidance(answers=[], wave_paths=[]),
        [],
    )
    assert "# Task: issue #3 — Empty speed field" in task
    assert "Branch: weekend/3-empty (based on main)" in task
    assert "Diff limit: 400 changed lines" in task
    assert assessment.plan.strip() in task
    assert '<untrusted_issue number="3"' in task
    assert "Fix the parser please." in task


def test_answers_from_the_reviewer_reach_the_task(workspace_policy: Policy) -> None:
    issue = build_issue(3, "Empty speed field", "body", [], [], [], None)
    assessment = build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], [])
    task = render_task(
        prompt_text(PromptName.TASK_TEMPLATE, None),
        issue,
        assessment,
        pilot_of(workspace_policy),
        "weekend/3-empty",
        400,
        TaskGuidance(answers=["Use knots, not metres per second."], wave_paths=[]),
        [],
    )
    assert "- Use knots, not metres per second." in task


def test_the_task_names_what_the_rest_of_the_wave_touches_and_the_shared_files(
    workspace_policy: Policy,
) -> None:
    issue = build_issue(3, "Empty speed field", "body", [], [], [], None)
    assessment = build_assessment(Verdict.EXECUTE, Effort.XS, Risk.TESTS, [], [])
    template = prompt_text(PromptName.TASK_TEMPLATE, None)
    repo = pilot_of(workspace_policy)
    in_company = render_task(
        template,
        issue,
        assessment,
        repo,
        "weekend/3-empty",
        400,
        TaskGuidance(answers=[], wave_paths=["src/other.py", "tests/test_other.py"]),
        ["CHANGELOG.md", "docs/generated/**"],
    )
    assert (
        "Other tasks worked in this wave touch: src/other.py, tests/test_other.py; stay off them."
        in in_company
    )
    assert "Shared files, appended to and never rewritten: CHANGELOG.md, docs/generated/**" in (
        in_company
    )
    alone = render_task(
        template,
        issue,
        assessment,
        repo,
        "weekend/3-empty",
        400,
        TaskGuidance(answers=[], wave_paths=[]),
        [],
    )
    assert f"touch: {NO_WAVE_PATHS_TEXT}; stay off them." in alone
    assert f"never rewritten: {NO_SHARED_PATHS_TEXT}" in alone


def test_the_worker_runs_under_the_sandboxed_fence_with_its_own_budget(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    prompts = load_worker_prompts(
        pilot_of(workspace_policy), workspace_policy.workspace.prompts_dir
    )
    call = WorkerCall(
        session_id="session-id",
        resume=False,
        budget_usd=workspace_policy.budget.per_task_usd,
        transcript=tmp_path / "worker-1.jsonl",
    )
    command = build_command(
        worker_invocation(workspace_policy, prompts, "do the task", tmp_path, call, None)
    )
    pairs = list(zip(command, command[1:], strict=False))
    assert ("--permission-mode", "acceptEdits") in pairs
    assert ("--setting-sources", "user") in pairs
    assert ("--settings", str(workspace_policy.settings.worker)) in pairs
    assert ("--output-format", "stream-json") in pairs
    assert ("--max-budget-usd", str(workspace_policy.budget.per_task_usd)) in pairs
    assert ("--session-id", "session-id") in pairs
    assert ("--tools", "Read,Grep,Glob,Edit,Write,Bash") in pairs
    assert "--restricted" not in command
    assert "--no-session-persistence" not in command


def test_a_resumed_worker_names_its_session_and_carries_what_is_left_of_its_budget(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    prompts = load_worker_prompts(
        pilot_of(workspace_policy), workspace_policy.workspace.prompts_dir
    )
    call = WorkerCall(
        session_id="session-id", resume=True, budget_usd=2.5, transcript=tmp_path / "worker-2.jsonl"
    )
    command = build_command(
        worker_invocation(workspace_policy, prompts, RESUME_PROMPT, tmp_path, call, None)
    )
    pairs = list(zip(command, command[1:], strict=False))
    assert ("--resume", "session-id") in pairs
    assert "--session-id" not in command
    assert ("--max-budget-usd", "2.5") in pairs


def test_the_conventions_of_the_repository_ride_along_in_the_system_prompt(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    conventions = tmp_path / "conventions.md"
    conventions.write_text("Gate: pytest tests\nThe repository speaks for itself here.\n")
    repo = pilot_of(workspace_policy).model_copy(update={"conventions_prompt": conventions})
    prompts = load_worker_prompts(repo, workspace_policy.workspace.prompts_dir)
    assert "The repository speaks for itself here." in prompts.system
    assert "Git belongs to the orchestrator" in prompts.system


def test_the_writing_guide_sits_between_the_worker_rules_and_the_conventions(
    tmp_path: Path, workspace_policy: Policy
) -> None:
    conventions = tmp_path / "conventions.md"
    conventions.write_text("Gate: pytest tests\nThe repository speaks for itself here.\n")
    repo = pilot_of(workspace_policy).model_copy(update={"conventions_prompt": conventions})
    system = load_worker_prompts(repo, workspace_policy.workspace.prompts_dir).system
    rules = system.index("Git belongs to the orchestrator")
    guide = system.index("# Writing about work")
    repository = system.index("The repository speaks for itself here.")
    assert rules < guide < repository


def test_a_valid_delivery_is_read_back_from_the_structured_output() -> None:
    payload = delivery_payload("done", "fix(records): treat an empty speed as unknown", [])
    delivery = delivery_from_result(build_result(payload))
    assert delivery is not None
    assert delivery.status is DeliveryStatus.DONE
    assert delivery.commit_subject.startswith("fix(records)")


def test_an_unusable_answer_leaves_no_delivery() -> None:
    assert delivery_from_result(build_result(None)) is None
    assert delivery_from_result(build_result({"status": "done"})) is None


def test_the_abandoned_delivery_names_the_reason_and_claims_nothing() -> None:
    delivery = abandoned_delivery(ClaudeOutcome.TIMEOUT)
    assert delivery.status is DeliveryStatus.ABANDONED
    assert "timeout" in delivery.summary
    assert delivery.files_changed == []
    assert delivery.confidence is Confidence.LOW
    assert Blocker.ASSESSOR_FAILED.value not in delivery.summary
