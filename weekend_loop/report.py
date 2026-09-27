from __future__ import annotations

from typing import Final

from weekend_loop.limits import render_usage
from weekend_loop.models import (
    Confidence,
    Effort,
    RunState,
    SoloReason,
    Task,
    TaskStatus,
    Verdict,
)

VERDICT_ORDER: Final[dict[Verdict, int]] = {
    Verdict.EXECUTE: 0,
    Verdict.PROPOSE: 1,
    Verdict.NEEDS_INPUT: 2,
    Verdict.SKIP: 3,
}
EFFORT_ORDER: Final[dict[Effort, int]] = {
    Effort.XS: 0,
    Effort.S: 1,
    Effort.M: 2,
    Effort.L: 3,
}
CONFIDENCE_ORDER: Final[dict[Confidence, int]] = {
    Confidence.HIGH: 0,
    Confidence.MEDIUM: 1,
    Confidence.LOW: 2,
}
UNASSESSED_RANK: Final[int] = len(VERDICT_ORDER)
DETAILED_VERDICTS: Final[tuple[Verdict, ...]] = (Verdict.EXECUTE, Verdict.PROPOSE)


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").strip()


def ranking_key(task: Task) -> tuple[int, int, int, int]:
    if task.assessment is None:
        return (UNASSESSED_RANK, 0, 0, task.issue_number)
    assessment = task.assessment
    return (
        VERDICT_ORDER[assessment.verdict],
        EFFORT_ORDER[assessment.effort],
        CONFIDENCE_ORDER[assessment.confidence],
        task.issue_number,
    )


def assessed_tasks(state: RunState) -> list[Task]:
    return sorted((task for task in state.tasks if task.assessment is not None), key=ranking_key)


def filtered_tasks(state: RunState) -> list[Task]:
    return [
        task
        for task in state.tasks
        if task.assessment is None
        and task.eligibility is not None
        and not task.eligibility.eligible
    ]


def render_candidate_table(tasks: list[Task]) -> str:
    header = "| Issue | Verdict | Effort | Risk | Confidence | Blockers | Title |"
    separator = "|---|---|---|---|---|---|---|"
    rows = []
    for task in tasks:
        assessment = task.assessment
        if assessment is None:
            continue
        blockers = ", ".join(blocker.value for blocker in assessment.blockers) or "—"
        rows.append(
            f"| #{task.issue_number} | {assessment.verdict.value} | {assessment.effort.value} "
            f"| {assessment.risk.value} | {assessment.confidence.value} | {blockers} "
            f"| {escape_cell(task.title)} |"
        )
    return "\n".join([header, separator, *rows])


def render_filtered_table(tasks: list[Task]) -> str:
    header = "| Issue | Reasons | Title |"
    separator = "|---|---|---|"
    rows = []
    for task in tasks:
        if task.eligibility is None:
            continue
        reasons = ", ".join(reason.value for reason in task.eligibility.reasons)
        rows.append(f"| #{task.issue_number} | {reasons} | {escape_cell(task.title)} |")
    return "\n".join([header, separator, *rows])


def render_plans(tasks: list[Task]) -> str:
    sections: list[str] = []
    for task in tasks:
        assessment = task.assessment
        if assessment is None or assessment.verdict not in DETAILED_VERDICTS:
            continue
        paths = ", ".join(assessment.touched_paths) or "none named"
        sections.append(
            f"### #{task.issue_number} — {task.title} ({assessment.verdict.value})\n\n"
            f"{assessment.plan.strip()}\n\nTouched paths: {paths}"
        )
    return "\n\n".join(sections) if sections else "Nothing reached execute or propose."


def render_questions(tasks: list[Task]) -> str:
    sections: list[str] = []
    for task in tasks:
        assessment = task.assessment
        if assessment is None or not assessment.questions:
            continue
        numbered = "\n".join(
            f"{index}. {question}" for index, question in enumerate(assessment.questions, start=1)
        )
        sections.append(f"### #{task.issue_number} — {task.title}\n\n{numbered}")
    return "\n\n".join(sections) if sections else "No questions."


def render_triage_plan(state: RunState, repo_slug: str) -> str:
    candidates = assessed_tasks(state)
    filtered = filtered_tasks(state)
    header = [
        f"# Triage plan — {repo_slug}",
        "",
        f"- run: `{state.run_id}`",
        f"- mode: {state.mode.value}",
        f"- assessed: {len(candidates)}; filtered out before assessment: {len(filtered)}",
        f"- spend: ${state.spent_usd:.2f} of ${state.envelope_usd:.2f}",
        f"- allowance: {render_usage(state.usage)}",
    ]
    if state.notes:
        header.extend(["", *[f"- note: {note}" for note in state.notes]])
    return "\n".join(
        [
            *header,
            "",
            "## Ranked candidates",
            "",
            render_candidate_table(candidates),
            "",
            "## What it would do",
            "",
            render_plans(candidates),
            "",
            "## Questions it would ask",
            "",
            render_questions(candidates),
            "",
            "## Filtered out before assessment",
            "",
            render_filtered_table(filtered),
            "",
        ]
    )


DIGEST_TITLE_TEMPLATE: Final[str] = "Weekend run {date}"
DIGEST_DATE_FORMAT: Final[str] = "%Y-%m-%d"
REVIEW_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.REVIEW,)
UNFINISHED_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.UNFINISHED,)
PUBLISHED_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.REVIEW, TaskStatus.UNFINISHED)
QUESTION_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.NEEDS_INPUT,)
LEFT_STATUSES: Final[tuple[TaskStatus, ...]] = (TaskStatus.ABANDONED, TaskStatus.ASSESSED)
SOLO_REASON_TEXT: Final[dict[SoloReason, str]] = {
    SoloReason.SHARED_PATH: "alone: it touches a shared path",
    SoloReason.NO_TOUCHED_PATHS: "alone: its assessment names no paths",
}


def digest_title(state: RunState) -> str:
    return DIGEST_TITLE_TEMPLATE.format(date=state.started_at.strftime(DIGEST_DATE_FORMAT))


def tasks_with_status(state: RunState, statuses: tuple[TaskStatus, ...]) -> list[Task]:
    return [task for task in state.tasks if task.status in statuses]


def render_review_section(tasks: list[Task]) -> str:
    if not tasks:
        return "Nothing reached the reviewer this time."
    lines = ["| Issue | Pull request | Lines | Cost | Summary |", "|---|---|---|---|---|"]
    for task in tasks:
        delivery = task.delivery
        summary = escape_cell(delivery.summary) if delivery is not None else ""
        lines.append(
            f"| #{task.issue_number} | {task.pull_request_url or task.branch or '—'} "
            f"| {task.gate.diff_lines if task.gate is not None else 0} "
            f"| ${task.cost_usd:.2f} | {summary} |"
        )
    return "\n".join(lines)


def render_unfinished_section(tasks: list[Task]) -> str:
    if not tasks:
        return "No unfinished work was offered."
    lines = []
    for task in tasks:
        landing = task.pull_request_url or task.branch or "—"
        reason = task.delivery.summary if task.delivery is not None else ""
        lines.append(f"- #{task.issue_number} {task.title}: {landing} — {reason}")
    return "\n".join(lines)


def render_question_section(tasks: list[Task]) -> str:
    if not tasks:
        return "No question is open."
    sections = []
    for task in tasks:
        delivery = task.delivery
        questions = delivery.questions if delivery is not None else []
        numbered = "\n".join(
            f"{index}. {question}" for index, question in enumerate(questions, start=1)
        )
        sections.append(f"**#{task.issue_number} — {task.title}**\n\n{numbered}")
    return "\n\n".join(sections)


def render_left_section(tasks: list[Task]) -> str:
    if not tasks:
        return "Nothing was left behind."
    lines = []
    for task in tasks:
        reason = task.delivery.summary if task.delivery is not None else "not taken up"
        lines.append(f"- #{task.issue_number} {task.title}: {task.status.value} — {reason}")
    return "\n".join(lines)


def waves_of_run(state: RunState) -> dict[int, list[Task]]:
    grouped: dict[int, list[Task]] = {}
    for task in state.tasks:
        if task.wave is not None:
            grouped.setdefault(task.wave, []).append(task)
    return dict(sorted(grouped.items()))


def render_wave_line(number: int, tasks: list[Task]) -> str:
    members = ", ".join(f"#{task.issue_number}" for task in tasks)
    reasons = [SOLO_REASON_TEXT[task.solo_reason] for task in tasks if task.solo_reason is not None]
    suffix = f" ({reasons[0]})" if reasons else ""
    return f"- wave {number}: {members}{suffix}"


def render_overlap_lines(state: RunState) -> list[str]:
    return [
        f"- #{task.issue_number} and #{overlap.issue_number} both changed "
        f"{', '.join(overlap.paths)}: merge them one at a time and run the tests after each"
        for task in state.tasks
        for overlap in task.overlaps
        if task.issue_number < overlap.issue_number
    ]


def render_merge_care_section(state: RunState) -> list[str]:
    lines = render_overlap_lines(state)
    if not lines:
        return []
    return ["## Merge with care", "", *lines, ""]


def render_waves_section(state: RunState) -> list[str]:
    waves = waves_of_run(state)
    if not waves:
        return []
    lines = [render_wave_line(number, tasks) for number, tasks in waves.items()]
    return ["## Waves", "", *lines, ""]


def render_digest(state: RunState, repo_slug: str) -> str:
    reviewable = tasks_with_status(state, REVIEW_STATUSES)
    unfinished = tasks_with_status(state, UNFINISHED_STATUSES)
    questions = tasks_with_status(state, QUESTION_STATUSES)
    left = tasks_with_status(state, LEFT_STATUSES)
    return "\n".join(
        [
            f"# {digest_title(state)} — {repo_slug}",
            "",
            f"- run: `{state.run_id}`",
            f"- phase at the end: {state.phase.value}",
            f"- spend: ${state.spent_usd:.2f} of ${state.envelope_usd:.2f}",
            f"- allowance: {render_usage(state.usage)}",
            f"- delivered: {len(reviewable)}; unfinished: {len(unfinished)}; "
            f"awaiting an answer: {len(questions)}; left: {len(left)}",
            *[f"- note: {note}" for note in state.notes],
            "",
            "## Ready for review",
            "",
            render_review_section(reviewable),
            "",
            "## Unfinished — do not merge",
            "",
            render_unfinished_section(unfinished),
            "",
            "## Waiting on an answer",
            "",
            render_question_section(questions),
            "",
            "## Left for next time",
            "",
            render_left_section(left),
            "",
            *render_merge_care_section(state),
            *render_waves_section(state),
        ]
    )
