from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pocketchat.answers import answer_to

GREETING: Final[str] = "Hi, I'm Pocketchat. Ask me anything!"


class Author(StrEnum):
    PERSON = "person"
    POCKETCHAT = "pocketchat"


@dataclass(frozen=True)
class Message:
    author: Author
    text: str


class Chat:
    def __init__(self) -> None:
        self.messages: list[Message] = [Message(Author.POCKETCHAT, GREETING)]

    def ask(self, question: str) -> None:
        self.messages.append(Message(Author.PERSON, question))
        self.messages.append(Message(Author.POCKETCHAT, answer_to(question)))
