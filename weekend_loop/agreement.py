from __future__ import annotations

import csv
from pathlib import Path
from typing import Final

from weekend_loop.models import (
    AgreementReport,
    BlindLabel,
    BlindLabelEntry,
    Blocker,
    Disagreement,
    Issue,
    RunState,
    Task,
    Verdict,
)

LABEL_FIELDS: Final[tuple[str, ...]] = ("issue_number", "label", "expected_blocker", "title")
EMPTY_CELL: Final[str] = ""


def write_label_template(path: Path, issues: list[Issue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as sheet:
        writer = csv.writer(sheet)
        writer.writerow(LABEL_FIELDS)
        for issue in issues:
            writer.writerow([issue.number, EMPTY_CELL, EMPTY_CELL, issue.title])


def load_blind_labels(path: Path) -> list[BlindLabelEntry]:
    entries: list[BlindLabelEntry] = []
    with path.open(newline="") as sheet:
        for row in csv.DictReader(sheet):
            label = (row.get("label") or "").strip()
            if not label:
                continue
            blocker = (row.get("expected_blocker") or "").strip()
            entries.append(
                BlindLabelEntry(
                    issue_number=int(row["issue_number"]),
                    label=BlindLabel(label),
                    expected_blocker=Blocker(blocker) if blocker else None,
                )
            )
    return entries


def agent_label(task: Task) -> BlindLabel | None:
    if task.eligibility is not None and not task.eligibility.eligible:
        return BlindLabel.NEVER
    if task.assessment is None:
        return None
    return BlindLabel.NEVER if task.assessment.verdict is Verdict.SKIP else BlindLabel.NOT_NEVER


def score_agreement(state: RunState, entries: list[BlindLabelEntry]) -> AgreementReport:
    tasks = {task.issue_number: task for task in state.tasks}
    compared = 0
    agreed = 0
    blocker_compared = 0
    blocker_matched = 0
    execute_on_never: list[int] = []
    disagreements: list[Disagreement] = []
    for entry in entries:
        task = tasks.get(entry.issue_number)
        if task is None:
            continue
        label = agent_label(task)
        if label is None:
            continue
        compared += 1
        verdict = task.assessment.verdict if task.assessment is not None else None
        if label is entry.label:
            agreed += 1
        else:
            disagreements.append(
                Disagreement(
                    issue_number=entry.issue_number,
                    user_label=entry.label,
                    agent_label=label,
                    verdict=verdict,
                )
            )
        if entry.label is BlindLabel.NEVER and verdict is Verdict.EXECUTE:
            execute_on_never.append(entry.issue_number)
        if entry.expected_blocker is not None and task.assessment is not None:
            blocker_compared += 1
            if entry.expected_blocker in task.assessment.blockers:
                blocker_matched += 1
    return AgreementReport(
        run_id=state.run_id,
        repo_key=state.repo_key,
        labelled_count=len(entries),
        compared_count=compared,
        agreed_count=agreed,
        agreement_rate=agreed / compared if compared else 0.0,
        execute_on_never=execute_on_never,
        blocker_compared=blocker_compared,
        blocker_matched=blocker_matched,
        disagreements=disagreements,
    )


def render_agreement(report: AgreementReport) -> str:
    lines = [
        f"agreement for run {report.run_id} ({report.repo_key})",
        f"  labelled: {report.labelled_count}; compared: {report.compared_count}",
        f"  never vs not-never agreement: {report.agreement_rate:.0%} "
        f"({report.agreed_count}/{report.compared_count})",
        f"  execute verdicts on never-labelled issues: {len(report.execute_on_never)}",
        f"  blockers matched: {report.blocker_matched}/{report.blocker_compared}",
    ]
    for disagreement in report.disagreements:
        verdict = disagreement.verdict.value if disagreement.verdict is not None else "ineligible"
        lines.append(
            f"    #{disagreement.issue_number}: operator {disagreement.user_label.value}, "
            f"agent {disagreement.agent_label.value} ({verdict})"
        )
    return "\n".join(lines)
