import json
from http import HTTPMethod, HTTPStatus

import pytest

from pocketchat.chat import GREETING, Chat
from pocketchat.routes import Response, route


def texts_in(response: Response) -> list[str]:
    return [message["text"] for message in json.loads(response.body)["messages"]]


def ask(chat: Chat, question: str) -> Response:
    return route(HTTPMethod.POST, "/api/ask", json.dumps({"question": question}).encode(), chat)


def test_the_page_is_served() -> None:
    response = route(HTTPMethod.GET, "/", b"", Chat())
    assert response.status is HTTPStatus.OK
    assert b"Pocketchat" in response.body


def test_asking_returns_the_whole_conversation() -> None:
    texts = texts_in(ask(Chat(), "Hello"))
    assert texts[:2] == [GREETING, "Hello"]
    assert len(texts) == 3


def test_an_unknown_address_is_not_found() -> None:
    assert route(HTTPMethod.GET, "/secrets", b"", Chat()).status is HTTPStatus.NOT_FOUND


@pytest.mark.skip(reason="New chat is not finished yet")
def test_new_chat_keeps_only_the_greeting() -> None:
    chat = Chat()
    ask(chat, "Hello")
    response = route(HTTPMethod.POST, "/api/new-chat", b"", chat)
    assert texts_in(response) == [GREETING]
