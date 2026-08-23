import base64
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.ipc import IpcRequest, dispatch_asgi_request
from agent_product.main import create_app


@pytest.mark.asyncio
async def test_ipc_dispatches_request_without_tcp(tmp_path: Path) -> None:
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'ipc.db').as_posix()}",
        knowledge_dir=str(tmp_path / "knowledge"),
    )
    app = create_app(settings=settings, model=TestModel(custom_output_text="Test response"))
    events: list[dict[str, object]] = []

    async with app.router.lifespan_context(app):
        await dispatch_asgi_request(
            app,
            IpcRequest(
                id="health-1",
                method="GET",
                path="/health/ready",
                headers={},
            ),
            events.append,
        )

    assert events[0]["type"] == "response_start"
    assert events[0]["status"] == 200
    body = b"".join(
        base64.b64decode(str(event["data"]))
        for event in events
        if event["type"] == "response_chunk"
    )
    assert json.loads(body) == {"status": "ready"}
    assert events[-1]["type"] == "response_end"


@pytest.mark.asyncio
async def test_ipc_preserves_streaming_chunks() -> None:
    stream_app = FastAPI()

    @stream_app.get("/stream")
    async def stream() -> StreamingResponse:
        async def chunks():
            yield b"first\n"
            yield b"second\n"

        return StreamingResponse(chunks(), media_type="text/plain")

    events: list[dict[str, object]] = []
    await dispatch_asgi_request(
        stream_app,
        IpcRequest(id="stream-1", method="GET", path="/stream", headers={}),
        events.append,
    )

    chunks = [
        base64.b64decode(str(event["data"]))
        for event in events
        if event["type"] == "response_chunk"
    ]
    assert chunks == [b"first\n", b"second\n"]
