from __future__ import annotations

from weekend_loop.assessor import load_assessor_prompts, render_worker_limits
from weekend_loop.models import Effort, Policy, Risk


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
