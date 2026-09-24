from http import HTTPMethod
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final

from pocketchat.chat import Chat
from pocketchat.routes import Response, route

HOST: Final[str] = "localhost"
PORT: Final[int] = 8000


class ChatServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], chat: Chat) -> None:
        super().__init__(address, ChatRequestHandler)
        self.chat = chat


class ChatRequestHandler(BaseHTTPRequestHandler):
    server: ChatServer

    def do_GET(self) -> None:
        self.send(route(HTTPMethod.GET, self.path, b"", self.server.chat))

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.send(route(HTTPMethod.POST, self.path, self.rfile.read(length), self.server.chat))

    def send(self, response: Response) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)


def main() -> None:
    server = ChatServer((HOST, PORT), Chat())
    print(f"Pocketchat is running at http://{HOST}:{PORT} (press Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
