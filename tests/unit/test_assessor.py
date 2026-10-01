from __future__ import annotations

from pathlib import Path

from tests.unit.test_prefilter import build_issue
from weekend_loop.assessor import (
    load_assessor_prompts,
    render_assessor_prompt,
    render_batch_index,
    render_worker_limits,
)
from weekend_loop.models import Effort, Policy, Risk
from weekend_loop.prefilter import spec_signals


def test_the_assessor_is_told_the_operators_limits(workspace_policy: Policy) -> None:
    worker = workspace_policy.worker.model_copy(
        update={
            "allowed_effort": [Effort.XS, Effort.S, Effort.M],
            "allowed_risk": [Risk.DOCS, Risk.BEHAVIOUR],
            "max_diff_lines": 900,
        }
    )

    block = render_worker_limits(worker)

    assert "efforts the worker may take: XS, S, M" in block
    assert "risks the worker may take: docs, behaviour" in block
    assert "at most 900" in block


def test_the_assessor_writes_its_plans_and_questions_by_the_writing_guide(
    workspace_policy: Policy,
) -> None:
    system = load_assessor_prompts(workspace_policy.workspace.prompts_dir).system

    assert "You are the triage assessor" in system
    assert "## Describing a task" in system
    assert "## Asking a question" in system


def test_the_assessor_sees_the_other_issues_of_the_run_and_what_github_links(
    workspace_policy: Policy,
) -> None:
    current = build_issue(17, "Rules book", "## Business requirement\nNo book.\n", [], [], [], None)
    parent = build_issue(
        13, "Saved rules", "## Goal\nPlayers save a rule set. Then they reuse it.", [], [], [], None
    ).model_copy(update={"blocked_by": [11]})
    prompt = render_assessor_prompt(
        load_assessor_prompts(workspace_policy.workspace.prompts_dir).task_template,
        current,
        spec_signals(current.model_copy(update={"blocked_by": [13]}), Path("/absent")),
        "owner/repo",
        "none",
        "limits",
        render_batch_index([current, parent], current.number),
    )
    assert "- #13 Saved rules: Players save a rule set (blocked by #11)" in prompt
    assert "- #17 Rules book" not in prompt
    assert "blocked by, as linked on GitHub: #13" in prompt
    assert "<untrusted_issue_index>" in prompt
    assert (
        render_batch_index([current], current.number) == "none; this is the only issue in the run"
    )
