from __future__ import annotations

from typing import Final

from weekend_loop.status import Liveness
from weekend_loop.web.demo import DemoStep, DemoTask, DemoView, Outcome, Tone
from weekend_loop.web.theme import (
    ACCENT,
    FONT_STACK,
    HAIRLINE,
    INK,
    MUTED,
    PAGE,
    PRODUCT_NAME,
    SURFACE,
    escape,
)

DEMO_REFRESH_SECONDS: Final[int] = 5
DEMO_PATH: Final[str] = "/demo"
DEMO_BODY_PATH: Final[str] = "/demo/body"
DEMO_CONTAINER_ID: Final[str] = "demo"
PROCESS_PANE_ID: Final[str] = "demo-process"
PAGE_TITLE: Final[str] = PRODUCT_NAME
NO_RUN_TEXT: Final[str] = "Waiting for the first run."
NO_STEP_TEXT: Final[str] = "The run has logged no step yet."
NO_TASK_TEXT: Final[str] = "No issue has been read yet."
WAITING_BADGE: Final[tuple[str, Tone]] = ("waiting", Tone.MUTED)
LIVENESS_BADGES: Final[dict[Liveness, tuple[str, Tone]]] = {
    Liveness.ALIVE: ("live", Tone.POSITIVE),
    Liveness.FINISHED: ("finished", Tone.MUTED),
    Liveness.DEAD: ("stopped", Tone.NEGATIVE),
    Liveness.UNKNOWN: ("starting", Tone.ATTENTION),
}
TONE_COLOURS: Final[dict[Tone, tuple[str, str]]] = {
    Tone.POSITIVE: ("#E4F5F2", "#1D7466"),
    Tone.ACTIVE: ("#E8EEFF", ACCENT),
    Tone.ATTENTION: ("#FBF1DB", "#86600F"),
    Tone.NEGATIVE: ("#FCE7E1", "#B2361B"),
    Tone.MUTED: ("#F0F0F0", "#5C5C5C"),
}
TONE_RULES: Final[str] = "\n".join(
    f".demo-{tone.value} {{ background: {background}; color: {foreground}; }}"
    for tone, (background, foreground) in TONE_COLOURS.items()
)
DEMO_STYLE: Final[str] = f"""<style>
:root {{ color-scheme: light; }}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: {SURFACE};
  color: {INK};
  font-family: {FONT_STACK};
  line-height: 1.4;
}}
#{DEMO_CONTAINER_ID} {{
  display: flex;
  flex-direction: column;
  gap: 20px;
  height: 100vh;
  padding: 24px 32px;
}}
.demo-header {{
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}}
.demo-header h1 {{
  margin: 0;
  font-size: 30px;
  font-weight: 600;
  letter-spacing: -0.02em;
}}
.demo-run {{ color: {MUTED}; font-size: 13px; }}
.demo-state {{ display: flex; align-items: center; gap: 12px; color: {MUTED}; font-size: 15px; }}
.demo-columns {{
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 20px;
  flex: 1;
  min-height: 0;
}}
.demo-card {{
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: {PAGE};
  border: 1px solid {HAIRLINE};
  border-radius: 12px;
  padding: 20px 24px;
}}
.demo-card h2 {{
  margin: 0 0 12px 0;
  color: {MUTED};
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
.demo-scroll {{ flex: 1; min-height: 0; overflow-y: auto; }}
.demo-card table {{ width: 100%; border-collapse: collapse; font-size: 15px; }}
.demo-card th {{
  position: sticky;
  top: 0;
  background: {PAGE};
  color: {MUTED};
  font-size: 12px;
  font-weight: 400;
  text-align: left;
  padding: 0 12px 8px 0;
}}
.demo-card td {{ border-top: 1px solid {HAIRLINE}; padding: 9px 12px 9px 0; vertical-align: top; }}
.demo-time {{ color: {MUTED}; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.demo-issue {{ font-weight: 600; white-space: nowrap; }}
.demo-step {{ white-space: nowrap; }}
.demo-pill {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}}
{TONE_RULES}
.demo-detail {{ color: {MUTED}; font-size: 13px; margin-left: 8px; }}
.demo-reason {{ color: {MUTED}; font-size: 13px; }}
.demo-link {{ color: {ACCENT}; font-size: 13px; margin-left: 8px; text-decoration: none; }}
.demo-empty {{ color: {MUTED}; font-size: 15px; }}
.demo-dot {{ width: 8px; height: 8px; border-radius: 50%; background: currentColor; }}
.demo-pulsing .demo-dot {{ animation: demo-pulse 1.6s ease-in-out infinite; }}
@keyframes demo-pulse {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.25; }} }}
@media (max-width: 900px) {{
  #{DEMO_CONTAINER_ID} {{ height: auto; padding: 16px; }}
  .demo-columns {{ grid-template-columns: minmax(0, 1fr); }}
  .demo-scroll {{ max-height: 60vh; }}
}}
</style>"""
DEMO_SCRIPT: Final[str] = f"""<script>
const demoSource = "{DEMO_BODY_PATH}" + window.location.search;
function processPane() {{ return document.getElementById("{PROCESS_PANE_ID}"); }}
function pinnedToLatest(pane) {{
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 24;
}}
async function refreshDemo() {{
  const before = processPane();
  const pinned = before === null || pinnedToLatest(before);
  const offset = before === null ? 0 : before.scrollTop;
  const response = await fetch(demoSource, {{ cache: "no-store" }});
  if (!response.ok) {{ return; }}
  document.getElementById("{DEMO_CONTAINER_ID}").innerHTML = await response.text();
  const after = processPane();
  if (after !== null) {{ after.scrollTop = pinned ? after.scrollHeight : offset; }}
}}
const initialPane = processPane();
if (initialPane !== null) {{ initialPane.scrollTop = initialPane.scrollHeight; }}
setInterval(refreshDemo, {DEMO_REFRESH_SECONDS * 1000});
</script>"""


def pill(text: str, tone: Tone, pulsing: bool) -> str:
    dot = '<span class="demo-dot"></span>' if pulsing else ""
    pulse_class = " demo-pulsing" if pulsing else ""
    return f'<span class="demo-pill demo-{tone.value}{pulse_class}">{dot}{escape(text)}</span>'


def render_outcome(outcome: Outcome) -> str:
    detail = (
        f'<span class="demo-detail">{escape(outcome.detail)}</span>'
        if outcome.detail is not None
        else ""
    )
    if outcome.tone is Tone.PLAIN:
        return escape(outcome.text) + detail
    return pill(outcome.text, outcome.tone, False) + detail


def issue_cell(issue_number: int | None) -> str:
    return f"#{issue_number}" if issue_number is not None else ""


def render_step(step: DemoStep) -> str:
    return (
        f'<tr><td class="demo-time">{escape(step.time)}</td>'
        f'<td class="demo-issue">{issue_cell(step.issue_number)}</td>'
        f'<td class="demo-step">{escape(step.step)}</td>'
        f"<td>{render_outcome(step.outcome)}</td></tr>"
    )


def render_process(steps: list[DemoStep]) -> str:
    if not steps:
        body = f'<div class="demo-empty">{escape(NO_STEP_TEXT)}</div>'
    else:
        rows = "".join(render_step(step) for step in steps)
        body = (
            "<table><thead><tr><th>Time</th><th>Issue</th><th>Step</th><th>Outcome</th></tr>"
            f"</thead><tbody>{rows}</tbody></table>"
        )
    return (
        '<section class="demo-card"><h2>Process</h2>'
        f'<div class="demo-scroll" id="{PROCESS_PANE_ID}">{body}</div></section>'
    )


def render_task(task: DemoTask) -> str:
    reason = (
        f'<div class="demo-reason">{escape(task.outcome.detail)}</div>'
        if task.outcome.detail is not None
        else ""
    )
    link = (
        f'<a class="demo-link" href="{escape(task.link)}" target="_blank">open PR</a>'
        if task.link is not None
        else ""
    )
    return (
        f'<tr><td class="demo-issue">#{task.issue_number}</td>'
        f"<td>{escape(task.title)}{reason}</td>"
        f"<td>{pill(task.outcome.text, task.outcome.tone, False)}{link}</td></tr>"
    )


def render_tasks(tasks: list[DemoTask]) -> str:
    if not tasks:
        body = f'<div class="demo-empty">{escape(NO_TASK_TEXT)}</div>'
    else:
        rows = "".join(render_task(task) for task in tasks)
        body = (
            "<table><thead><tr><th>Issue</th><th>Title</th><th>Status</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    return (
        f'<section class="demo-card"><h2>Tasks</h2><div class="demo-scroll">{body}</div></section>'
    )


def render_state(badge: tuple[str, Tone], pulsing: bool, activity: str | None) -> str:
    text, tone = badge
    phrase = f"<span>{escape(activity)}</span>" if activity is not None else ""
    return f'<div class="demo-state">{phrase}{pill(text, tone, pulsing)}</div>'


def render_header(run_id: str | None, state: str) -> str:
    run = f'<div class="demo-run">{escape(run_id)}</div>' if run_id is not None else ""
    return (
        f'<header class="demo-header"><div><h1>{escape(PAGE_TITLE)}</h1>{run}</div>{state}</header>'
    )


def render_demo_body(view: DemoView | None) -> str:
    if view is None:
        return render_header(None, render_state(WAITING_BADGE, False, None)) + (
            f'<section class="demo-card"><div class="demo-empty">{escape(NO_RUN_TEXT)}</div>'
            "</section>"
        )
    state = render_state(
        LIVENESS_BADGES[view.liveness], view.liveness is Liveness.ALIVE, view.activity
    )
    return (
        render_header(view.run_id, state)
        + '<div class="demo-columns">'
        + render_process(view.steps)
        + render_tasks(view.tasks)
        + "</div>"
    )


def render_demo_page(view: DemoView | None) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(PAGE_TITLE)}</title>{DEMO_STYLE}</head><body>"
        f'<main id="{DEMO_CONTAINER_ID}">{render_demo_body(view)}</main>'
        f"{DEMO_SCRIPT}</body></html>"
    )
