from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final

from weekend_loop.demo.board import commit_playground
from weekend_loop.demo.seed import (
    GITHUB_REPO_KEY,
    SeedOutcome,
    SeedRunner,
    acceptance_map,
    issues_directory,
    load_seed_issues,
    seed_environment,
    seed_repository,
    write_acceptance_map,
)
from weekend_loop.github import read_token
from weekend_loop.models import Backend, CheckOutcome, Policy, PreflightReport, RepoTarget
from weekend_loop.policy import repo_target
from weekend_loop.preflight import probe_reading, run_preflight
from weekend_loop.workbench import clone_url, git_environment, run_git, write_askpass_script

OPERATOR_ACTION_EXIT_CODE: Final[int] = 2
STAGING_DIRECTORY_NAME: Final[str] = "playground"
HEADS_PREFIX: Final[str] = "refs/heads/"
WORKFLOW_REJECTION_MARKERS: Final[tuple[str, ...]] = ("refusing to allow", "workflow")
PERMISSION_REFUSAL_MARKERS: Final[tuple[str, ...]] = ("http 403", "resource not accessible")
REFUSAL_DETAIL_CHARACTERS: Final[int] = 300
TOKEN_INSTRUCTIONS: Final[str] = (
    "Create a fine-grained GitHub token that covers {slug} with read and write access\n"
    "to Contents, Issues, Pull requests and Workflows. Save it as a single line in {token_file}\n"
    "and run `chmod 600 {token_file}`."
)
REPOSITORY_INSTRUCTIONS: Final[str] = (
    "GitHub refused access to {slug}. Create it as an empty private repository (no README,\n"
    "license or .gitignore) and make sure the token in {token_file} covers it\n"
    "with read and write access to Contents."
)
WORKFLOW_INSTRUCTIONS: Final[str] = (
    "GitHub refused the files under .github/workflows from the token in {token_file}.\n"
    "Add Workflows (read and write) to the token's repository permissions on github.com:\n"
    "Settings > Developer settings > Fine-grained tokens."
)
FOREIGN_BRANCHES_INSTRUCTIONS: Final[str] = (
    "{slug} already has branches ({branches}) and no {base_branch}.\n"
    "Recreate it as an empty repository so the mockup starts from a clean history."
)
PERMISSION_INSTRUCTIONS: Final[str] = (
    "GitHub refused a request from the token in {token_file}:\n"
    "  {detail}\n"
    "Give the token read and write access to Issues and Pull requests on {slug}."
)
PREFLIGHT_INSTRUCTIONS: Final[str] = "Preflight found what still needs you:\n{blockers}"
READY_NOTE: Final[str] = (
    "Ready. Start the run by hand:\n"
    "  uv run weekend-loop prepare --repo-key {repo_key}\n"
    "  uv run weekend-loop weekend --repo-key {repo_key}"
)
RERUN_NOTE: Final[str] = "Run this script again once that is done; finished steps are skipped."
CONTINUE_PROMPT: Final[str] = "Press Enter once that is done, or Ctrl+C to stop. "


class OperatorActionRequiredError(Exception):
    pass


@dataclass(frozen=True)
class SetupContext:
    policy: Policy
    repo_key: str
    repo: RepoTarget
    remote_url: str
    examples: Path


@dataclass(frozen=True)
class SetupStep:
    name: str
    run: Callable[[SetupContext], str]


def instructions(template: str, context: SetupContext) -> str:
    return template.format(slug=context.repo.slug, token_file=context.repo.token_file)


def token_environment(context: SetupContext) -> dict[str, str]:
    try:
        token = read_token(context.repo.token_path())
    except (FileNotFoundError, ValueError) as error:
        raise OperatorActionRequiredError(instructions(TOKEN_INSTRUCTIONS, context)) from error
    return git_environment(token, write_askpass_script(context.policy.state_dir))


def branch_names(ls_remote_output: str) -> list[str]:
    return [
        line.split("\t")[1].removeprefix(HEADS_PREFIX)
        for line in ls_remote_output.splitlines()
        if line.strip()
    ]


def remote_branches(context: SetupContext, environment: dict[str, str]) -> list[str]:
    try:
        output = run_git(
            ["ls-remote", "--heads", context.remote_url], context.policy.state_dir, environment
        )
    except subprocess.CalledProcessError as error:
        raise OperatorActionRequiredError(instructions(REPOSITORY_INSTRUCTIONS, context)) from error
    return branch_names(output)


def is_workflow_rejection(stderr: str) -> bool:
    lowered = stderr.lower()
    return all(marker in lowered for marker in WORKFLOW_REJECTION_MARKERS)


def push_playground(context: SetupContext, environment: dict[str, str]) -> None:
    base_reference = f"{HEADS_PREFIX}{context.repo.base_branch}"
    with TemporaryDirectory() as temporary:
        staging = Path(temporary) / STAGING_DIRECTORY_NAME
        commit_playground(context.examples, staging, context.repo.base_branch)
        try:
            run_git(
                ["push", context.remote_url, f"{base_reference}:{base_reference}"],
                staging,
                environment,
            )
        except subprocess.CalledProcessError as error:
            if is_workflow_rejection(error.stderr):
                raise OperatorActionRequiredError(
                    instructions(WORKFLOW_INSTRUCTIONS, context)
                ) from error
            raise


def publish_playground(context: SetupContext) -> str:
    environment = token_environment(context)
    branches = remote_branches(context, environment)
    base_branch = context.repo.base_branch
    if base_branch in branches:
        return f"{context.repo.slug} already carries {base_branch}"
    if branches:
        raise OperatorActionRequiredError(
            FOREIGN_BRANCHES_INSTRUCTIONS.format(
                slug=context.repo.slug, branches=", ".join(branches), base_branch=base_branch
            )
        )
    push_playground(context, environment)
    return f"pushed the Pocketchat mockup to {context.repo.slug} on {base_branch}"


def gh_environment(context: SetupContext) -> dict[str, str]:
    try:
        return seed_environment(context.repo, context.policy.state_dir)
    except (FileNotFoundError, ValueError) as error:
        raise OperatorActionRequiredError(instructions(TOKEN_INSTRUCTIONS, context)) from error


def is_permission_refusal(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in PERMISSION_REFUSAL_MARKERS)


def seed_summary(outcome: SeedOutcome, tests: dict[str, str]) -> str:
    created = len(outcome.created_issues)
    total = len(outcome.issue_numbers)
    issues = f"created {created} of {total} issues" if created else f"all {total} issues were there"
    pull_requests = f"opened {len(outcome.opened_pull_requests)} colleague draft pull request(s)"
    tested = ", ".join(f"#{number}" for number in sorted(tests, key=int))
    return f"{issues}, {pull_requests}, hidden tests for {tested}"


def seed_issues(context: SetupContext) -> str:
    runner = SeedRunner(dry_run=False, environment=gh_environment(context))
    issues = load_seed_issues(issues_directory(context.examples))
    try:
        outcome = seed_repository(runner, context.repo, context.policy.labels, issues)
    except subprocess.CalledProcessError as error:
        if is_permission_refusal(error.stderr):
            detail = error.stderr.strip()[-REFUSAL_DETAIL_CHARACTERS:]
            raise OperatorActionRequiredError(
                PERMISSION_INSTRUCTIONS.format(
                    token_file=context.repo.token_file, detail=detail, slug=context.repo.slug
                )
            ) from error
        raise
    tests = acceptance_map(issues, outcome.issue_numbers)
    write_acceptance_map(context.policy.state_dir, tests)
    return seed_summary(outcome, tests)


def listed_checks(report: PreflightReport, required: bool) -> list[str]:
    return [
        f"  - {check.name}: {check.detail}"
        for check in report.checks
        if check.outcome is CheckOutcome.FAILED and check.required is required
    ]


def readiness(report: PreflightReport) -> str:
    blockers = listed_checks(report, required=True)
    if blockers:
        raise OperatorActionRequiredError(
            PREFLIGHT_INSTRUCTIONS.format(blockers="\n".join(blockers))
        )
    warnings = listed_checks(report, required=False)
    summary = f"clear to run, {len(report.checks)} checks"
    return "\n".join([summary, *warnings]) if warnings else summary


def check_readiness(context: SetupContext) -> str:
    reading = probe_reading(context.policy)
    return readiness(run_preflight(context.policy, context.repo_key, datetime.now(UTC), reading))


SETUP_STEPS: Final[tuple[SetupStep, ...]] = (
    SetupStep("publish the Pocketchat mockup", publish_playground),
    SetupStep("seed the labels, issues and the colleague's pull request", seed_issues),
    SetupStep("run the preflight checks", check_readiness),
)


def run_step(step: SetupStep, context: SetupContext, interactive: bool) -> bool:
    while True:
        try:
            print(f"done     {step.name}: {step.run(context)}")
            return True
        except OperatorActionRequiredError as request:
            print(f"waiting  {step.name}\n\n{request}\n")
            if not interactive:
                return False
            input(CONTINUE_PROMPT)


def run_steps(steps: tuple[SetupStep, ...], context: SetupContext, interactive: bool) -> int:
    for step in steps:
        if not run_step(step, context, interactive):
            print(RERUN_NOTE)
            return OPERATOR_ACTION_EXIT_CODE
    print(READY_NOTE.format(repo_key=context.repo_key))
    return 0


def github_target(policy: Policy, repo_key: str) -> RepoTarget:
    repo = repo_target(policy, repo_key)
    if repo.backend is not Backend.GITHUB:
        raise ValueError(
            f"repository {repo_key!r} uses the {repo.backend.value} backend; "
            f"the setup prepares a GitHub one such as {GITHUB_REPO_KEY!r}"
        )
    return repo


def publish_demo(examples: Path, policy: Policy, repo_key: str, interactive: bool) -> int:
    repo = github_target(policy, repo_key)
    context = SetupContext(
        policy=policy,
        repo_key=repo_key,
        repo=repo,
        remote_url=clone_url(repo, policy.state_dir),
        examples=examples,
    )
    return run_steps(SETUP_STEPS, context, interactive)
