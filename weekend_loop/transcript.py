from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from weekend_loop.models import Record, UsageReading

MAX_LINE_WIDTH: Final[int] = 160
ELLIPSIS: Final[str] = "…"
NEWLINE: Final[bytes] = b"\n"
WHITESPACE_PATTERN: Final[re.Pattern[str]] = re.compile(r"\s+")
UNKNOWN_VALUE: Final[str] = "unknown"
SUCCESS_SUBTYPE: Final[str] = "success"
ERROR_OUTCOME: Final[str] = "error"
TOOL_TARGET_KEYS: Final[dict[str, str]] = {
    "Bash": "command",
    "Read": "file_path",
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
    "WebFetch": "url",
    "WebSearch": "query",
    "Task": "description",
    "Agent": "description",
}
SEARCH_TOOLS: Final[frozenset[str]] = frozenset({"Grep", "Glob"})
ALLOWANCE_WINDOWS: Final[tuple[tuple[str, str], ...]] = (
    ("five_hour", "five-hour"),
    ("seven_day", "seven-day"),
)


class TranscriptCursor(Record):
    path: Path
    offset: int


def single_line(text: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def clip(text: str) -> str:
    if len(text) <= MAX_LINE_WIDTH:
        return text
    return text[: MAX_LINE_WIDTH - len(ELLIPSIS)] + ELLIPSIS


def labelled(label: str, text: str) -> str:
    return clip(f"{label}: {single_line(text)}")


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def text_field(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value.strip() else None


def dict_field(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    return value if isinstance(value, dict) else None


def content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    envelope = dict_field(message, "message")
    content = envelope.get("content") if envelope is not None else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def decode_message(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def render_system(message: dict[str, Any]) -> list[str]:
    if message.get("subtype") != "init":
        return []
    model = text_field(message, "model") or UNKNOWN_VALUE
    tools = message.get("tools")
    tool_count = len(tools) if isinstance(tools, list) else 0
    return [labelled("start", f"{model} with {tool_count} tools")]


def search_target(tool_input: dict[str, Any]) -> str:
    pattern = text_field(tool_input, "pattern") or ""
    path = text_field(tool_input, "path")
    return pattern if path is None else f"{pattern} in {path}"


def first_text_input(tool_input: dict[str, Any]) -> str:
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return value
    return ""


def tool_target(name: str, tool_input: dict[str, Any]) -> str:
    if name in SEARCH_TOOLS:
        return search_target(tool_input)
    key = TOOL_TARGET_KEYS.get(name)
    if key is not None:
        return text_field(tool_input, key) or ""
    return first_text_input(tool_input)


def render_tool_use(block: dict[str, Any]) -> list[str]:
    name = text_field(block, "name") or UNKNOWN_VALUE
    tool_input = dict_field(block, "input") or {}
    target = single_line(tool_target(name, tool_input))
    return [labelled(name, target) if target else name]


def render_block(block: dict[str, Any]) -> list[str]:
    kind = block.get("type")
    if kind == "text":
        text = text_field(block, "text")
        return [] if text is None else [labelled("says", text)]
    if kind == "thinking":
        thinking = text_field(block, "thinking")
        return [] if thinking is None else [labelled("thinking", thinking)]
    if kind == "tool_use":
        return render_tool_use(block)
    return []


def render_assistant(message: dict[str, Any]) -> list[str]:
    return [line for block in content_blocks(message) for line in render_block(block)]


def tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        part["text"]
        for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    return "\n".join(parts)


def render_tool_error(block: dict[str, Any]) -> list[str]:
    if block.get("type") != "tool_result" or block.get("is_error") is not True:
        return []
    return [labelled("tool error", first_line(tool_result_text(block)))]


def render_user(message: dict[str, Any]) -> list[str]:
    return [line for block in content_blocks(message) for line in render_tool_error(block)]


def percentage(utilization: float) -> str:
    return f"{round(utilization * 100)}%"


def allowance_phrase(windows: list[tuple[str, float]], status: str) -> str:
    listed = ", ".join(f"{label} {percentage(utilization)}" for label, utilization in windows)
    return f"{listed} ({status})"


def usage_phrase(reading: UsageReading) -> str:
    return allowance_phrase(
        [
            ("five-hour", reading.five_hour.utilization),
            ("seven-day", reading.seven_day.utilization),
        ],
        reading.status,
    )


def window_utilization(windows: dict[str, Any], key: str) -> float | None:
    window = dict_field(windows, key)
    utilization = window.get("utilization") if window is not None else None
    return float(utilization) if isinstance(utilization, int | float) else None


def allowance_windows(info: dict[str, Any]) -> list[tuple[str, float]]:
    windows = dict_field(info, "unifiedWindows") or {}
    found: list[tuple[str, float]] = []
    for key, label in ALLOWANCE_WINDOWS:
        utilization = window_utilization(windows, key)
        if utilization is not None:
            found.append((label, utilization))
    return found


def render_rate_limit(message: dict[str, Any]) -> list[str]:
    info = dict_field(message, "rate_limit_info")
    if info is None:
        return []
    status = text_field(info, "status") or UNKNOWN_VALUE
    windows = allowance_windows(info)
    if windows:
        return [labelled("allowance", allowance_phrase(windows, status))]
    limit_type = text_field(info, "rateLimitType")
    return [labelled("allowance", status if limit_type is None else f"{status} on {limit_type}")]


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def result_outcome(message: dict[str, Any]) -> str:
    subtype = text_field(message, "subtype") or UNKNOWN_VALUE
    if message.get("is_error") is True and subtype == SUCCESS_SUBTYPE:
        return ERROR_OUTCOME
    return subtype


def render_result(message: dict[str, Any]) -> list[str]:
    parts = [result_outcome(message)]
    cost = message.get("total_cost_usd")
    if isinstance(cost, int | float):
        parts.append(f"${cost:.2f}")
    turns = message.get("num_turns")
    if isinstance(turns, int):
        parts.append(plural(turns, "turn"))
    text = text_field(message, "result")
    if message.get("is_error") is True and text is not None:
        parts.append(first_line(text))
    return [labelled("result", ", ".join(parts))]


MESSAGE_RENDERERS: Final[dict[str, Callable[[dict[str, Any]], list[str]]]] = {
    "system": render_system,
    "assistant": render_assistant,
    "user": render_user,
    "rate_limit_event": render_rate_limit,
    "result": render_result,
}


def render_message(message: dict[str, Any]) -> list[str]:
    kind = message.get("type")
    renderer = MESSAGE_RENDERERS.get(kind) if isinstance(kind, str) else None
    return [] if renderer is None else renderer(message)


def render_line(line: str) -> list[str]:
    message = decode_message(line)
    return [] if message is None else render_message(message)


def render_lines(lines: list[str]) -> list[str]:
    return [rendered for line in lines for rendered in render_line(line)]


def tail_of(lines: list[str], count: int) -> list[str]:
    if count <= 0:
        return []
    groups: list[list[str]] = []
    collected = 0
    for line in reversed(lines):
        if collected >= count:
            break
        rendered = render_line(line)
        groups.append(rendered)
        collected += len(rendered)
    flattened = [text for group in reversed(groups) for text in group]
    return flattened[-count:]


def complete_lines(chunk: bytes) -> tuple[list[str], int]:
    end = chunk.rfind(NEWLINE)
    if end < 0:
        return [], 0
    return chunk[:end].decode(errors="replace").split("\n"), end + 1


def appended_lines(path: Path, offset: int) -> tuple[list[str], int]:
    if not path.is_file():
        return [], offset
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    lines, consumed = complete_lines(chunk)
    return lines, offset + consumed


def rendered_transcript(path: Path) -> list[str]:
    return render_lines(appended_lines(path, 0)[0])


def attach_transcript(path: Path, count: int) -> tuple[TranscriptCursor, list[str]]:
    lines, offset = appended_lines(path, 0)
    return TranscriptCursor(path=path, offset=offset), tail_of(lines, count)


def transcript_tail(path: Path, count: int) -> list[str]:
    return attach_transcript(path, count)[1]


def follow_transcript(cursor: TranscriptCursor) -> tuple[TranscriptCursor, list[str]]:
    lines, offset = appended_lines(cursor.path, cursor.offset)
    return cursor.model_copy(update={"offset": offset}), render_lines(lines)
