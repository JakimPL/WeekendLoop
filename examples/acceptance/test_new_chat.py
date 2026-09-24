import json
from http import HTTPMethod, HTTPStatus

from pocketchat.chat import GREETING, Chat
from pocketchat.routes import Response, route


def texts_in(response: Response) -> list[str]:
    payload: dict[str, list[dict[str, str]]] = json.loads(response.body)
    return [message["text"] for message in payload["messages"]]


def ask(chat: Chat, question: str) -> Response:
    return route(HTTPMethod.POST, "/api/ask", json.dumps({"question": question}).encode(), chat)


def test_new_chat_leaves_only_the_greeting() -> None:
    chat = Chat()
    ask(chat, "Hello")
    response = route(HTTPMethod.POST, "/api/new-chat", b"", chat)
    assert response.status is HTTPStatus.OK
    assert texts_in(response) == [GREETING]
    assert texts_in(route(HTTPMethod.GET, "/api/messages", b"", chat)) == [GREETING]


def test_the_conversation_goes_on_after_a_new_chat() -> None:
    chat = Chat()
    ask(chat, "Hello")
    route(HTTPMethod.POST, "/api/new-chat", b"", chat)
    texts = texts_in(ask(chat, "Who are you?"))
    assert texts[:2] == [GREETING, "Who are you?"]
    assert len(texts) == 3
