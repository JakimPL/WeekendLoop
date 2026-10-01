from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.backends import BoardReader, BoardWriter, repository_token
from weekend_loop.dependencies import stack_chains
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
MERGE_CARE_SUFFIX: Final[str] = " [merge care]"
MERGE_CARE_TEMPLATE: Final[str] = (
    "This branch and #{issue_number} both changed {paths}, and git cannot merge them on its own. "
    "Merge one, then resolve the other against it and run the tests."
)
CLEAN_OVERLAP_TEMPLATE: Final[str] = (
    "This branch and #{issue_number} both changed {paths}; git merges the two cleanly. "
    "Run the tests after merging the second."
)
STACKED_TEMPLATE: Final[str] = (
    "This branch builds on #{parent} ({parent_pull_request}), so its changes start where that "
    "pull request ends. Merge #{parent} first. When GitHub shows the two as a stack, merging the "
    "lower one moves this one onto `{base}`. Otherwise merge #{parent} with a merge commit and "
    "delete its branch, and GitHub moves this pull request onto `{base}`."
)
STACK_LINKED_TEMPLATE: Final[str] = "{chain} form a stack on GitHub"
STACK_UNLINKED_TEMPLATE: Final[str] = "{chain} stay separate pull requests: {reason}"
PULL_REQUEST_NUMBER_SEPARATOR: Final[str] = "/pull/"
UNFINISHED_BANNER: Final[str] = (
    "> **Unfinished — do not merge.** The worker stopped before it finished this issue. "
    "The changes passed the gate and are offered for inspection only."
)
REVIEW_LEAD: Final[str] = "A draft pull request is ready for review: {url}"
UNFINISHED_LEAD: Final[str] = (
    "The worker stopped before it finished; its unfinished draft is here for inspection, "
    "not for merging: {url}"
)


def stacked_section(task: Task, parent_pull_request: str | None, base_branch: str) -> list[str]:
    if task.stacked_on is None:
        return []
    text = STACKED_TEMPLATE.format(
        parent=task.stacked_on,
        parent_pull_request=parent_pull_request or "its pull request",
        base=base_branch,
    )
    return ["", f"## Stacked on #{task.stacked_on}", "", text]


def overlap_sections(task: Task) -> list[str]:
    care = [overlap for overlap in task.overlaps if not overlap.merges_cleanly]
    clean = [overlap for overlap in task.overlaps if overlap.merges_cleanly]
    lines: list[str] = []
    for title, overlaps, template in (
        ("## Merge with care", care, MERGE_CARE_TEMPLATE),
        ("## Shares files with", clean, CLEAN_OVERLAP_TEMPLATE),
    ):
        if overlaps:
            lines.extend(["", title, ""])
            lines.extend(
                template.format(issue_number=overlap.issue_number, paths=", ".join(overlap.paths))
                for overlap in overlaps
            )
    return lines


def pull_request_body(
    task: Task, run_id: str, footer: str, base_branch: str, parent_pull_request: str | None
) -> str:
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
    lines.extend(stacked_section(task, parent_pull_request, base_branch))
    lines.extend(overlap_sections(task))
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
        title = UNFINISHED_TITLE_TEMPLATE.format(title=title)
    needs_care = any(not overlap.merges_cleanly for overlap in task.overlaps)
    return f"{title}{MERGE_CARE_SUFFIX}" if needs_care else title


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
    repo: RepoTarget,
    parent_pull_request: str | None,
) -> str:
    writer.push_branch(branch, policy.worker.branch_prefix, workbench, git_settings)
    append_event(run_directory, EventType.BRANCH_PUSHED, branch, task.issue_number)
    pull_request_url = writer.open_draft_pull_request(
        branch,
        pull_request_title(task),
        pull_request_body(
            task, run_id, policy.identity.comment_footer, repo.base_branch, parent_pull_request
        ),
        task.base_branch or repo.base_branch,
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
    repo: RepoTarget,
    parent_pull_request: str | None,
) -> Task:
    branch = task.branch
    if branch is None:
        raise ValueError(f"issue #{task.issue_number} has no branch to publish")
    pull_request_url = existing_pull_requests.get(branch)
    if pull_request_url is None:
        pull_request_url = open_pull_request(
            writer,
            policy,
            task,
            branch,
            run_directory,
            run_id,
            workbench,
            git_settings,
            repo,
            parent_pull_request,
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
    for task in publish_order(state.tasks):
        if task.published_at is not None:
            continue
        if publishable(task):
            parent = progress.task(task.stacked_on) if task.stacked_on is not None else None
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
                repo,
                parent.pull_request_url if parent is not None else None,
            )
        elif task.status in QUESTION_STATUSES:
            published = publish_question(writer, reader, policy, task, run_directory, state.run_id)
        else:
            continue
        progress.put_task(published.model_copy(update={"published_at": datetime.now(UTC)}))
        progress.save()
    link_stacks(writer, progress, set(existing.values()))
    return post_digest(writer, reader, policy, repo, progress.state, run_directory, limit)


def stack_depth(task: Task, tasks: dict[int, Task]) -> int:
    depth = 0
    current = task
    while current.stacked_on is not None and current.stacked_on in tasks and depth < len(tasks):
        current = tasks[current.stacked_on]
        depth += 1
    return depth


def publish_order(tasks: list[Task]) -> list[Task]:
    by_number = {task.issue_number: task for task in tasks}
    return sorted(tasks, key=lambda task: (stack_depth(task, by_number), task.issue_number))


def pull_request_number(url: str | None) -> int | None:
    if url is None or PULL_REQUEST_NUMBER_SEPARATOR not in url:
        return None
    tail = url.rsplit(PULL_REQUEST_NUMBER_SEPARATOR, 1)[1]
    return int(tail) if tail.isdigit() else None


def link_stacks(writer: BoardWriter, progress: RunProgress, earlier: set[str]) -> None:
    parents = {
        task.issue_number: task.stacked_on
        for task in progress.state.tasks
        if task.stacked_on is not None
    }
    for chain in stack_chains(parents):
        urls = [progress.task(number).pull_request_url for number in chain]
        numbers = [pull_request_number(url) for url in urls]
        opened = [number for number in numbers if number is not None]
        if len(opened) != len(chain) or all(url in earlier for url in urls):
            continue
        named = " → ".join(f"#{number}" for number in chain)
        try:
            writer.link_stack(opened)
        except subprocess.CalledProcessError as error:
            reason = (error.stderr or "").strip().splitlines()
            detail = STACK_UNLINKED_TEMPLATE.format(
                chain=named, reason=reason[-1] if reason else "no answer"
            )
            append_event(progress.run_directory, EventType.STACK_LINKED, detail, chain[0])
            continue
        detail = STACK_LINKED_TEMPLATE.format(chain=named)
        append_event(progress.run_directory, EventType.STACK_LINKED, detail, chain[0])


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
