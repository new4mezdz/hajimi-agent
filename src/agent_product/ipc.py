from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from fastapi import FastAPI

from agent_product.main import app

PROTOCOL_VERSION = 1


@dataclass(frozen=True, slots=True)
class IpcRequest:
    id: str
    method: str
    path: str
    headers: Mapping[str, str]
    body: str | None = None

    @classmethod
    def from_message(cls, message: Mapping[str, Any]) -> IpcRequest:
        request_id = message.get("id")
        method = message.get("method")
        path = message.get("path")
        headers = message.get("headers", {})
        body = message.get("body")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("IPC request id must be a non-empty string")
        if not isinstance(method, str) or not method:
            raise ValueError("IPC request method must be a non-empty string")
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("IPC request path must start with /")
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in headers.items()
        ):
            raise ValueError("IPC request headers must be a string map")
        if body is not None and not isinstance(body, str):
            raise ValueError("IPC request body must be a string or null")
        return cls(
            id=request_id,
            method=method.upper(),
            path=path,
            headers=headers,
            body=body,
        )


Emit = Callable[[dict[str, Any]], None]


def _response_headers(headers: list[tuple[bytes, bytes]]) -> list[list[str]]:
    return [[name.decode("latin-1"), value.decode("latin-1")] for name, value in headers]


async def dispatch_asgi_request(
    application: FastAPI,
    request: IpcRequest,
    emit: Emit,
) -> None:
    """Dispatch one desktop IPC request directly into the ASGI application."""
    parsed = urlsplit(request.path)
    body = request.body.encode("utf-8") if request.body is not None else b""
    headers = [
        (name.casefold().encode("latin-1"), value.encode("latin-1"))
        for name, value in request.headers.items()
    ]
    if not any(name == b"host" for name, _ in headers):
        headers.append((b"host", b"agent.local"))
    if body and not any(name == b"content-length" for name, _ in headers):
        headers.append((b"content-length", str(len(body)).encode("ascii")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": request.method,
        "scheme": "ipc",
        "path": unquote(parsed.path),
        "raw_path": parsed.path.encode("utf-8"),
        "query_string": parsed.query.encode("utf-8"),
        "root_path": "",
        "headers": headers,
        "client": ("desktop-ipc", 0),
        "server": ("agent.local", None),
        "state": {},
    }

    body_sent = False
    disconnected = asyncio.Event()
    response_started = False
    response_ended = False

    async def receive() -> dict[str, Any]:
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_ended
        message_type = message["type"]
        if message_type == "http.response.start":
            response_started = True
            emit(
                {
                    "id": request.id,
                    "type": "response_start",
                    "status": message["status"],
                    "headers": _response_headers(message.get("headers", [])),
                }
            )
            return
        if message_type != "http.response.body":
            return
        chunk = message.get("body", b"")
        if chunk:
            emit(
                {
                    "id": request.id,
                    "type": "response_chunk",
                    "data": base64.b64encode(chunk).decode("ascii"),
                }
            )
        if not message.get("more_body", False):
            response_ended = True
            emit({"id": request.id, "type": "response_end"})

    try:
        await application(scope, receive, send)
        if not response_started:
            raise RuntimeError("ASGI application returned without starting a response")
        if not response_ended:
            emit({"id": request.id, "type": "response_end"})
    except asyncio.CancelledError:
        disconnected.set()
        emit({"id": request.id, "type": "cancelled"})
        raise
    except Exception as exc:
        disconnected.set()
        emit(
            {
                "id": request.id,
                "type": "error",
                "message": f"Agent IPC request failed: {exc}",
            }
        )


class IpcServer:
    def __init__(self, application: FastAPI, emit: Emit) -> None:
        self.application = application
        self.emit = emit
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def handle_message(self, message: Mapping[str, Any]) -> None:
        message_type = message.get("type")
        request_id = message.get("id")
        if message_type == "cancel":
            if isinstance(request_id, str) and (task := self.tasks.get(request_id)):
                task.cancel()
            return
        if message_type != "request":
            raise ValueError("IPC message type must be request or cancel")

        request = IpcRequest.from_message(message)
        if request.id in self.tasks:
            raise ValueError(f"IPC request id is already active: {request.id}")
        task = asyncio.create_task(
            dispatch_asgi_request(self.application, request, self.emit),
            name=f"agent-ipc-{request.id}",
        )
        self.tasks[request.id] = task
        task.add_done_callback(lambda completed, request_id=request.id: self._complete(request_id))

    def _complete(self, request_id: str) -> None:
        task = self.tasks.pop(request_id, None)
        if task is not None and not task.cancelled():
            task.exception()

    async def close(self) -> None:
        active = list(self.tasks.values())
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)


def _emit_stdout(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


async def serve(
    application: FastAPI = app,
    *,
    readline: Callable[[], Awaitable[bytes]] | None = None,
    emit: Emit = _emit_stdout,
) -> None:
    read_line = readline or (lambda: asyncio.to_thread(sys.stdin.buffer.readline))
    server = IpcServer(application, emit)
    async with application.router.lifespan_context(application):
        emit({"type": "ready", "protocolVersion": PROTOCOL_VERSION})
        try:
            while line := await read_line():
                message: Any = None
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("IPC message must be a JSON object")
                    server.handle_message(message)
                except (json.JSONDecodeError, ValueError) as exc:
                    request_id = message.get("id") if isinstance(message, dict) else None
                    emit(
                        {
                            "id": request_id,
                            "type": "error",
                            "message": f"Invalid Agent IPC message: {exc}",
                        }
                    )
        finally:
            await server.close()


def main() -> None:
    asyncio.run(serve())


if __name__ == "__main__":
    main()
