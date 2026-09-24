from typing import Final

PREPARED_ANSWERS: Final[dict[str, str]] = {
    "hello": "Hello! What would you like to know?",
    "who are you": (
        "I'm Pocketchat, a chat assistant. This page is a mockup, "
        "so my answers are written in advance."
    ),
    "weekend": "Weekends are for resting. Some agents like to work through them, though.",
    "joke": "Why did the developer go broke? Because they used up all their cache.",
}
FALLBACK_ANSWER: Final[str] = (
    "Good question! I'm only a mockup for now, so I know just a few prepared answers. "
    "Try saying hello or asking who I am."
)


def answer_to(question: str) -> str:
    lowered = question.lower()
    for keyword, answer in PREPARED_ANSWERS.items():
        if keyword in lowered:
            return answer
    return FALLBACK_ANSWER
