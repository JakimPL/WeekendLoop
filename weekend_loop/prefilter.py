from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from weekend_loop.models import (
    EligibilityDecision,
    EligibilityPolicy,
    IneligibilityReason,
    Issue,
    LabelPolicy,
    SpecSignals,
)

TEMPLATE_SECTIONS: Final[tuple[str, ...]] = ("Business requirement", "Goal", "Scope")
ACCEPTANCE_SECTIONS: Final[tuple[str, ...]] = ("Acceptance criteria", "Definition of done")
CHECKBOX_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*[-*]\s*\[[ xX]\]", re.MULTILINE)
BACKTICKED_PATTERN: Final[re.Pattern[str]] = re.compile(r"`([^`\n]+)`")
PATH_SUFFIXES: Final[tuple[str, ...]] = (
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".cfg",
    ".ini",
    ".sh",
    ".sql",
    ".ipynb",
)


def section_present(body: str, name: str) -> bool:
    pattern = re.compile(rf"^#{{1,4}}\s*{re.escape(name)}\b", re.IGNORECASE | re.MULTILINE)
    return pattern.search(body) is not None


def present_sections(body: str) -> list[str]:
    candidates = TEMPLATE_SECTIONS + ACCEPTANCE_SECTIONS
    return [name for name in candidates if section_present(body, name)]


def looks_like_path(token: str) -> bool:
    candidate = token.strip()
    if not candidate or " " in candidate:
        return False
    return candidate.endswith(PATH_SUFFIXES) or ("/" in candidate and not candidate.startswith("-"))


def referenced_paths(body: str) -> list[str]:
    found: list[str] = []
    for token in BACKTICKED_PATTERN.findall(body):
        candidate = token.strip().rstrip(".,:;")
        if looks_like_path(candidate) and candidate not in found:
            found.append(candidate)
    return found


def resolve_referenced_paths(paths: list[str], repository_root: Path) -> list[str]:
    return [path for path in paths if (repository_root / path).exists()]


def has_acceptance_criteria(body: str) -> bool:
    named = any(section_present(body, name) for name in ACCEPTANCE_SECTIONS)
    return named or CHECKBOX_PATTERN.search(body) is not None


def spec_signals(issue: Issue, repository_root: Path) -> SpecSignals:
    sections = present_sections(issue.body)
    paths = referenced_paths(issue.body)
    return SpecSignals(
        body_length=len(issue.body.strip()),
        sections_present=sections,
        has_template=all(name in sections for name in TEMPLATE_SECTIONS),
        referenced_paths=paths,
        resolved_paths=resolve_referenced_paths(paths, repository_root),
        has_acceptance_criteria=has_acceptance_criteria(issue.body),
        blocked_by=issue.blocked_by,
    )


def evaluate_eligibility(
    issue: Issue,
    eligibility: EligibilityPolicy,
    labels: LabelPolicy,
    owner_login: str,
    now: datetime,
) -> EligibilityDecision:
    reasons: list[IneligibilityReason] = []
    if issue.assignees and owner_login not in issue.assignees:
        reasons.append(IneligibilityReason.ASSIGNED_TO_SOMEONE_ELSE)
    if labels.never in issue.labels:
        reasons.append(IneligibilityReason.NEVER_LABEL)
    if len(issue.body.strip()) < eligibility.minimum_body_length:
        reasons.append(IneligibilityReason.BODY_TOO_SHORT)
    if re.search(eligibility.excluded_title_pattern, issue.title, re.IGNORECASE):
        reasons.append(IneligibilityReason.TITLE_PATTERN)
    if issue.open_linked_pull_requests:
        reasons.append(IneligibilityReason.OPEN_LINKED_PULL_REQUEST)
    if recently_touched_by_someone_else(issue, eligibility.foreign_activity_window_hours, now):
        reasons.append(IneligibilityReason.RECENT_FOREIGN_ACTIVITY)
    return EligibilityDecision(eligible=not reasons, reasons=reasons)


def recently_touched_by_someone_else(issue: Issue, window_hours: int, now: datetime) -> bool:
    if issue.last_foreign_activity_at is None:
        return False
    return now - issue.last_foreign_activity_at < timedelta(hours=window_hours)
