from __future__ import annotations

from pathlib import Path
from typing import Final

from pydantic import ValidationError

from weekend_loop.claude_cli import CallWatch, ClaudeInvocation, run_claude
from weekend_loop.confinement import Confinement
from weekend_loop.models import (
    Assessment,
    ClaudeOutcome,
    ClaudeResult,
    Confidence,
    Delivery,
    DeliveryStatus,
    Issue,
    Policy,
    Record,
    RepoTarget,
    WorkerOutcome,
)
from weekend_loop.resources import PromptName, SchemaName, prompt_text, read_resource, schema_text

WORKER_TOOLS: Final[list[str]] = ["Read", "Grep", "Glob", "Edit", "Write", "Bash"]
WORKER_PERMISSION_MODE: Final[str] = "acceptEdits"
WORKER_OUTPUT_FORMAT: Final[str] = "stream-json"
WORKER_SETTING_SOURCES: Final[str] = "user"
SECONDS_PER_MINUTE: Final[int] = 60
DELIVERY_FALLBACK: Final[str] = (
    ", and print that same JSON object as your final message if the structured answer fails"
)
NO_ANSWERS_TEXT: Final[str] = "None yet; nobody is available during the run."
NO_WAVE_PATHS_TEXT: Final[str] = "none; this task runs alone"
NO_SHARED_PATHS_TEXT: Final[str] = "none named"
ABANDONED_SUMMARY_TEMPLATE: Final[str] = "No delivery arrived; the run ended as {outcome}."
RESUME_PROMPT: Final[str] = (
    "Your previous turn was interrupted before it finished. Check what is already on disk in "
    "the checkout, carry on from there, and finish with the JSON delivery object."
)


class WorkerCall(Record):
    session_id: str
    resume: bool
    budget_usd: float
    transcript: Path


class WorkerPrompts(Record):
    system: str
    task_template: str
    json_schema: str


class TaskGuidance(Record):
    answers: list[str]
    wave_paths: list[str]


def conventions_text(repo: RepoTarget, overrides: Path) -> str:
    if repo.conventions_prompt is None:
        return prompt_text(PromptName.CONVENTIONS_DEFAULT, overrides)
    return read_resource(repo.conventions_prompt)


def load_worker_prompts(repo: RepoTarget, overrides: Path) -> WorkerPrompts:
    return WorkerPrompts(
        system="\n\n".join(
            [
                prompt_text(PromptName.WORKER_SYSTEM, overrides),
                prompt_text(PromptName.WRITING_GUIDE, overrides),
                conventions_text(repo, overrides),
            ]
        ),
        task_template=prompt_text(PromptName.TASK_TEMPLATE, overrides),
        json_schema=schema_text(SchemaName.DELIVERY),
    )


def listed(paths: list[str], empty_text: str) -> str:
    return ", ".join(paths) if paths else empty_text


def render_task(
    template: str,
    issue: Issue,
    assessment: Assessment,
    repo: RepoTarget,
    branch: str,
    max_diff_lines: int,
    guidance: TaskGuidance,
    shared_paths: list[str],
) -> str:
    answers = guidance.answers
    return template.format(
        issue_number=issue.number,
        issue_title=issue.title,
        repo_slug=repo.slug,
        branch=branch,
        base_branch=repo.base_branch,
        max_diff_lines=max_diff_lines,
        wave_paths=listed(guidance.wave_paths, NO_WAVE_PATHS_TEXT),
        shared_paths=listed(shared_paths, NO_SHARED_PATHS_TEXT),
        delivery_fallback=DELIVERY_FALLBACK,
        plan=assessment.plan.strip(),
        answers="\n".join(f"- {answer}" for answer in answers) if answers else NO_ANSWERS_TEXT,
        issue_body=issue.body.strip(),
    )


def worker_invocation(
    policy: Policy,
    prompts: WorkerPrompts,
    prompt: str,
    workbench: Path,
    call: WorkerCall,
    confinement: Confinement | None,
) -> ClaudeInvocation:
    return ClaudeInvocation(
        prompt=prompt,
        system_prompt=prompts.system,
        model=policy.models.worker,
        effort=policy.models.worker_effort,
        tools=WORKER_TOOLS,
        permission_mode=WORKER_PERMISSION_MODE,
        restricted=False,
        setting_sources=WORKER_SETTING_SOURCES,
        settings_file=policy.settings.worker,
        json_schema=prompts.json_schema,
        output_format=WORKER_OUTPUT_FORMAT,
        max_budget_usd=call.budget_usd,
        timeout_seconds=policy.worker.timeout_minutes * SECONDS_PER_MINUTE,
        working_directory=workbench,
        session_id=call.session_id,
        resume=call.resume,
        persist_session=True,
        transcript_path=call.transcript,
        idle_seconds=policy.worker.idle_minutes * SECONDS_PER_MINUTE,
        confinement=confinement,
    )


def abandoned_delivery(outcome: ClaudeOutcome) -> Delivery:
    return Delivery(
        status=DeliveryStatus.ABANDONED,
        commit_subject="",
        summary=ABANDONED_SUMMARY_TEMPLATE.format(outcome=outcome.value),
        verification="none",
        judgement_calls=[],
        questions=[],
        files_changed=[],
        confidence=Confidence.LOW,
    )


def delivery_from_result(result: ClaudeResult) -> Delivery | None:
    if result.structured_output is None:
        return None
    try:
        return Delivery.model_validate(result.structured_output)
    except ValidationError:
        return None


def run_worker(
    policy: Policy,
    prompts: WorkerPrompts,
    prompt: str,
    workbench: Path,
    environment: dict[str, str],
    call: WorkerCall,
    watch: CallWatch,
    issue_number: int,
    confinement: Confinement | None,
) -> WorkerOutcome:
    result = run_claude(
        worker_invocation(
            policy, prompts, RESUME_PROMPT if call.resume else prompt, workbench, call, confinement
        ),
        environment,
        watch,
    )
    delivery = delivery_from_result(result)
    return WorkerOutcome(
        issue_number=issue_number,
        delivery=delivery if delivery is not None else abandoned_delivery(result.outcome),
        outcome=result.outcome,
        cost_usd=result.cost_usd,
        session_id=result.session_id if result.session_id is not None else call.session_id,
        permission_denials=result.permission_denials,
        rejection=result.rejection,
    )
