from __future__ import annotations

from datetime import UTC, datetime

from weekend_loop.backends import reader_for, writer_for
from weekend_loop.briefing import write_prepared
from weekend_loop.github import signed
from weekend_loop.models import (
    Policy,
    PreparedSession,
    RepoMode,
    RepoTarget,
    RunKind,
    RunPhase,
    RunState,
    Task,
)
from weekend_loop.notify import render_question_alert, send_alert
from weekend_loop.publish import ask_on_issue
from weekend_loop.questions import PREPARE_LEAD, render_question_comment
from weekend_loop.runs import RunDirectory, create_run_directory, new_run_id
from weekend_loop.schedule import preparation_deadline
from weekend_loop.supervision import PULSE_SECONDS, RunSupervisor
from weekend_loop.triage import finish_triage, triage


def questions_of(task: Task) -> list[str]:
    return task.assessment.questions if task.assessment is not None else []


def question_count(state: RunState) -> int:
    return sum(len(questions_of(task)) for task in state.tasks)


def prepare_comment(task: Task, run_id: str, footer: str) -> str:
    return signed(render_question_comment(PREPARE_LEAD, questions_of(task)), footer, run_id)


def ask_on_issues(
    policy: Policy, repo: RepoTarget, run_directory: RunDirectory, state: RunState
) -> None:
    if repo.mode is not RepoMode.EXECUTE:
        return
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    for task in state.tasks:
        if not questions_of(task):
            continue
        ask_on_issue(
            writer,
            policy,
            task.issue_number,
            prepare_comment(task, state.run_id, policy.identity.comment_footer),
            run_directory,
        )


def run_prepare(
    policy: Policy, repo: RepoTarget, repo_key: str, limit: int, now: datetime
) -> tuple[RunState, RunDirectory]:
    run_id = new_run_id(policy.state_dir, repo_key, now)
    run_directory = create_run_directory(policy.state_dir, run_id)
    state = finish_triage(
        run_directory,
        triage(
            policy,
            repo,
            repo_key,
            reader_for(repo, policy.state_dir),
            run_directory,
            run_id,
            now,
            limit,
            RunSupervisor(run_directory, PULSE_SECONDS),
            RunKind.PREPARE,
            preparation_deadline(policy.schedule, now),
        ),
        repo,
        RunPhase.FINISHED,
    )
    ask_on_issues(policy, repo, run_directory, state)
    write_prepared(
        policy.state_dir,
        PreparedSession(
            run_id=run_id,
            repo_key=repo_key,
            # stamped after the agent's own comments and labels, which move each issue's updated_at
            prepared_at=datetime.now(UTC),
            question_count=question_count(state),
        ),
    )
    send_alert(policy.workspace.alert_webhook_path, render_question_alert(state))
    return state, run_directory
