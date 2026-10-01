from __future__ import annotations

from pathlib import Path

from weekend_loop.commands import command_environment
from weekend_loop.gate import (
    binary_files,
    changed_python_files,
    diff_line_count,
    evaluate_gate,
    forbidden_matches,
    parse_numstat,
    path_matches,
    render_command,
    run_gate_commands,
    secret_matches,
)
from weekend_loop.models import ChangedFile, CommandResult

NUMSTAT = "12\t3\tlogbook/records.py\n0\t0\tREADME.md\n-\t-\tdocs/plot.png\n"
FORBIDDEN = [".github/**", "pyproject.toml", "uv.lock", "config/**", "**/.env"]


def build_files() -> list[ChangedFile]:
    return parse_numstat(NUMSTAT)


def build_text_files() -> list[ChangedFile]:
    return parse_numstat("12\t3\tlogbook/records.py\n0\t0\tREADME.md\n")


def passing_command() -> CommandResult:
    return CommandResult(command="pytest", exit_code=0, duration_seconds=1.0, output_tail="ok")


def failing_command() -> CommandResult:
    return CommandResult(command="pytest", exit_code=1, duration_seconds=1.0, output_tail="boom")


def test_numstat_reports_line_counts_and_binary_files() -> None:
    files = build_files()
    assert [changed.path for changed in files] == [
        "logbook/records.py",
        "README.md",
        "docs/plot.png",
    ]
    assert diff_line_count(files) == 15
    assert changed_python_files(files) == ["logbook/records.py"]
    assert binary_files(files) == ["docs/plot.png"]


def test_a_rename_is_recorded_under_its_new_path() -> None:
    files = parse_numstat("1\t1\tlogbook/{old.py => new.py}\n")
    assert files[0].path == "logbook/old.py => new.py".split(" => ")[-1]


def test_policy_globs_reach_directories_and_nested_names() -> None:
    assert path_matches(".github/workflows/tests.yml", ".github/**")
    assert path_matches(".github", ".github/**")
    assert path_matches("pyproject.toml", "pyproject.toml")
    assert path_matches("packages/inner/.env", "**/.env")
    assert path_matches(".env", "**/.env")
    assert not path_matches("logbook/records.py", ".github/**")


def test_forbidden_paths_are_reported_by_name() -> None:
    files = parse_numstat("1\t0\t.github/workflows/tests.yml\n2\t0\tlogbook/records.py\n")
    assert forbidden_matches(files, FORBIDDEN) == [".github/workflows/tests.yml"]


def test_the_scan_names_the_kind_of_credential_it_found() -> None:
    diff = "+token = 'ghp_" + "a" * 36 + "'\n+key = 'AKIA" + "B" * 16 + "'\n"
    assert secret_matches(diff) == ["aws access key", "github token"]
    assert secret_matches("+speed = None\n") == []


def test_a_command_without_python_files_is_dropped(tmp_path: Path) -> None:
    assert render_command("pytest tests", []) == "pytest tests"
    assert render_command("ruff check {changed_python_files}", []) is None
    assert render_command("ruff check {changed_python_files}", ["a b.py"]) == "ruff check 'a b.py'"


def test_gate_commands_stop_at_the_first_failure(tmp_path: Path) -> None:
    (tmp_path / "good.py").write_text("value = 1\n")
    (tmp_path / "bad.py").write_text("def broken(\n")
    results = run_gate_commands(
        [
            "python3 -m compileall -q {changed_python_files}",
            "python3 -c pass",
        ],
        ["bad.py"],
        tmp_path,
        command_environment({}),
        None,
    )
    assert len(results) == 1
    assert results[0].exit_code != 0


def test_a_clean_small_diff_with_green_commands_passes() -> None:
    gate = evaluate_gate(
        build_text_files(), "+speed = None\n", [passing_command()], 1, 400, FORBIDDEN
    )
    assert gate.passed
    assert gate.diff_lines == 15


def test_every_boundary_breach_fails_the_gate() -> None:
    files = build_text_files()
    diff = "+speed = None\n"
    assert not evaluate_gate(files, diff, [failing_command()], 1, 400, FORBIDDEN).passed
    assert not evaluate_gate(files, diff, [passing_command()], 1, 5, FORBIDDEN).passed
    assert not evaluate_gate(files, diff, [passing_command()], 0, 400, FORBIDDEN).passed
    assert not evaluate_gate([], diff, [passing_command()], 1, 400, FORBIDDEN).passed
    secret = "+token = 'ghp_" + "a" * 36 + "'\n"
    assert not evaluate_gate(files, secret, [passing_command()], 1, 400, FORBIDDEN).passed
    forbidden = parse_numstat("1\t0\t.github/workflows/tests.yml\n")
    assert not evaluate_gate(forbidden, diff, [passing_command()], 1, 400, FORBIDDEN).passed


def test_a_binary_file_fails_the_gate() -> None:
    binaries = parse_numstat("-\t-\tdocs/plot.png\n")
    gate = evaluate_gate(binaries, "", [passing_command()], 1, 400, FORBIDDEN)
    assert not gate.passed
    assert gate.binary_files == ["docs/plot.png"]
