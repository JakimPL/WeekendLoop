from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from weekend_loop.briefing import RepoBriefing, render_briefing_block
from weekend_loop.claude_cli import ClaudeInvocation, run_claude
from weekend_loop.models import (
    ActivityKind,
    Assessment,
    AssessmentOutcome,
    Blocker,
    ClaudeOutcome,
    ClaudeResult,
    Confidence,
    Effort,
    Issue,
    Policy,
    Record,
    RepoTarget,
    Risk,
    SpecSignals,
    Verdict,
)
from weekend_loop.resources import PromptName, SchemaName, prompt_text, schema_text
from weekend_loop.supervision import RunSupervisor

ASSESSOR_PERMISSION_MODE: Final[str] = "plan"
ASSESSOR_TOOLS: Final[list[str]] = ["Read", "Grep", "Glob"]
ASSESSOR_OUTPUT_FORMAT: Final[str] = "stream-json"
ASSESSMENT_TRANSCRIPT_ROLE: Final[str] = "assessment"
SECONDS_PER_MINUTE: Final[int] = 60
FAILED_PLAN_TEMPLATE: Final[str] = "The assessor returned no verdict; the run ended as {outcome}."


class AssessorPrompts(Record):
    system: str
    task_template: str
    json_schema: str


def load_assessor_prompts(overrides: Path) -> AssessorPrompts:
    return AssessorPrompts(
        system=prompt_text(PromptName.ASSESSOR_SYSTEM, overrides),
        task_template=prompt_text(PromptName.ASSESSOR_TASK, overrides),
        json_schema=schema_text(SchemaName.ASSESSMENT),
    )


def render_spec_signals(signals: SpecSignals) -> str:
    missing = [path for path in signals.referenced_paths if path not in signals.resolved_paths]
    lines = [
        f"- body length: {signals.body_length} characters",
        f"- template sections present: {', '.join(signals.sections_present) or 'none'}",
        f"- acceptance criteria stated: {'yes' if signals.has_acceptance_criteria else 'no'}",
        f"- referenced paths that exist: {', '.join(signals.resolved_paths) or 'none'}",
        f"- referenced paths not found: {', '.join(missing) or 'none'}",
    ]
    return "\n".join(lines)


def render_assessor_prompt(
    template: str, issue: Issue, signals: SpecSignals, repo_slug: str, briefing_block: str
) -> str:
    return template.format(
        issue_number=issue.number,
        repo_slug=repo_slug,
        spec_signals=render_spec_signals(signals),
        briefing=briefing_block,
        issue_title=issue.title,
        issue_body=issue.body.strip(),
    )


def assessor_invocation(
    policy: Policy, prompts: AssessorPrompts, prompt: str, workbench: Path, transcript: Path
) -> ClaudeInvocation:
    return ClaudeInvocation(
        prompt=prompt,
        system_prompt=prompts.system,
        model=policy.models.assessor,
        effort=policy.models.assessor_effort,
        tools=ASSESSOR_TOOLS,
        permission_mode=ASSESSOR_PERMISSION_MODE,
        restricted=True,
        setting_sources=None,
        settings_file=policy.settings.assessor,
        json_schema=prompts.json_schema,
        output_format=ASSESSOR_OUTPUT_FORMAT,
        max_budget_usd=policy.budget.assessor_usd,
        timeout_seconds=policy.worker.assessor_timeout_minutes * SECONDS_PER_MINUTE,
        working_directory=workbench,
        session_id=None,
        resume=False,
        persist_session=False,
        transcript_path=transcript,
        idle_seconds=policy.worker.idle_minutes * SECONDS_PER_MINUTE,
    )


def failed_assessment(outcome: ClaudeOutcome) -> Assessment:
    return Assessment(
        verdict=Verdict.SKIP,
        effort=Effort.L,
        risk=Risk.INTERFACE,
        blockers=[Blocker.ASSESSOR_FAILED],
        plan=FAILED_PLAN_TEMPLATE.format(outcome=outcome.value),
        touched_paths=[],
        questions=[],
        confidence=Confidence.LOW,
    )


def assessment_from_result(result: ClaudeResult) -> Assessment | None:
    if result.outcome is not ClaudeOutcome.OK or result.structured_output is None:
        return None
    try:
        return Assessment.model_validate(result.structured_output)
    except ValidationError:
        return None


class AssessorContext(Record):
    repo: RepoTarget
    prompts: AssessorPrompts
    workbench: Path
    environment: dict[str, str]
    briefing: RepoBriefing


def assess_issue(
    policy: Policy,
    context: AssessorContext,
    issue: Issue,
    signals: SpecSignals,
    supervisor: RunSupervisor,
) -> AssessmentOutcome:
    prompt = render_assessor_prompt(
        context.prompts.task_template,
        issue,
        signals,
        context.repo.slug,
        render_briefing_block(context.briefing, issue.number),
    )
    transcript = supervisor.transcript_for(ASSESSMENT_TRANSCRIPT_ROLE, issue.number)
    supervisor.enter(ActivityKind.ASSESSING, issue.number, transcript, None)
    result = run_claude(
        assessor_invocation(policy, context.prompts, prompt, context.workbench, transcript),
        context.environment,
        supervisor,
    )
    supervisor.leave(issue.number)
    assessment = assessment_from_result(result)
    return AssessmentOutcome(
        issue_number=issue.number,
        assessment=assessment if assessment is not None else failed_assessment(result.outcome),
        outcome=result.outcome,
        cost_usd=result.cost_usd,
        session_id=result.session_id,
        rejection=result.rejection,
    )


def affordable(policy: Policy, spent_usd: float) -> bool:
    return spent_usd + policy.budget.assessor_usd <= policy.budget.envelope_usd
