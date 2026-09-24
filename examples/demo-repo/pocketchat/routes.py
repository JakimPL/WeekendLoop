import json
from dataclasses import asdict, dataclass
from http import HTTPMethod, HTTPStatus
from pathlib import Path
from typing import Final

from pocketchat.chat import Chat

STATIC_DIRECTORY: Final[Path] = Path(__file__).parent / "static"
JSON_TYPE: Final[str] = "application/json"
TEXT_TYPE: Final[str] = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class StaticFile:
    name: str
    content_type: str


STATIC_FILES: Final[dict[str, StaticFile]] = {
    "/": StaticFile("index.html", "text/html; charset=utf-8"),
    "/app.js": StaticFile("app.js", "text/javascript; charset=utf-8"),
    "/style.css": StaticFile("style.css", "text/css; charset=utf-8"),
}


@dataclass(frozen=True)
class Response:
    status: HTTPStatus
    content_type: str
    body: bytes


def route(method: HTTPMethod, path: str, body: bytes, chat: Chat) -> Response:
    match method, path:
        case HTTPMethod.GET, "/api/messages":
            return messages_of(chat)
        case HTTPMethod.POST, "/api/ask":
            chat.ask(question_in(body))
            return messages_of(chat)
        case HTTPMethod.GET, _ if path in STATIC_FILES:
            return static_file(STATIC_FILES[path])
    return Response(HTTPStatus.NOT_FOUND, TEXT_TYPE, b"Not found")


def messages_of(chat: Chat) -> Response:
    payload = {"messages": [asdict(message) for message in chat.messages]}
    return Response(HTTPStatus.OK, JSON_TYPE, json.dumps(payload).encode())


def question_in(body: bytes) -> str:
    payload: dict[str, str] = json.loads(body)
    return payload["question"].strip()


def static_file(file: StaticFile) -> Response:
    return Response(HTTPStatus.OK, file.content_type, (STATIC_DIRECTORY / file.name).read_bytes())
