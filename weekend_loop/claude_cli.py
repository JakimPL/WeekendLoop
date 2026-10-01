from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from weekend_loop.confinement import Confinement, confined, confined_environment, unit_name
from weekend_loop.models import (
    ClaudeOutcome,
    ClaudeResult,
    LimitRejection,
    Record,
    UsageReading,
    UsageWindow,
)
from weekend_loop.processes import exited, terminate

CLAUDE_BINARY: Final[str] = "claude"
SETPRIV_BINARY: Final[str] = "setpriv"
TIMEOUT_BINARY: Final[str] = "timeout"
PARENT_DEATH_SIGNAL: Final[str] = "TERM"
STDERR_SUFFIX: Final[str] = ".stderr"
KILL_AFTER: Final[str] = "30s"
TIMEOUT_EXIT_CODE: Final[int] = 124
SIGKILL_EXIT_CODE: Final[int] = 137
JSON_OUTPUT: Final[str] = "json"
STREAM_JSON_OUTPUT: Final[str] = "stream-json"
RESULT_MESSAGE_TYPE: Final[str] = "result"
RATE_LIMIT_MESSAGE_TYPE: Final[str] = "rate_limit_event"
RATE_LIMIT_INFO_KEY: Final[str] = "rate_limit_info"
UNIFIED_WINDOWS_KEY: Final[str] = "unifiedWindows"
FIVE_HOUR_KEY: Final[str] = "five_hour"
SEVEN_DAY_KEY: Final[str] = "seven_day"
UTILIZATION_KEY: Final[str] = "utilization"
RESETS_AT_KEY: Final[str] = "resetsAt"
STATUS_KEY: Final[str] = "status"
OVERAGE_KEY: Final[str] = "isUsingOverage"
RATE_LIMIT_TYPE_KEY: Final[str] = "rateLimitType"
REJECTED_STATUS: Final[str] = "rejected"
UNKNOWN_STATUS: Final[str] = "unknown"
SUCCESS_SUBTYPE: Final[str] = "success"
BUDGET_SUBTYPE: Final[str] = "error_max_budget_usd"
BUDGET_TERMINAL_REASON: Final[str] = "budget_exhausted"
TOO_MANY_REQUESTS_STATUS: Final[int] = 429
ERROR_TAIL_CHARACTERS: Final[int] = 800
POLL_INTERVAL_SECONDS: Final[float] = 2.0
INHERITED_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = ("PATH", "LANG", "LC_ALL", "TZ")
OAUTH_TOKEN_VARIABLE: Final[str] = "CLAUDE_CODE_OAUTH_TOKEN"
# The CLI names each allowance window in its limit message: "You've hit your <label> · resets …".
WEEKLY_LIMIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(weekly|opus|sonnet|fable|usage credit|spend) limit", re.IGNORECASE
)
WINDOW_LIMIT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(session limit|5-?hour limit|usage limit reached|rate limit)", re.IGNORECASE
)
SESSION_MISSING_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"no conversation found", re.IGNORECASE
)
FENCED_JSON_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL
)


class CallWatch(Protocol):
    def stop_requested(self) -> bool: ...

    def beat(self) -> None: ...


class Unwatched:
    def stop_requested(self) -> bool:
        return False

    def beat(self) -> None:
        return None


UNWATCHED: Final[CallWatch] = Unwatched()


class Ending(StrEnum):
    EXITED = "exited"
    CANCELLED = "cancelled"
    STALLED = "stalled"


class CallEnd(Record):
    ending: Ending
    exit_code: int
    duration_seconds: float
    timeout_seconds: int


class ClaudeInvocation(Record):
    prompt: str
    system_prompt: str
    model: str
    effort: str
    tools: list[str]
    permission_mode: str
    restricted: bool
    setting_sources: str | None
    settings_file: Path
    json_schema: str | None
    output_format: str
    max_budget_usd: float
    timeout_seconds: int
    working_directory: Path
    session_id: str | None
    resume: bool
    persist_session: bool
    transcript_path: Path
    idle_seconds: int
    confinement: Confinement | None


def build_command(invocation: ClaudeInvocation) -> list[str]:
    command = [
        SETPRIV_BINARY,
        "--pdeathsig",
        PARENT_DEATH_SIGNAL,
        TIMEOUT_BINARY,
        f"--kill-after={KILL_AFTER}",
        f"{invocation.timeout_seconds}s",
        CLAUDE_BINARY,
        "-p",
        invocation.prompt,
        "--model",
        invocation.model,
        "--effort",
        invocation.effort,
        "--permission-mode",
        invocation.permission_mode,
        "--permission-prompts",
        "none",
        "--settings",
        str(invocation.settings_file),
        "--append-system-prompt",
        invocation.system_prompt,
        "--output-format",
        invocation.output_format,
        "--max-budget-usd",
        f"{invocation.max_budget_usd}",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if invocation.restricted:
        command.append("--restricted")
    command.extend(["--tools", ",".join(invocation.tools)])
    if invocation.json_schema is not None:
        command.extend(["--json-schema", invocation.json_schema])
    if invocation.setting_sources is not None:
        command.extend(["--setting-sources", invocation.setting_sources])
    if invocation.output_format == STREAM_JSON_OUTPUT:
        command.append("--verbose")
    if invocation.session_id is not None:
        session_flag = "--resume" if invocation.resume else "--session-id"
        command.extend([session_flag, invocation.session_id])
    if not invocation.persist_session:
        command.append("--no-session-persistence")
    return command


def confined_command(invocation: ClaudeInvocation) -> list[str]:
    command = build_command(invocation)
    confinement = invocation.confinement
    if confinement is None:
        return command
    return confined(command, confinement, unit_name(confinement.unit_prefix))


def invocation_environment(
    invocation: ClaudeInvocation, environment: dict[str, str]
) -> dict[str, str]:
    confinement = invocation.confinement
    if confinement is None:
        return environment
    return confined_environment(environment, confinement)


def agent_environment(agent_home: Path, oauth_token: str, extra: dict[str, str]) -> dict[str, str]:
    environment = {key: os.environ[key] for key in INHERITED_ENVIRONMENT_KEYS if key in os.environ}
    environment["HOME"] = str(agent_home)
    environment[OAUTH_TOKEN_VARIABLE] = oauth_token
    environment.update(extra)
    return environment


ONE_LINE_TOKEN_HINT: Final[str] = (
    'keep only the token itself, the line `claude setup-token` prints after "Your OAuth token"'
)


def read_oauth_token(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Claude token file {path} is missing")
    token = path.read_text().strip()
    if not token:
        raise ValueError(f"Claude token file {path} is empty")
    if any(character.isspace() for character in token):
        raise ValueError(
            f"Claude token file {path} holds more than the token; {ONE_LINE_TOKEN_HINT}"
        )
    return token


def stderr_path(transcript: Path) -> Path:
    return transcript.with_suffix(STDERR_SUFFIX)


def wait_for_process(
    process: subprocess.Popen[bytes], transcript: Path, idle_seconds: int, watch: CallWatch
) -> Ending:
    last_size = -1
    last_growth = time.monotonic()
    while not exited(process, POLL_INTERVAL_SECONDS):
        watch.beat()
        if watch.stop_requested():
            terminate(process)
            return Ending.CANCELLED
        size = transcript.stat().st_size
        if size != last_size:
            last_size, last_growth = size, time.monotonic()
        elif time.monotonic() - last_growth >= idle_seconds:
            terminate(process)
            return Ending.STALLED
    return Ending.EXITED


def run_claude(
    invocation: ClaudeInvocation, environment: dict[str, str], watch: CallWatch
) -> ClaudeResult:
    transcript = invocation.transcript_path
    transcript.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.monotonic()
    observed_at = datetime.now(UTC)
    with transcript.open("wb") as stdout_file, stderr_path(transcript).open("wb") as stderr_file:
        process = subprocess.Popen(
            confined_command(invocation),
            cwd=invocation.working_directory,
            env=invocation_environment(invocation, environment),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
        )
        ending = wait_for_process(process, transcript, invocation.idle_seconds, watch)
    stdout = transcript.read_text(errors="replace")
    stderr = stderr_path(transcript).read_text(errors="replace")
    end = CallEnd(
        ending=ending,
        exit_code=process.returncode,
        duration_seconds=time.monotonic() - started_at,
        timeout_seconds=invocation.timeout_seconds,
    )
    return build_result(
        end,
        parse_output(stdout, invocation.output_format),
        stderr,
        usage_from_stream(stdout, observed_at),
        parse_rejection(stdout),
    )


def parse_output(stdout: str, output_format: str) -> dict[str, Any] | None:
    if output_format == STREAM_JSON_OUTPUT:
        return last_result_message(stdout)
    return parse_json_object(stdout)


def last_result_message(stdout: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for line in stdout.splitlines():
        message = parse_json_object(line)
        if message is not None and message.get("type") == RESULT_MESSAGE_TYPE:
            found = message
    return found


def parse_window(payload: dict[str, Any]) -> UsageWindow | None:
    utilization = payload.get(UTILIZATION_KEY)
    resets_at = payload.get(RESETS_AT_KEY)
    if not isinstance(utilization, int | float) or not isinstance(resets_at, int | float):
        return None
    return UsageWindow(
        utilization=float(utilization), resets_at=datetime.fromtimestamp(resets_at, UTC)
    )


def parse_usage_event(message: dict[str, Any], observed_at: datetime) -> UsageReading | None:
    if message.get("type") != RATE_LIMIT_MESSAGE_TYPE:
        return None
    info = message.get(RATE_LIMIT_INFO_KEY)
    if not isinstance(info, dict):
        return None
    windows = info.get(UNIFIED_WINDOWS_KEY)
    if not isinstance(windows, dict):
        return None
    five_hour_payload = windows.get(FIVE_HOUR_KEY)
    seven_day_payload = windows.get(SEVEN_DAY_KEY)
    if not isinstance(five_hour_payload, dict) or not isinstance(seven_day_payload, dict):
        return None
    five_hour = parse_window(five_hour_payload)
    seven_day = parse_window(seven_day_payload)
    if five_hour is None or seven_day is None:
        return None
    status = info.get(STATUS_KEY)
    return UsageReading(
        five_hour=five_hour,
        seven_day=seven_day,
        status=status if isinstance(status, str) else UNKNOWN_STATUS,
        is_using_overage=info.get(OVERAGE_KEY) is True,
        observed_at=observed_at,
    )


def last_rate_limit_event(stdout: str) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None
    for line in stdout.splitlines():
        message = parse_json_object(line)
        if message is not None and message.get("type") == RATE_LIMIT_MESSAGE_TYPE:
            found = message
    return found


def usage_from_stream(stdout: str, observed_at: datetime) -> UsageReading | None:
    event = last_rate_limit_event(stdout)
    return None if event is None else parse_usage_event(event, observed_at)


def rejection_from(info: dict[str, Any]) -> LimitRejection:
    rate_limit_type = info.get(RATE_LIMIT_TYPE_KEY)
    resets_at = info.get(RESETS_AT_KEY)
    return LimitRejection(
        rate_limit_type=rate_limit_type if isinstance(rate_limit_type, str) else None,
        resets_at=(
            datetime.fromtimestamp(resets_at, UTC) if isinstance(resets_at, int | float) else None
        ),
    )


def parse_rejection(stdout: str) -> LimitRejection | None:
    event = last_rate_limit_event(stdout)
    if event is None:
        return None
    info = event.get(RATE_LIMIT_INFO_KEY)
    if not isinstance(info, dict) or info.get(STATUS_KEY) != REJECTED_STATUS:
        return None
    return rejection_from(info)


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if not candidate:
        return None
    fenced = FENCED_JSON_PATTERN.search(candidate)
    if fenced is not None:
        candidate = fenced.group(1)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def build_result(
    end: CallEnd,
    payload: dict[str, Any] | None,
    stderr: str,
    usage: UsageReading | None,
    rejection: LimitRejection | None,
) -> ClaudeResult:
    outcome = classify_outcome(end, payload, rejection, stderr)
    message = f"{error_text(payload)}\n{stderr}".strip()
    return ClaudeResult(
        outcome=outcome,
        exit_code=end.exit_code,
        cost_usd=result_cost(payload),
        duration_seconds=end.duration_seconds,
        session_id=result_session_id(payload),
        text=result_text(payload),
        structured_output=extract_structured_output(payload),
        permission_denials=result_denials(payload),
        error_message=None if outcome is ClaudeOutcome.OK else message[-ERROR_TAIL_CHARACTERS:],
        usage=usage,
        rejection=rejection,
    )


def classify_outcome(
    end: CallEnd,
    payload: dict[str, Any] | None,
    rejection: LimitRejection | None,
    stderr: str,
) -> ClaudeOutcome:
    if end.ending is Ending.CANCELLED:
        return ClaudeOutcome.CANCELLED
    if end.ending is Ending.STALLED:
        return ClaudeOutcome.STALLED
    wall_clock = wall_clock_outcome(end)
    if wall_clock is not None:
        return wall_clock
    if payload is None:
        return silent_outcome(rejection, stderr)
    if succeeded(end.exit_code, payload):
        return ClaudeOutcome.OK
    return failure_outcome(payload, rejection, f"{error_text(payload)}\n{stderr}")


def wall_clock_outcome(end: CallEnd) -> ClaudeOutcome | None:
    if end.exit_code == TIMEOUT_EXIT_CODE:
        return ClaudeOutcome.TIMEOUT
    if end.exit_code == SIGKILL_EXIT_CODE:
        # `timeout` exits 137 both after --kill-after and when the child was SIGKILLed (OOM).
        timed_out = end.duration_seconds >= end.timeout_seconds
        return ClaudeOutcome.TIMEOUT if timed_out else ClaudeOutcome.KILLED
    return None


def silent_outcome(rejection: LimitRejection | None, stderr: str) -> ClaudeOutcome:
    if SESSION_MISSING_PATTERN.search(stderr):
        return ClaudeOutcome.SESSION_MISSING
    if rejection is not None:
        return limit_of(rejection)
    limit = limit_in_text(stderr)
    return limit if limit is not None else ClaudeOutcome.NO_OUTPUT


def succeeded(exit_code: int, payload: dict[str, Any]) -> bool:
    return (
        exit_code == 0
        and payload.get("is_error") is not True
        and payload.get("subtype", SUCCESS_SUBTYPE) == SUCCESS_SUBTYPE
    )


def failure_outcome(
    payload: dict[str, Any], rejection: LimitRejection | None, text: str
) -> ClaudeOutcome:
    if (
        payload.get("subtype") == BUDGET_SUBTYPE
        or payload.get("terminal_reason") == BUDGET_TERMINAL_REASON
    ):
        return ClaudeOutcome.BUDGET
    if rejection is not None:
        return limit_of(rejection)
    limit = limit_in_text(text)
    if limit is not None:
        return limit
    if payload.get("api_error_status") == TOO_MANY_REQUESTS_STATUS:
        return ClaudeOutcome.WINDOW_LIMIT
    return ClaudeOutcome.FAILED


def limit_of(rejection: LimitRejection) -> ClaudeOutcome:
    if rejection.rate_limit_type in (None, FIVE_HOUR_KEY):
        return ClaudeOutcome.WINDOW_LIMIT
    return ClaudeOutcome.WEEKLY_LIMIT


def limit_in_text(text: str) -> ClaudeOutcome | None:
    if WEEKLY_LIMIT_PATTERN.search(text):
        return ClaudeOutcome.WEEKLY_LIMIT
    if WINDOW_LIMIT_PATTERN.search(text):
        return ClaudeOutcome.WINDOW_LIMIT
    return None


def error_text(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return ""
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return result_text(payload)
    listed = [error for error in errors if isinstance(error, str)]
    return "\n".join([result_text(payload), *listed]).strip()


def result_text(payload: dict[str, Any] | None) -> str:
    if payload is None:
        return ""
    value = payload.get("result")
    return value if isinstance(value, str) else ""


def result_cost(payload: dict[str, Any] | None) -> float:
    if payload is None:
        return 0.0
    value = payload.get("total_cost_usd")
    return float(value) if isinstance(value, int | float) else 0.0


def result_session_id(payload: dict[str, Any] | None) -> str | None:
    if payload is None:
        return None
    value = payload.get("session_id")
    return value if isinstance(value, str) else None


def result_denials(payload: dict[str, Any] | None) -> list[str]:
    if payload is None:
        return []
    denials = payload.get("permission_denials")
    if not isinstance(denials, list):
        return []
    named: list[str] = []
    for denial in denials:
        if isinstance(denial, dict):
            tool_name = denial.get("tool_name")
            named.append(tool_name if isinstance(tool_name, str) else json.dumps(denial))
        else:
            named.append(str(denial))
    return named


def extract_structured_output(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    value = payload.get("structured_output")
    if isinstance(value, dict):
        return value
    return parse_json_object(result_text(payload))
