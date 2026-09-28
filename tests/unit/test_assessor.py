from __future__ import annotations

from weekend_loop.assessor import render_worker_limits
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
