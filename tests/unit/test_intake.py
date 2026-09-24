from pathlib import Path
from typing import Final

from tests.unit.test_local_board import FIRST_ISSUE, seeded
from weekend_loop.backends import reader_for, writer_for
from weekend_loop.board import LocalBoard, now_utc, open_board
from weekend_loop.briefing import answers_of, notes_of, question_key, read_briefing
from weekend_loop.github import signed
from weekend_loop.intake import ingest_answers, replied_issues
from weekend_loop.models import BoardComment, Intake, IssueIntake, Policy, RepoTarget
from weekend_loop.policy import policy_at, repo_target
from weekend_loop.questions import PREPARE_LEAD, render_question_comment

QUESTIONS: Final[list[str]] = ["Which unit does the feed use?", "Round or truncate?"]
ISSUE_LIMIT: Final[int] = 100


def asked(tmp_path: Path, label: bool) -> tuple[Policy, RepoTarget, LocalBoard]:
    policy = policy_at(seeded(tmp_path))
    repo = repo_target(policy, "demo")
    writer = writer_for(repo, policy.state_dir, policy.identity, policy.labels.namespace)
    body = render_question_comment(PREPARE_LEAD, QUESTIONS)
    writer.comment_on_issue(FIRST_ISSUE, signed(body, policy.identity.comment_footer, "run-7"))
    if label:
        writer.add_labels(FIRST_ISSUE, [policy.labels.needs_input])
    return policy, repo, open_board(policy.state_dir, repo.slug)


def reply(board: LocalBoard, author: str, body: str) -> None:
    board.append_comment(FIRST_ISSUE, BoardComment(author=author, created_at=now_utc(), body=body))


def operator(board: LocalBoard) -> str:
    return board.read_index().viewer_login


def ingest(policy: Policy, repo: RepoTarget) -> Intake:
    return ingest_answers(
        policy.state_dir,
        "demo",
        reader_for(repo, policy.state_dir),
        policy.identity,
        policy.labels.needs_input,
        ISSUE_LIMIT,
    )


def test_a_numbered_reply_on_the_issue_files_an_answer_to_each_question(tmp_path: Path) -> None:
    policy, repo, board = asked(tmp_path, True)
    reply(board, operator(board), "1. Knots.\n2. Round, half up.")

    intake = ingest(policy, repo)

    assert intake.issues == [IssueIntake(issue_number=FIRST_ISSUE, replies=1, answers=2, notes=0)]
    standing = answers_of(read_briefing(policy.state_dir, "demo"), FIRST_ISSUE)
    assert standing[question_key(QUESTIONS[0])].text == "Knots."
    assert standing[question_key(QUESTIONS[1])].text == "Round, half up."


def test_reading_the_same_thread_again_files_nothing_new(tmp_path: Path) -> None:
    policy, repo, board = asked(tmp_path, True)
    reply(board, operator(board), "1. Knots.\n\nLeave the CLI alone.")
    ingest(policy, repo)
    before = read_briefing(policy.state_dir, "demo")

    intake = ingest(policy, repo)

    assert intake.issues == [IssueIntake(issue_number=FIRST_ISSUE, replies=1, answers=0, notes=0)]
    assert read_briefing(policy.state_dir, "demo") == before


def test_prose_that_settles_no_question_is_kept_as_a_note_and_marks_the_issue(
    tmp_path: Path,
) -> None:
    policy, repo, board = asked(tmp_path, True)
    reply(board, operator(board), "Use knots and round half up.")

    intake = ingest(policy, repo)

    briefing = read_briefing(policy.state_dir, "demo")
    assert answers_of(briefing, FIRST_ISSUE) == {}
    assert [note.text for note in notes_of(briefing, FIRST_ISSUE)] == [
        "Use knots and round half up."
    ]
    assert replied_issues(intake) == {FIRST_ISSUE}


def test_a_colleagues_reply_is_never_taken_as_the_operators_guidance(tmp_path: Path) -> None:
    policy, repo, board = asked(tmp_path, True)
    reply(board, "colleague", "1. Furlongs.")

    assert ingest(policy, repo).issues == []
    assert read_briefing(policy.state_dir, "demo").answers == []


def test_an_issue_the_agent_is_not_waiting_on_is_not_read(tmp_path: Path) -> None:
    policy, repo, board = asked(tmp_path, False)
    reply(board, operator(board), "1. Knots.")

    assert ingest(policy, repo).issues == []
