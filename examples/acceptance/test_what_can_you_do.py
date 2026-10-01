from pocketchat.answers import FALLBACK_ANSWER, answer_to


def test_asking_what_it_can_do_lists_the_prepared_topics() -> None:
    answer = answer_to("What can you do?").lower()
    assert answer != FALLBACK_ANSWER.lower()
    assert "hello" in answer or "greet" in answer
    for topic in ("joke", "weekend"):
        assert topic in answer


def test_the_other_answers_stay_as_they_were() -> None:
    assert "Pocketchat" in answer_to("Who are you?")
