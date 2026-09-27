from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION: Final[int] = 2
MINUTES_PER_HOUR: Final[int] = 60
MINUTES_PER_DAY: Final[int] = 24 * MINUTES_PER_HOUR
MINUTES_PER_WEEK: Final[int] = 7 * MINUTES_PER_DAY
MAX_QUESTIONS_PER_ASSESSMENT: Final[int] = 3
DEFAULT_BASE_BRANCH: Final[str] = "main"
DEFAULT_BRANCH_PREFIX: Final[str] = "weekend/"
DEFAULT_IDLE_MINUTES: Final[int] = 15
DEFAULT_PARALLEL_WORKERS: Final[int] = 1
REPOSITORY_SLUG_PATTERN: Final[str] = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
CONFIG_FILENAME: Final[str] = "config.yaml"
REFERENCE_FILENAME: Final[str] = "config.reference.yaml"
SECRETS_DIRECTORY_NAME: Final[str] = "secrets"
PROMPTS_DIRECTORY_NAME: Final[str] = "prompts"
ACCEPTANCE_DIRECTORY_NAME: Final[str] = "acceptance"
AGENT_HOME_NAME: Final[str] = "agent-home"
WORK_DIRECTORY_NAME: Final[str] = "work"
WORKTREES_SUFFIX: Final[str] = "-worktrees"
STATE_DIRECTORY_NAME: Final[str] = "state"
CLAUDE_DIRECTORY_NAME: Final[str] = ".claude"
WORKER_FENCE_NAME: Final[str] = "settings.json"
ASSESSOR_FENCE_NAME: Final[str] = "assessor.settings.json"
OAUTH_TOKEN_NAME: Final[str] = "claude-oauth.token"
REPOSITORY_TOKEN_TEMPLATE: Final[str] = "github-{repo_key}.token"
ALERT_WEBHOOK_NAME: Final[str] = "alert-webhook.url"
DEFAULT_FORBIDDEN_PATHS: Final[tuple[str, ...]] = (".github/**", "**/.env", "**/.env.*")
DEFAULT_ENVELOPE_USD: Final[float] = 15.0
DEFAULT_PER_TASK_USD: Final[float] = 6.0
DEFAULT_ASSESSOR_USD: Final[float] = 0.5
DEFAULT_MAX_TASKS: Final[int] = 3
DEFAULT_SEVEN_DAY_CEILING: Final[float] = 0.95
DEFAULT_SEVEN_DAY_RESERVE: Final[float] = 0.08
DEFAULT_FIVE_HOUR_CEILING: Final[float] = 0.95
DEFAULT_PROBE_USD: Final[float] = 0.05
DEFAULT_PROBE_TIMEOUT_SECONDS: Final[int] = 120
DEFAULT_ASSESSOR_MODEL: Final[str] = "sonnet"
DEFAULT_ASSESSOR_EFFORT: Final[str] = "low"
DEFAULT_WORKER_MODEL: Final[str] = "opus"
DEFAULT_WORKER_EFFORT: Final[str] = "high"
DEFAULT_TIMEOUT_MINUTES: Final[int] = 45
DEFAULT_ASSESSOR_TIMEOUT_MINUTES: Final[int] = 8
DEFAULT_MAX_DIFF_LINES: Final[int] = 400
DEFAULT_MINIMUM_BODY_LENGTH: Final[int] = 50
DEFAULT_EXCLUDED_TITLE_PATTERN: Final[str] = "EPIC|AREA|SPIKE|Literature|experiments|analysis"
DEFAULT_FOREIGN_ACTIVITY_WINDOW_HOURS: Final[int] = 48
DEFAULT_GIT_AUTHOR_NAME: Final[str] = "Weekend Loop"
DEFAULT_COMMENT_FOOTER: Final[str] = "— weekend-loop run {run_id}"
DEFAULT_TIMEZONE: Final[str] = "UTC"

DEFAULT_LABEL_NAMESPACE: Final[str] = "weekend:"
LABEL_NAMESPACE_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._/-]*[:/-]$"
LABEL_SUFFIXES: Final[dict[str, str]] = {
    "auto": "auto",
    "approved": "approved",
    "never": "never",
    "review": "review",
    "needs_input": "needs-input",
    "unfinished": "unfinished",
}
DEFAULT_WINDOW_OPENS: Final[dict[str, Any]] = {"day_of_week": "fri", "hour": 18, "minute": 0}
DEFAULT_WINDOW_CLOSES: Final[dict[str, Any]] = {"day_of_week": "sun", "hour": 23, "minute": 59}
DEFAULT_SCHEDULED_RUNS: Final[tuple[dict[str, Any], ...]] = (
    {"day_of_week": "thu", "hour": 20, "minute": 0, "command": "prepare"},
    {"day_of_week": "fri", "hour": 21, "minute": 0, "command": "weekend"},
    {"day_of_week": "sat", "hour": 10, "minute": 0, "command": "weekend"},
)
ASSESSMENT_DESCRIPTION: Final[str] = (
    "The assessor's verdict on one eligible issue, produced without any write access."
)
DELIVERY_DESCRIPTION: Final[str] = (
    "The worker's account of one attempt; the orchestrator verifies every claim independently."
)


class Verdict(StrEnum):
    EXECUTE = "execute"
    PROPOSE = "propose"
    NEEDS_INPUT = "needs_input"
    SKIP = "skip"


class Effort(StrEnum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"


class Risk(StrEnum):
    DOCS = "docs"
    TESTS = "tests"
    REFACTOR = "refactor"
    BEHAVIOUR = "behaviour"
    INTERFACE = "interface"


class Blocker(StrEnum):
    EMPTY_BODY = "empty_body"
    UNCLEAR_GOAL = "unclear_goal"
    NO_ACCEPTANCE_CRITERIA = "no_acceptance_criteria"
    NEEDS_EXTERNAL_DATA = "needs_external_data"
    NEEDS_GPU = "needs_gpu"
    NEEDS_HUMAN = "needs_human"
    NEEDS_WEB = "needs_web"
    DEPENDS_ON_OPEN_ISSUE = "depends_on_open_issue"
    OVERLAPS_OPEN_PR = "overlaps_open_pr"
    TOO_LARGE = "too_large"
    TOUCHES_FORBIDDEN_PATHS = "touches_forbidden_paths"
    ASSESSOR_FAILED = "assessor_failed"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DeliveryStatus(StrEnum):
    DONE = "done"
    PARTIAL = "partial"
    ABANDONED = "abandoned"
    NEEDS_INPUT = "needs_input"


class TaskStatus(StrEnum):
    CANDIDATE = "candidate"
    INELIGIBLE = "ineligible"
    ASSESSED = "assessed"
    APPROVED = "approved"
    WORKING = "working"
    REVIEW = "review"
    NEEDS_INPUT = "needs_input"
    UNFINISHED = "unfinished"
    ABANDONED = "abandoned"
    SKIPPED = "skipped"


class SoloReason(StrEnum):
    SHARED_PATH = "shared_path"
    NO_TOUCHED_PATHS = "no_touched_paths"


class RunPhase(StrEnum):
    PREFLIGHT = "preflight"
    TRIAGE = "triage"
    EXECUTE = "execute"
    PARKED = "parked"
    PUBLISH = "publish"
    REPORT = "report"
    FINISHED = "finished"
    ABORTED = "aborted"


class Weekday(StrEnum):
    MONDAY = "mon"
    TUESDAY = "tue"
    WEDNESDAY = "wed"
    THURSDAY = "thu"
    FRIDAY = "fri"
    SATURDAY = "sat"
    SUNDAY = "sun"


WEEKDAYS: Final[tuple[Weekday, ...]] = tuple(Weekday)


class Backend(StrEnum):
    GITHUB = "github"
    LOCAL = "local"


class IssueState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class ScheduledCommand(StrEnum):
    PREPARE = "prepare"
    WEEKEND = "weekend"


DEFAULT_SCHEDULED_COMMAND: Final[ScheduledCommand] = ScheduledCommand.WEEKEND


class RepoMode(StrEnum):
    DRY_RUN = "dry_run"
    EXECUTE = "execute"


class RunKind(StrEnum):
    TRIAGE = "triage"
    PREPARE = "prepare"
    WEEKEND = "weekend"


class IneligibilityReason(StrEnum):
    ASSIGNED_TO_SOMEONE_ELSE = "assigned_to_someone_else"
    NEVER_LABEL = "never_label"
    BODY_TOO_SHORT = "body_too_short"
    TITLE_PATTERN = "title_pattern"
    OPEN_LINKED_PULL_REQUEST = "open_linked_pull_request"
    RECENT_FOREIGN_ACTIVITY = "recent_foreign_activity"


class ClaudeOutcome(StrEnum):
    OK = "ok"
    CANCELLED = "cancelled"
    BUDGET = "budget"
    TIMEOUT = "timeout"
    WEEKLY_LIMIT = "weekly_limit"
    WINDOW_LIMIT = "window_limit"
    KILLED = "killed"
    STALLED = "stalled"
    SESSION_MISSING = "session_missing"
    NO_OUTPUT = "no_output"
    FAILED = "failed"


class StopReason(StrEnum):
    OPERATOR = "operator"
    ALLOWANCE = "allowance"
    FIVE_HOUR_LIMIT = "five_hour_limit"
    WINDOW_CLOSED = "window_closed"
    ENVELOPE = "envelope"
    SETUP_FAILED = "setup_failed"


class EventType(StrEnum):
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    PREFILTER_FINISHED = "prefilter_finished"
    ASSESSMENT_STARTED = "assessment_started"
    ASSESSMENT_FINISHED = "assessment_finished"
    TASK_STARTED = "task_started"
    TASK_RESUMED = "task_resumed"
    TASK_SKIPPED = "task_skipped"
    WAVE_STARTED = "wave_started"
    WORKER_FINISHED = "worker_finished"
    GATE_FINISHED = "gate_finished"
    ACCEPTANCE_FINISHED = "acceptance_finished"
    TASK_FINISHED = "task_finished"
    BRANCH_PUSHED = "branch_pushed"
    PULL_REQUEST_OPENED = "pull_request_opened"
    COMMENT_POSTED = "comment_posted"
    ANSWER_RECEIVED = "answer_received"
    LABEL_WRITTEN = "label_written"
    LABEL_REMOVED = "label_removed"
    DIGEST_POSTED = "digest_posted"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LIMIT_REACHED = "limit_reached"
    USAGE_PARKED = "usage_parked"
    RUN_FINISHED = "run_finished"
    RUN_ABORTED = "run_aborted"


class Consent(StrEnum):
    AUTO = "auto"
    APPROVED = "approved"
    NEEDS_APPROVAL = "needs_approval"
    NEVER = "never"


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class BlindLabel(StrEnum):
    NEVER = "never"
    NOT_NEVER = "not-never"


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class StructuredOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConfigSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Assessment(StructuredOutput):
    model_config = ConfigDict(json_schema_extra={"description": ASSESSMENT_DESCRIPTION})

    verdict: Verdict
    effort: Effort
    risk: Risk
    blockers: list[Blocker]
    plan: str
    touched_paths: list[str]
    questions: list[str] = Field(max_length=MAX_QUESTIONS_PER_ASSESSMENT)
    confidence: Confidence


class Delivery(StructuredOutput):
    model_config = ConfigDict(json_schema_extra={"description": DELIVERY_DESCRIPTION})

    status: DeliveryStatus
    commit_subject: str
    summary: str
    verification: str
    judgement_calls: list[str]
    questions: list[str]
    files_changed: list[str]
    confidence: Confidence


class Issue(Record):
    number: int
    title: str
    body: str
    labels: list[str]
    assignees: list[str]
    milestone: str | None
    author: str
    created_at: datetime
    updated_at: datetime
    url: str
    open_linked_pull_requests: list[int]
    last_foreign_activity_at: datetime | None


class BoardLabel(Record):
    name: str
    description: str
    colour: str


class BoardComment(Record):
    author: str
    created_at: datetime
    body: str


class BoardIssue(Record):
    number: int
    title: str
    body: str
    labels: list[str]
    assignees: list[str]
    milestone: str | None
    author: str
    created_at: datetime
    updated_at: datetime
    state: IssueState


class BoardPullRequest(Record):
    number: int
    title: str
    body: str
    head_branch: str
    base_branch: str
    author: str
    draft: bool
    state: IssueState
    created_at: datetime


class BoardIndex(Record):
    viewer_login: str
    next_number: int
    labels: list[BoardLabel]


class SpecSignals(Record):
    body_length: int
    sections_present: list[str]
    has_template: bool
    referenced_paths: list[str]
    resolved_paths: list[str]
    has_acceptance_criteria: bool


class EligibilityDecision(Record):
    eligible: bool
    reasons: list[IneligibilityReason]


class CommandResult(Record):
    command: str
    exit_code: int
    duration_seconds: float
    output_tail: str


class GateResult(Record):
    passed: bool
    commands: list[CommandResult]
    diff_lines: int
    forbidden_paths_touched: list[str]
    secret_matches: list[str]
    binary_files: list[str]
    commit_count: int


class Task(Record):
    issue_number: int
    title: str
    status: TaskStatus
    eligibility: EligibilityDecision | None
    spec_signals: SpecSignals | None
    assessment: Assessment | None
    delivery: Delivery | None
    gate: GateResult | None
    branch: str | None
    pull_request_url: str | None
    session_id: str | None
    attempts: int
    cost_usd: float
    worker_cost_usd: float = 0.0
    resumes: int = 0
    published_at: datetime | None = None
    wave: int | None = None
    solo_reason: SoloReason | None = None


class UsageWindow(Record):
    utilization: float = Field(ge=0.0)
    resets_at: datetime


class UsageReading(Record):
    five_hour: UsageWindow
    seven_day: UsageWindow
    status: str
    is_using_overage: bool
    observed_at: datetime


class RunState(Record):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    repo_key: str
    mode: RepoMode
    phase: RunPhase
    started_at: datetime
    updated_at: datetime
    heartbeat_at: datetime
    envelope_usd: float
    spent_usd: float
    usage: UsageReading | None
    tasks: list[Task]
    notes: list[str]
    stop_reason: StopReason | None = None
    stop_detail: str | None = None
    kind: RunKind | None = None
    deadline_at: datetime | None = None
    repo_slug: str | None = None


class ActivityKind(StrEnum):
    PROBING = "probing"
    ASSESSING = "assessing"
    WORKING = "working"
    GATING = "gating"
    PARKED = "parked"
    PAUSED = "paused"
    PUBLISHING = "publishing"


class Activity(Record):
    kind: ActivityKind
    issue_number: int | None
    started_at: datetime
    transcript: Path | None
    resumes_at: datetime | None


class Pulse(Record):
    pid: int
    boot_id: str
    heartbeat_at: datetime
    activity: Activity | None
    activities: list[Activity] = Field(default_factory=list)


class LedgerEntry(Record):
    run_id: str
    repo_key: str
    issue_number: int
    estimate_usd: float
    actual_usd: float
    outcome: TaskStatus
    session_id: str | None
    finished_at: datetime


class Halt(Record):
    reason: StopReason
    detail: str


class LimitRejection(Record):
    rate_limit_type: str | None
    resets_at: datetime | None


class ClaudeResult(Record):
    outcome: ClaudeOutcome
    exit_code: int
    cost_usd: float
    duration_seconds: float
    session_id: str | None
    text: str
    structured_output: dict[str, Any] | None
    permission_denials: list[str]
    error_message: str | None
    usage: UsageReading | None
    rejection: LimitRejection | None


class IssueComment(Record):
    author: str
    created_at: datetime
    body: str


class QuestionThread(Record):
    questions: list[str]
    replies: list[str]


class ReplyAnswer(Record):
    question: str
    text: str


class ParsedReply(Record):
    answers: list[ReplyAnswer]
    note: str | None


class IssueIntake(Record):
    issue_number: int
    replies: int
    answers: int
    notes: int


class Intake(Record):
    issues: list[IssueIntake]


class PullRequest(Record):
    number: int
    title: str
    body: str
    head_branch: str
    author: str
    url: str


class AssessmentOutcome(Record):
    issue_number: int
    assessment: Assessment
    outcome: ClaudeOutcome
    cost_usd: float
    session_id: str | None
    rejection: LimitRejection | None = None


class ChangedFile(Record):
    path: str
    added: int
    removed: int
    binary: bool


class WorkerOutcome(Record):
    issue_number: int
    delivery: Delivery
    outcome: ClaudeOutcome
    cost_usd: float
    session_id: str | None
    permission_denials: list[str]
    rejection: LimitRejection | None = None


class InboxMessage(Record):
    text: str
    written_at: datetime


class InboxAnswer(Record):
    issue_number: int
    question_index: int
    question: str
    text: str
    written_at: datetime


class Inbox(Record):
    stop: bool
    pause: bool
    approvals: list[int]
    skips: list[int]
    answers: list[InboxAnswer]
    messages: list[str]


class BriefingAnswer(Record):
    issue_number: int
    question: str
    question_key: str
    text: str
    written_at: datetime


class IssueNote(Record):
    text: str
    written_at: datetime


class IssueNotes(Record):
    issue_number: int
    notes: list[IssueNote]


class PreparedSession(Record):
    run_id: str
    repo_key: str
    prepared_at: datetime
    question_count: int


class RunEvent(Record):
    at: datetime
    event: EventType
    issue_number: int | None
    detail: str


class PreflightCheck(Record):
    name: str
    outcome: CheckOutcome
    required: bool
    detail: str


class PreflightReport(Record):
    repo_key: str
    mode: RepoMode
    checked_at: datetime
    checks: list[PreflightCheck]

    @property
    def clear_to_run(self) -> bool:
        return all(
            check.outcome is not CheckOutcome.FAILED for check in self.checks if check.required
        )


class BlindLabelEntry(Record):
    issue_number: int
    label: BlindLabel
    expected_blocker: Blocker | None


class Disagreement(Record):
    issue_number: int
    user_label: BlindLabel
    agent_label: BlindLabel
    verdict: Verdict | None


class AgreementReport(Record):
    run_id: str
    repo_key: str
    labelled_count: int
    compared_count: int
    agreed_count: int
    agreement_rate: float
    execute_on_never: list[int]
    blocker_compared: int
    blocker_matched: int
    disagreements: list[Disagreement]


class RepoTarget(ConfigSection):
    slug: str = Field(pattern=REPOSITORY_SLUG_PATTERN)
    mode: RepoMode
    backend: Backend
    gate_commands: list[str] = Field(min_length=1)
    token_file: Path | None = None
    base_branch: str = DEFAULT_BASE_BRANCH
    recurse_submodules: bool = False
    remote_url: str | None = None
    setup_commands: list[str] = Field(default_factory=list)
    acceptance_command: str | None = None
    conventions_prompt: Path | None = None
    forbidden_paths: list[str] = Field(
        default_factory=lambda: list(DEFAULT_FORBIDDEN_PATHS), min_length=1
    )

    def token_path(self) -> Path:
        if self.token_file is None:
            raise ValueError(f"repository {self.slug} names no token_file")
        return self.token_file


class BudgetPolicy(ConfigSection):
    envelope_usd: float = Field(default=DEFAULT_ENVELOPE_USD, gt=0)
    per_task_usd: float = Field(default=DEFAULT_PER_TASK_USD, gt=0)
    assessor_usd: float = Field(default=DEFAULT_ASSESSOR_USD, gt=0)
    max_tasks: int = Field(default=DEFAULT_MAX_TASKS, ge=1)
    weekly_reset_at: datetime | None = None

    @model_validator(mode="after")
    def caps_fit_the_envelope(self) -> BudgetPolicy:
        if self.per_task_usd > self.envelope_usd:
            raise ValueError("per_task_usd exceeds envelope_usd")
        if self.assessor_usd > self.envelope_usd:
            raise ValueError("assessor_usd exceeds envelope_usd")
        return self


class UsagePolicy(ConfigSection):
    seven_day_ceiling: float = Field(default=DEFAULT_SEVEN_DAY_CEILING, gt=0.0, le=1.0)
    seven_day_reserve: float = Field(default=DEFAULT_SEVEN_DAY_RESERVE, gt=0.0, lt=1.0)
    five_hour_ceiling: float = Field(default=DEFAULT_FIVE_HOUR_CEILING, gt=0.0, le=1.0)
    probe_usd: float = Field(default=DEFAULT_PROBE_USD, gt=0)
    probe_timeout_seconds: int = Field(default=DEFAULT_PROBE_TIMEOUT_SECONDS, ge=1)

    @model_validator(mode="after")
    def the_reserve_leaves_room_under_the_ceiling(self) -> UsagePolicy:
        if self.seven_day_reserve >= self.seven_day_ceiling:
            raise ValueError("seven_day_reserve leaves no room under seven_day_ceiling")
        return self


class ModelPolicy(ConfigSection):
    assessor: str = DEFAULT_ASSESSOR_MODEL
    assessor_effort: str = DEFAULT_ASSESSOR_EFFORT
    worker: str = DEFAULT_WORKER_MODEL
    worker_effort: str = DEFAULT_WORKER_EFFORT


DEFAULT_ALLOWED_EFFORT: Final[tuple[Effort, ...]] = (Effort.XS, Effort.S)
DEFAULT_ALLOWED_RISK: Final[tuple[Risk, ...]] = (
    Risk.DOCS,
    Risk.TESTS,
    Risk.REFACTOR,
    Risk.BEHAVIOUR,
)


class WorkerPolicy(ConfigSection):
    timeout_minutes: int = Field(default=DEFAULT_TIMEOUT_MINUTES, ge=1)
    assessor_timeout_minutes: int = Field(default=DEFAULT_ASSESSOR_TIMEOUT_MINUTES, ge=1)
    idle_minutes: int = Field(default=DEFAULT_IDLE_MINUTES, ge=1)
    max_diff_lines: int = Field(default=DEFAULT_MAX_DIFF_LINES, ge=1)
    allowed_effort: list[Effort] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_EFFORT), min_length=1
    )
    allowed_risk: list[Risk] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_RISK), min_length=1
    )
    branch_prefix: str = DEFAULT_BRANCH_PREFIX
    parallel: int = Field(default=DEFAULT_PARALLEL_WORKERS, ge=1)
    shared_paths: list[str] = Field(default_factory=list)


class LabelPolicy(ConfigSection):
    namespace: str = Field(default=DEFAULT_LABEL_NAMESPACE, pattern=LABEL_NAMESPACE_PATTERN)
    auto: str
    approved: str
    never: str
    review: str
    needs_input: str
    unfinished: str

    @model_validator(mode="before")
    @classmethod
    def names_follow_the_namespace(cls, values: Any) -> Any:  # noqa: ANN401
        if not isinstance(values, dict):
            return values
        namespace = values.get("namespace", DEFAULT_LABEL_NAMESPACE)
        named = {field: f"{namespace}{suffix}" for field, suffix in LABEL_SUFFIXES.items()}
        return {**named, **values}

    @model_validator(mode="after")
    def every_name_sits_inside_the_namespace(self) -> LabelPolicy:
        outside = [
            f"labels.{field} {getattr(self, field)!r} is outside "
            f"labels.namespace {self.namespace!r}"
            for field in LABEL_SUFFIXES
            if not str(getattr(self, field)).startswith(self.namespace)
        ]
        if outside:
            raise ValueError("; ".join(outside))
        return self


class EligibilityPolicy(ConfigSection):
    minimum_body_length: int = Field(default=DEFAULT_MINIMUM_BODY_LENGTH, ge=0)
    excluded_title_pattern: str = DEFAULT_EXCLUDED_TITLE_PATTERN
    foreign_activity_window_hours: int = Field(default=DEFAULT_FOREIGN_ACTIVITY_WINDOW_HOURS, ge=0)

    @field_validator("excluded_title_pattern")
    @classmethod
    def pattern_compiles(cls, value: str) -> str:
        re.compile(value)
        return value


class IdentityPolicy(ConfigSection):
    git_author_email: str
    git_author_name: str = DEFAULT_GIT_AUTHOR_NAME
    comment_footer: str = DEFAULT_COMMENT_FOOTER
    operator_login: str | None = None


class WeeklyMoment(ConfigSection):
    day_of_week: Weekday
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)

    @property
    def minute_of_week(self) -> int:
        day = WEEKDAYS.index(self.day_of_week)
        return day * MINUTES_PER_DAY + self.hour * MINUTES_PER_HOUR + self.minute


class ScheduledRun(WeeklyMoment):
    command: ScheduledCommand = DEFAULT_SCHEDULED_COMMAND


class WeekendWindow(ConfigSection):
    opens: WeeklyMoment = Field(default_factory=lambda: WeeklyMoment(**DEFAULT_WINDOW_OPENS))
    closes: WeeklyMoment = Field(default_factory=lambda: WeeklyMoment(**DEFAULT_WINDOW_CLOSES))

    @property
    def length_minutes(self) -> int:
        # A window that closes at the moment it opens spans the whole week.
        span = (self.closes.minute_of_week - self.opens.minute_of_week) % MINUTES_PER_WEEK
        return span if span else MINUTES_PER_WEEK

    def covers(self, moment: WeeklyMoment) -> bool:
        offset = (moment.minute_of_week - self.opens.minute_of_week) % MINUTES_PER_WEEK
        return offset < self.length_minutes


class SchedulePolicy(ConfigSection):
    repo_key: str | None = None
    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1)
    window: WeekendWindow = Field(default_factory=WeekendWindow)
    runs: list[ScheduledRun] = Field(
        default_factory=lambda: [ScheduledRun(**run) for run in DEFAULT_SCHEDULED_RUNS],
        min_length=1,
    )

    @model_validator(mode="after")
    def weekend_runs_start_inside_the_window(self) -> SchedulePolicy:
        for run in self.runs:
            if run.command is ScheduledCommand.WEEKEND and not self.window.covers(run):
                raise ValueError(
                    f"the {run.day_of_week.value} {run.hour:02d}:{run.minute:02d} weekend run "
                    "starts outside schedule.window"
                )
        return self


class SettingsPolicy(Record):
    worker: Path
    assessor: Path


class Workspace(Record):
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def reference_path(self) -> Path:
        return self.root / REFERENCE_FILENAME

    @property
    def secrets_dir(self) -> Path:
        return self.root / SECRETS_DIRECTORY_NAME

    @property
    def prompts_dir(self) -> Path:
        return self.root / PROMPTS_DIRECTORY_NAME

    @property
    def acceptance_dir(self) -> Path:
        return self.root / ACCEPTANCE_DIRECTORY_NAME

    @property
    def agent_home(self) -> Path:
        return self.root / AGENT_HOME_NAME

    @property
    def work_dir(self) -> Path:
        return self.root / WORK_DIRECTORY_NAME

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIRECTORY_NAME

    @property
    def worker_fence(self) -> Path:
        return self.agent_home / CLAUDE_DIRECTORY_NAME / WORKER_FENCE_NAME

    @property
    def assessor_fence(self) -> Path:
        return self.agent_home / CLAUDE_DIRECTORY_NAME / ASSESSOR_FENCE_NAME

    @property
    def settings(self) -> SettingsPolicy:
        return SettingsPolicy(worker=self.worker_fence, assessor=self.assessor_fence)

    @property
    def oauth_token_path(self) -> Path:
        return self.secrets_dir / OAUTH_TOKEN_NAME

    @property
    def alert_webhook_path(self) -> Path:
        return self.secrets_dir / ALERT_WEBHOOK_NAME

    def repository_token_path(self, repo_key: str) -> Path:
        return self.secrets_dir / REPOSITORY_TOKEN_TEMPLATE.format(repo_key=repo_key)

    def workbench_path(self, repo_key: str) -> Path:
        return self.work_dir / repo_key

    def worktrees_path(self, repo_key: str) -> Path:
        return self.work_dir / f"{repo_key}{WORKTREES_SUFFIX}"

    def worktree_path(self, repo_key: str, branch: str) -> Path:
        return self.worktrees_path(repo_key) / branch


class Policy(ConfigSection):
    workspace: Workspace
    repos: dict[str, RepoTarget] = Field(min_length=1)
    identity: IdentityPolicy
    schedule: SchedulePolicy
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)
    usage: UsagePolicy = Field(default_factory=UsagePolicy)
    models: ModelPolicy = Field(default_factory=ModelPolicy)
    worker: WorkerPolicy = Field(default_factory=WorkerPolicy)
    labels: LabelPolicy = Field(default_factory=lambda: LabelPolicy.model_validate({}))
    eligibility: EligibilityPolicy = Field(default_factory=EligibilityPolicy)

    @property
    def state_dir(self) -> Path:
        return self.workspace.state_dir

    @property
    def agent_home(self) -> Path:
        return self.workspace.agent_home

    @property
    def settings(self) -> SettingsPolicy:
        return self.workspace.settings

    @model_validator(mode="after")
    def schedule_names_a_known_repository(self) -> Policy:
        if self.schedule.repo_key is None:
            if len(self.repos) == 1:
                return self
            known = ", ".join(sorted(self.repos))
            raise ValueError(f"schedule.repo_key must name one of: {known}")
        if self.schedule.repo_key not in self.repos:
            known = ", ".join(sorted(self.repos))
            raise ValueError(f"schedule.repo_key {self.schedule.repo_key!r} is not one of: {known}")
        return self

    @property
    def scheduled_repo_key(self) -> str:
        if self.schedule.repo_key is not None:
            return self.schedule.repo_key
        return next(iter(self.repos))
