from __future__ import annotations

import html
from typing import Final
from urllib.parse import urlsplit

from weekend_loop.local_github.paths import UNREACHABLE_HOST_SUFFIX
from weekend_loop.models import TaskStatus
from weekend_loop.status import Liveness

PRODUCT_NAME: Final[str] = "Weekend Loop"
INK: Final[str] = "#1A1A1A"
MUTED: Final[str] = "#707070"
PAGE: Final[str] = "#FFFFFF"
SURFACE: Final[str] = "#F7F7F7"
HAIRLINE: Final[str] = "#E5E5E5"
ACCENT: Final[str] = "#2563EB"
POSITIVE: Final[str] = "#10897B"
CAUTION: Final[str] = "#C08A1E"
ALERT: Final[str] = "#C4381B"
FONT_STACK: Final[str] = (
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, Arial, sans-serif"
)
MONOSPACE_STACK: Final[str] = "ui-monospace, 'DejaVu Sans Mono', monospace"
BUDGET_ALERT_FRACTION: Final[float] = 0.9
STALE_HEARTBEAT_SECONDS: Final[float] = 900.0
SECONDS_PER_MINUTE: Final[float] = 60.0
STATUS_COLOURS: Final[dict[str, str]] = {
    TaskStatus.REVIEW.value: POSITIVE,
    TaskStatus.NEEDS_INPUT.value: CAUTION,
    TaskStatus.UNFINISHED.value: CAUTION,
    TaskStatus.ABANDONED.value: ALERT,
    TaskStatus.WORKING.value: ACCENT,
}
LIVENESS_COLOURS: Final[dict[Liveness, str]] = {
    Liveness.ALIVE: POSITIVE,
    Liveness.DEAD: ALERT,
    Liveness.FINISHED: MUTED,
    Liveness.UNKNOWN: CAUTION,
}
PAGE_STYLE: Final[str] = f"""<style>
body {{ margin: 0; padding: 24px; background: {PAGE}; }}
.weekend-panel {{
  font-family: {FONT_STACK};
  color: {INK};
  background: {PAGE};
  line-height: 1.5;
}}
.weekend-nav {{ margin: 0 0 16px 0; font-family: {FONT_STACK}; }}
.weekend-nav a {{
  color: {ACCENT};
  margin-right: 16px;
  text-decoration: none;
  font-weight: 600;
}}
.weekend-banner {{
  background: {ACCENT};
  color: {PAGE};
  border-radius: 8px;
  padding: 24px;
}}
.weekend-banner h1 {{
  font-weight: 600;
  font-size: 32px;
  line-height: 1.2;
  letter-spacing: -0.02em;
  margin: 0 0 8px 0;
}}
.weekend-banner .weekend-meta {{ font-weight: 300; font-size: 16px; opacity: 0.85; }}
.weekend-wordmark {{ font-weight: 600; font-size: 14px; opacity: 0.75; margin-bottom: 8px; }}
.weekend-card {{
  background: {SURFACE};
  border: 1px solid {HAIRLINE};
  border-radius: 8px;
  padding: 16px;
  margin-top: 16px;
}}
.weekend-label {{ color: {MUTED}; font-size: 12px; text-transform: uppercase; }}
.weekend-track {{ background: {HAIRLINE}; border-radius: 8px; height: 16px; margin-top: 8px; }}
.weekend-fill {{ height: 16px; border-radius: 8px; }}
.weekend-chip {{
  display: inline-block;
  border-radius: 8px;
  padding: 4px 12px;
  margin: 4px 8px 0 0;
  font-size: 14px;
  color: {PAGE};
}}
.weekend-note {{ font-size: 14px; color: {INK}; margin: 4px 0; }}
.weekend-transcript {{
  font-family: {MONOSPACE_STACK};
  font-size: 13px;
  white-space: pre-wrap;
  word-break: break-word;
  background: {PAGE};
  border: 1px solid {HAIRLINE};
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 8px;
}}
.weekend-panel table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
.weekend-panel th, .weekend-panel td {{ text-align: left; padding: 6px 12px 6px 0; }}
.weekend-panel textarea {{
  width: 100%;
  font-family: inherit;
  font-size: 14px;
  padding: 8px;
  border: 1px solid {HAIRLINE};
  border-radius: 8px;
}}
.weekend-panel button {{
  background: {ACCENT};
  color: {PAGE};
  border: 0;
  border-radius: 8px;
  padding: 8px 16px;
  font-family: inherit;
  font-size: 14px;
  cursor: pointer;
  margin-top: 8px;
}}
.weekend-source {{ color: {MUTED}; font-size: 12px; }}
.weekend-digest {{ white-space: pre-wrap; font-size: 13px; }}
</style>"""


LINKABLE_SCHEMES: Final[tuple[str, ...]] = ("https://", "http://")


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def browsable(url: str | None) -> str | None:
    if url is None or not url.startswith(LINKABLE_SCHEMES):
        return None
    host = urlsplit(url).hostname or ""
    return None if host.endswith(UNREACHABLE_HOST_SUFFIX) else url
