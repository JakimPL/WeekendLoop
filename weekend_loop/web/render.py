from __future__ import annotations

from weekend_loop.status import RunStatus, age_phrase, current_phrase, event_line, liveness_phrase
from weekend_loop.web.theme import (
    ACCENT,
    ALERT,
    BUDGET_ALERT_FRACTION,
    LIVENESS_COLOURS,
    MUTED,
    PRODUCT_NAME,
    SECONDS_PER_MINUTE,
    STALE_HEARTBEAT_SECONDS,
    STATUS_COLOURS,
    escape,
)
from weekend_loop.web.view import NO_EVENT_TEXT, NO_TRANSCRIPT_TEXT, RunView


def heartbeat_phrase(view: RunView) -> str:
    minutes = view.heartbeat_age_seconds / SECONDS_PER_MINUTE
    freshness = "stale" if view.heartbeat_age_seconds > STALE_HEARTBEAT_SECONDS else "fresh"
    return f"heartbeat {minutes:.0f} min ago ({freshness})"


def pulse_phrase(status: RunStatus) -> str | None:
    if status.pulse_age_seconds is None:
        return None
    parts = [status.liveness.value]
    activity = current_phrase(status)
    if activity is not None:
        parts.append(activity)
    parts.append(f"pulse {age_phrase(status.pulse_age_seconds)} ago")
    return " · ".join(parts)


def control_phrase(view: RunView) -> str:
    if view.stop:
        return "stop requested"
    if view.pause:
        return "pause requested"
    return "running under policy"


def render_header(view: RunView, status: RunStatus) -> str:
    pulse = pulse_phrase(status)
    freshness = pulse if pulse is not None else heartbeat_phrase(view)
    return (
        '<div class="weekend-panel"><div class="weekend-banner">'
        f'<div class="weekend-wordmark">{escape(PRODUCT_NAME)}</div>'
        f"<h1>{escape(view.repo_key)} — {escape(view.phase)}</h1>"
        f'<div class="weekend-meta">run {escape(view.run_id)} · mode {escape(view.mode)} · '
        f"{escape(control_phrase(view))} · {escape(freshness)}</div>"
        "</div></div>"
    )


def render_budget(view: RunView) -> str:
    fill = ACCENT if view.budget_fraction < BUDGET_ALERT_FRACTION else ALERT
    percentage = round(view.budget_fraction * 100)
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        '<div class="weekend-label">budget</div>'
        f"<div>${view.spent_usd:.2f} of ${view.envelope_usd:.2f} ({percentage}%)</div>"
        f'<div class="weekend-track"><div class="weekend-fill" '
        f'style="width: {percentage}%; background: {fill};"></div></div>'
        "</div></div>"
    )


def render_status_chips(view: RunView) -> str:
    chips = "".join(
        f'<span class="weekend-chip" style="background: {STATUS_COLOURS.get(status, MUTED)};">'
        f"{escape(status)} {count}</span>"
        for status, count in view.counts.items()
    )
    body = chips if chips else '<div class="weekend-note">no tasks yet</div>'
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        f'<div class="weekend-label">tasks</div>{body}</div></div>'
    )


def render_notes(view: RunView) -> str:
    lines = [*view.notes, *view.messages]
    body = "".join(f'<div class="weekend-note">{escape(line)}</div>' for line in lines)
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        '<div class="weekend-label">notes</div>'
        f"{body if body else '<div class="weekend-note">none</div>'}</div></div>"
    )


def render_lines_block(lines: list[str], empty_text: str) -> str:
    if not lines:
        return f'<div class="weekend-note">{escape(empty_text)}</div>'
    return '<div class="weekend-transcript">' + "\n".join(escape(line) for line in lines) + "</div>"


def live_notes(status: RunStatus) -> list[str]:
    notes = [liveness_phrase(status)]
    activity = current_phrase(status)
    if activity is not None:
        notes.append(activity)
    if status.transcript is not None:
        notes.append(f"transcript: {status.transcript}")
    return notes


def render_live_summary(status: RunStatus) -> str:
    chip = (
        f'<span class="weekend-chip" style="background: {LIVENESS_COLOURS[status.liveness]};">'
        f"{escape(status.liveness.value)}</span>"
    )
    notes = [f'<div class="weekend-note">{escape(note)}</div>' for note in live_notes(status)]
    return chip + "".join(notes)


def render_live(status: RunStatus, transcript_link: str | None) -> str:
    link = (
        f'<div class="weekend-note"><a href="{escape(transcript_link)}">full transcript</a></div>'
        if transcript_link is not None
        else ""
    )
    events = [event_line(event) for event in status.events]
    return (
        '<div class="weekend-panel"><div class="weekend-card">'
        '<div class="weekend-label">live</div>'
        f"{render_live_summary(status)}"
        '<div class="weekend-label">transcript</div>'
        f"{render_lines_block(status.transcript_lines, NO_TRANSCRIPT_TEXT)}{link}"
        '<div class="weekend-label">events (UTC)</div>'
        f"{render_lines_block(events, NO_EVENT_TEXT)}"
        "</div></div>"
    )
