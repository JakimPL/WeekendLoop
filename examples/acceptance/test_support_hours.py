from pocketchat.answers import FALLBACK_ANSWER, answer_to

QUESTIONS = (
    "When is support available?",
    "What are your opening hours?",
)


def test_questions_about_support_get_the_hours() -> None:
    for question in QUESTIONS:
        answer = answer_to(question)
        assert answer != FALLBACK_ANSWER, question
        assert "Monday" in answer and "17:00" in answer, question


def test_the_other_answers_stay_as_they_were() -> None:
    assert "Pocketchat" in answer_to("Who are you?")
