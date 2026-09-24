from pocketchat.answers import FALLBACK_ANSWER, answer_to


def test_a_known_question_gets_its_prepared_answer() -> None:
    assert "Pocketchat" in answer_to("Who are you?")


def test_an_unknown_question_gets_the_fallback_answer() -> None:
    assert answer_to("What is the capital of France?") == FALLBACK_ANSWER
