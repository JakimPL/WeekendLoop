import json
from http import HTTPMethod, HTTPStatus

from pocketchat.chat import Chat
from pocketchat.routes import route


def stats_of(chat: Chat) -> dict[str, int]:
    response = route(HTTPMethod.GET, "/api/stats", b"", chat)
    assert response.status is HTTPStatus.OK
    payload: dict[str, int] = json.loads(response.body)
    return payload


def test_a_fresh_chat_has_not_started_over() -> None:
    assert stats_of(Chat()) == {"new_chats": 0}


def test_each_new_chat_counts_once_and_questions_do_not() -> None:
    chat = Chat()
    route(HTTPMethod.POST, "/api/ask", json.dumps({"question": "Hello"}).encode(), chat)
    route(HTTPMethod.POST, "/api/new-chat", b"", chat)
    route(HTTPMethod.POST, "/api/ask", json.dumps({"question": "Who are you?"}).encode(), chat)
    route(HTTPMethod.POST, "/api/new-chat", b"", chat)
    assert stats_of(chat) == {"new_chats": 2}
