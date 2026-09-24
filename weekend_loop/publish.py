from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.backends import BoardReader, BoardWriter, repository_token
from weekend_loop.github import signed
from weekend_loop.models import (
    EventType,
    Issue,
    IssueComment,
    Policy,
    RepoMode,
    RepoTarget,
    RunPhase,
    RunState,
    Task,
    TaskStatus,
)
from weekend_loop.questions import (
    STOPPED_LEAD,
    is_agent_comment,
    is_question_comment,
    render_question_comment,
)
from weekend_loop.report import PUBLISHED_STATUSES, QUESTION_STATUSES, digest_title, render_digest
from weekend_loop.runs import RunDirectory, RunProgress, append_event
from weekend_loop.workbench import git_environment, write_askpass_script

DIGEST_NOTE_TEMPLATE: Final[str] = "digest posted at {url}"
PULL_REQUEST_TITLE_FALLBACK: Final[str] = "weekend: {title}"
UNFINISHED_TITLE_TEMPLATE: Final[str] = "[unfinished] {title}"
UNFINISHED_BANNER: Final[str] = (
    "> **Unfinished — do not merge.** The worker stopped before it finished this issue. "
    "The changes passed the gate and are offered for inspection only."
)
REVIEW_LEAD: Final[str] = "A draft pull request is ready for review: {url}"
UNFINISHED_LEAD: Final[str] = (
    "The worker stopped before it finished; its unfinished draft is here for inspection, "
    "not for merging: {url}"
)


def pull_request_body(task: Task, run_id: str, footer: str) -> str:
    delivery = task.delivery
    gate = task.gate
    banner = [UNFINISHED_BANNER, ""] if task.status is TaskStatus.UNFINISHED else []
    lines = [
        *banner,
        delivery.summary if delivery is not None else "",
        "",
        f"Refs #{task.issue_number}",
        "",
        "## Verification",
        "",
        delivery.verification if delivery is not None else "",
        f"Gate: {'passed' if gate is not None and gate.passed else 'not run'}, "
        f"{gate.diff_lines if gate is not None else 0} changed lines.",
    ]
    if delivery is not None and delivery.judgement_calls:
        lines.extend(["", "## Judgement calls", ""])
        lines.extend(f"- {call}" for call in delivery.judgement_calls)
    if delivery is not None and delivery.questions:
        lines.extend(["", "## Open questions", ""])
        lines.extend(f"- {question}" for question in delivery.questions)
    return signed("\n".join(lines), footer, run_id)


def review_comment(task: Task, pull_request_url: str, run_id: str, footer: str) -> str:
    delivery = task.delivery
    summary = delivery.summary if delivery is not None else ""
    lead = UNFINISHED_LEAD if task.status is TaskStatus.UNFINISHED else REVIEW_LEAD
    return signed(f"{lead.format(url=pull_request_url)}\n\n{summary}", footer, run_id)


def question_comment(task: Task, run_id: str, footer: str) -> str:
    delivery = task.delivery
    questions = delivery.questions if delivery is not None else []
    return signed(render_question_comment(STOPPED_LEAD, questions), footer, run_id)


def pull_request_title(task: Task) -> str:
    delivery = task.delivery
    subject = delivery.commit_subject.strip() if delivery is not None else ""
    title = subject if subject else PULL_REQUEST_TITLE_FALLBACK.format(title=task.title)
    if task.status is TaskStatus.UNFINISHED:
        return UNFINISHED_TITLE_TEMPLATE.format(title=title)
    return title


def delivery_label(task: Task, policy: Policy) -> str:
    if task.status is TaskStatus.UNFINISHED:
        return policy.labels.unfinished
    return policy.labels.review


def publishable(task: Task) -> bool:
    return (
        task.status in PUBLISHED_STATUSES
        and task.branch is not None
        and task.gate is not None
        and task.gate.passed
    )


def footer_line(policy: Policy, run_id: str) -> str:
    return policy.identity.comment_footer.format(run_id=run_id)


def announced(comments: list[IssueComment], pull_request_url: str) -> bool:
    return any(
        is_agent_comment(comment.body) and pull_request_url in comment.body for comment in comments
    )


def asked(comments: list[IssueComment], footer: str) -> bool:
    return any(is_question_comment(comment.body) and footer in comment.body for comment in comments)


def open_pull_request(
    writer: BoardWriter,
    policy: Policy,
    task: Task,
    branch: str,
    run_directory: RunDirectory,
    run_id: str,
    workbench: Path,
    git_settings: dict[str, str],
) -> str:
    writer.push_branch(branch, policy.worker.branch_prefix, workbench, git_settings)
    append_event(run_directory, EventType.BRANCH_PUSHED, branch, task.issue_number)
    pull_request_url = writer.open_draft_pull_request(
        branch,
        pull_request_title(task),
        pull_request_body(task, run_id, policy.identity.comment_footer),
    )
    append_event(run_directory, EventType.PULL_REQUEST_OPENED, pull_request_url, task.issue_number)
    return pull_request_url


def publish_delivery(
    writer: BoardWriter,
    reader: BoardReader,
    policy: Policy,
    task: Task,
    run_directory: RunDirectory,
    run_id: str,
    workbench: Path,
    git_settings: dict[str, str],
    existing_pull_requests: dict[str, str],
) -> Task:
    branch = task.branch
    if branch is None:
        raise ValueError(f"issue #{task.issue_number} has no branch to publish")
    pull_request_url = existing_pull_requests.get(branch)
    if pull_request_url is None:
        pull_request_url = open_pull_request(
            writer, policy, task, branch, run_directory, run_id, workbench, git_settings
        )
    if not announced(reader.issue_comments(task.issue_number), pull_request_url):
        writer.comment_on_issue(
            task.issue_number,
            review_comment(task, pull_request_url, run_id, policy.identity.comment_footer),
        )
        append_event(run_directory, EventType.COMMENT_POSTED, "review", task.issue_number)
    label = delivery_label(task, policy)
    writer.add_labels(task.issue_number, [label])
    append_event(run_directory, EventType.LABEL_WRITTEN, label, task.issue_number)
    writer.remove_labels(task.issue_number, [policy.labels.needs_input])
    append_event(
        run_directory, EventType.LABEL_REMOVED, policy.labels.needs_input, task.issue_number
    )
    return task.model_copy(update={"pull_request_url": pull_request_url})


def ask_on_issue(
    writer: BoardWriter,
    policy: Policy,
    issue_number: int,
    body: str,
    run_directory: RunDirectory,
) -> None:
    writer.comment_on_issue(issue_number, body)
    append_event(run_directory, EventType.COMMENT_POSTED, "questions", issue_number)
    writer.add_labels(issue_number, [policy.labels.needs_input])
    append_event(run_directory, EventType.LABEL_WRITTEN, policy.labels.needs_input, issue_number)


def publish_question(
    writer: BoardWriter,
    reader: BoardReader,
    policy: Policy,
    task: Task,
    run_directory: RunDirectory,
    run_id: str,
) -> Task:
    if asked(reader.issue_comments(task.issue_number), footer_line(policy, run_id)):
        writer.add_labels(task.issue_number, [policy.labels.needs_input])
        return task
    ask_on_issue(
        writer,
        policy,
        task.issue_number,
        question_comment(task, run_id, policy.identity.comment_footer),
        run_directory,
    )
    return task


def publish_run(
    policy: Policy,
    repo: RepoTarget,
    repo_key: str,
    reader: BoardReader,
    writer: BoardWriter,
    run_directory: RunDirectory,
    state: RunState,
    limit: int,
) -> RunState:
    if repo.mode is not RepoMode.EXECUTE:
        raise ValueError(f"{repo_key} is a dry-run repository; publishing stays off")
    progress = RunProgress(run_directory, state)
    progress.enter_phase(RunPhase.PUBLISH)
    progress.save()
    workbench = policy.workspace.workbench_path(repo_key)
    git_settings = git_environment(repository_token(repo), write_askpass_script(policy.state_dir))
    existing = {
        pull_request.head_branch: pull_request.url
        for pull_request in reader.open_pull_requests(limit)
    }
    for task in state.tasks:
        if task.published_at is not None:
            continue
        if publishable(task):
            published = publish_delivery(
                writer,
                reader,
                policy,
                task,
                run_directory,
                state.run_id,
                workbench,
                git_settings,
                existing,
            )
        elif task.status in QUESTION_STATUSES:
            published = publish_question(writer, reader, policy, task, run_directory, state.run_id)
        else:
            continue
        progress.put_task(published.model_copy(update={"published_at": datetime.now(UTC)}))
        progress.save()
    return post_digest(writer, reader, policy, repo, progress.state, run_directory, limit)


def existing_digest(issues: list[Issue], title: str, footer: str) -> str | None:
    for issue in issues:
        if issue.title == title and footer in issue.body:
            return issue.url
    return None


def post_digest(
    writer: BoardWriter,
    reader: BoardReader,
    policy: Policy,
    repo: RepoTarget,
    state: RunState,
    run_directory: RunDirectory,
    limit: int,
) -> RunState:
    body = render_digest(state, repo.slug)
    run_directory.digest_path.write_text(body)
    title = digest_title(state)
    url = existing_digest(reader.open_issues(limit), title, footer_line(policy, state.run_id))
    if url is None:
        url = writer.create_issue(title, signed(body, policy.identity.comment_footer, state.run_id))
        append_event(run_directory, EventType.DIGEST_POSTED, url, None)
    return state.model_copy(
        update={
            "phase": RunPhase.FINISHED,
            "notes": [*state.notes, DIGEST_NOTE_TEMPLATE.format(url=url)],
        }
    )
