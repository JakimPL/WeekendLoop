from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.unit.conftest import issue_payload, write_github_data, write_test_policy
from weekend_loop.models import BudgetPolicy, CheckOutcome, UsagePolicy, UsageReading, UsageWindow
from weekend_loop.policy import policy_at
from weekend_loop.preflight import (
    check_secret_file,
    check_settings_file,
    check_usage_credits,
    check_usage_headroom,
    check_weekly_reset,
    probe_outcome,
    run_preflight,
    status_of,
)

USAGE = UsagePolicy(
    seven_day_ceiling=0.95,
    seven_day_reserve=0.08,
    five_hour_ceiling=0.95,
    probe_usd=0.05,
    probe_timeout_seconds=120,
)


def build_reading(
    seven_day_utilization: float, seven_day_reset: datetime, is_using_overage: bool
) -> UsageReading:
    return UsageReading(
        five_hour=UsageWindow(utilization=0.1, resets_at=NOW + timedelta(hours=2)),
        seven_day=UsageWindow(utilization=seven_day_utilization, resets_at=seven_day_reset),
        status="allowed",
        is_using_overage=is_using_overage,
        observed_at=NOW,
    )


NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)


def build_budget(weekly_reset_at: datetime | None) -> BudgetPolicy:
    return BudgetPolicy(
        envelope_usd=15.0,
        per_task_usd=6.0,
        assessor_usd=0.5,
        max_tasks=3,
        weekly_reset_at=weekly_reset_at,
    )


def test_a_credential_readable_by_others_fails_the_check(tmp_path: Path) -> None:
    path = tmp_path / "token"
    path.write_text("secret")
    path.chmod(0o644)
    assert check_secret_file("token", path, True).outcome is CheckOutcome.FAILED
    path.chmod(0o600)
    assert check_secret_file("token", path, True).outcome is CheckOutcome.PASSED


def test_a_missing_or_empty_credential_fails_the_check(tmp_path: Path) -> None:
    assert check_secret_file("token", tmp_path / "absent", True).outcome is CheckOutcome.FAILED
    empty = tmp_path / "empty"
    empty.write_text("  ")
    empty.chmod(0o600)
    assert check_secret_file("token", empty, True).outcome is CheckOutcome.FAILED


def test_settings_must_be_readable_json(tmp_path: Path) -> None:
    broken = tmp_path / "settings.json"
    broken.write_text("{not json")
    assert check_settings_file("settings", broken, True).outcome is CheckOutcome.FAILED
    broken.write_text('{"permissions": {}}')
    assert check_settings_file("settings", broken, True).outcome is CheckOutcome.PASSED


def test_an_unrecorded_weekly_reset_leaves_the_run_to_the_live_reading() -> None:
    assert check_weekly_reset(build_budget(None), NOW).outcome is CheckOutcome.PASSED


def test_a_recorded_weekly_reset_that_has_passed_is_refused() -> None:
    passed_reset = check_weekly_reset(build_budget(NOW - timedelta(hours=1)), NOW)
    assert passed_reset.outcome is CheckOutcome.FAILED
    ahead = check_weekly_reset(build_budget(NOW + timedelta(hours=1)), NOW)
    assert ahead.outcome is CheckOutcome.PASSED


def test_a_run_drawing_on_usage_credits_is_refused() -> None:
    billed = build_reading(0.40, NOW + timedelta(days=2), True)
    assert check_usage_credits(billed, True).outcome is CheckOutcome.FAILED
    covered = build_reading(0.40, NOW + timedelta(days=2), False)
    assert check_usage_credits(covered, True).outcome is CheckOutcome.PASSED


def test_an_unread_allowance_leaves_the_credits_check_to_the_envelope() -> None:
    assert check_usage_credits(None, True).outcome is CheckOutcome.PASSED


def prepare_environment(tmp_path: Path, fake_binaries: Path, push: bool) -> Path:
    write_github_data(
        fake_binaries,
        issues=[issue_payload(1, "title", "body", [], [])],
        pull_requests=[],
        comments={},
        push=push,
    )
    return write_test_policy(tmp_path, NOW + timedelta(days=2))


def test_a_read_only_repository_clears_preflight(tmp_path: Path, fake_binaries: Path) -> None:
    policy = policy_at(prepare_environment(tmp_path, fake_binaries, push=False))
    report = run_preflight(policy, "dryrun", NOW, None)
    assert report.clear_to_run, [check for check in report.checks if not check.required]


def test_a_dry_run_token_that_can_push_blocks_the_run(tmp_path: Path, fake_binaries: Path) -> None:
    policy = policy_at(prepare_environment(tmp_path, fake_binaries, push=True))
    report = run_preflight(policy, "dryrun", NOW, None)
    assert not report.clear_to_run
    failures = [
        check.name
        for check in report.checks
        if check.outcome is CheckOutcome.FAILED and check.required
    ]
    assert failures == ["github access to example-org/example-repo"]


def test_execute_mode_refuses_a_run_the_subscription_no_longer_covers(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy = policy_at(prepare_environment(tmp_path, fake_binaries, push=True))
    billed = build_reading(0.40, NOW + timedelta(days=2), True)
    report = run_preflight(policy, "demo", NOW, billed)
    failures = {
        check.name
        for check in report.checks
        if check.outcome is CheckOutcome.FAILED and check.required
    }
    assert "usage credits" in failures
    assert not report.clear_to_run


def test_execute_mode_clears_a_run_the_subscription_covers(
    tmp_path: Path, fake_binaries: Path
) -> None:
    policy = policy_at(prepare_environment(tmp_path, fake_binaries, push=True))
    covered = build_reading(0.40, NOW + timedelta(days=2), False)
    report = run_preflight(policy, "demo", NOW, covered)
    assert report.clear_to_run, [check for check in report.checks if not check.outcome]


def test_a_weekly_allowance_without_room_for_a_task_refuses_the_run() -> None:
    reading = build_reading(0.93, NOW + timedelta(days=2), False)
    assert check_usage_headroom(reading, USAGE, NOW).outcome is CheckOutcome.FAILED


def test_a_weekly_allowance_with_room_clears_the_run() -> None:
    reading = build_reading(0.40, NOW + timedelta(days=2), False)
    check = check_usage_headroom(reading, USAGE, NOW)
    assert check.outcome is CheckOutcome.PASSED
    assert "40%" in check.detail


def test_an_allowance_nobody_could_read_clears_the_run() -> None:
    assert check_usage_headroom(None, USAGE, NOW).outcome is CheckOutcome.PASSED


def test_an_allowance_reading_from_a_window_that_reset_clears_the_run() -> None:
    stale = build_reading(0.99, NOW - timedelta(minutes=1), False)
    assert check_usage_headroom(stale, USAGE, NOW).outcome is CheckOutcome.PASSED


def test_the_write_probe_reads_the_status_from_the_answer_body() -> None:
    error = subprocess.CalledProcessError(
        1, ["gh"], output='{"message": "probe", "status": "403"}', stderr=""
    )
    assert status_of(error) == "403"


def test_the_write_probe_reads_the_status_from_the_message_when_the_body_is_empty() -> None:
    error = subprocess.CalledProcessError(
        1, ["gh"], output="", stderr="gh: Object does not exist (HTTP 422)"
    )
    assert status_of(error) == "422"
    silent = subprocess.CalledProcessError(1, ["gh"], output="", stderr="")
    assert status_of(silent) == "unknown"


def test_a_probe_answer_names_what_the_token_can_do() -> None:
    assert probe_outcome("422") is True
    assert probe_outcome("403") is False
    assert probe_outcome("404") is None
