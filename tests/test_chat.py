import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from agent_product.core.config import Settings
from agent_product.main import create_app


def test_chat_creates_and_continues_conversation(client: TestClient) -> None:
    headers = {"X-Tenant-ID": "tenant-a"}
    first = client.post("/v1/chat", headers=headers, json={"message": "Hello"})

    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["message"] == "Test response"
    assert first_body["version"] == 1

    second = client.post(
        "/v1/chat",
        headers=headers,
        json={
            "message": "Continue",
            "conversation_id": first_body["conversation_id"],
        },
    )

    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == first_body["conversation_id"]
    assert second.json()["version"] == 2


def test_tenant_cannot_read_another_tenants_conversation(client: TestClient) -> None:
    created = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"message": "Hello"},
    )
    conversation_id = created.json()["conversation_id"]

    response = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"X-Tenant-ID": "tenant-b"},
    )
    assert response.status_code == 404


def test_invalid_tenant_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "not valid!"},
        json={"message": "Hello"},
    )
    assert response.status_code == 400


def test_service_api_key(secured_client: TestClient) -> None:
    missing = secured_client.post("/v1/chat", json={"message": "Hello"})
    assert missing.status_code == 401

    accepted = secured_client.post(
        "/v1/chat",
        headers={"X-API-Key": "secret"},
        json={"message": "Hello"},
    )
    assert accepted.status_code == 200


def test_streaming_chat_persists_server_side_history(client: TestClient) -> None:
    conversation_id = str(uuid4())
    payload = {
        "trigger": "submit-message",
        "id": conversation_id,
        "messages": [
            {
                "id": "user-message-1",
                "role": "user",
                "parts": [{"type": "text", "text": "Hello from the UI"}],
            }
        ],
    }

    response = client.post(
        "/v1/chat/stream",
        headers={"X-Tenant-ID": "tenant-a", "Accept": "text/event-stream"},
        json=payload,
    )

    assert response.status_code == 200, response.text
    assert '"type":"finish"' in response.text
    assert "[DONE]" in response.text

    metadata = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert metadata.status_code == 200
    assert metadata.json()["version"] == 1

    messages = client.get(
        f"/v1/conversations/{conversation_id}/messages",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    assert messages.status_code == 200
    roles = [message["role"] for message in messages.json()]
    assert roles[0] == "user"
    assert roles[-1] == "assistant"


def test_streaming_chat_rejects_client_supplied_history(client: TestClient) -> None:
    conversation_id = str(uuid4())
    message = {
        "id": "user-message-1",
        "role": "user",
        "parts": [{"type": "text", "text": "Hello"}],
    }
    response = client.post(
        "/v1/chat/stream",
        json={
            "trigger": "submit-message",
            "id": conversation_id,
            "messages": [message, {**message, "id": "user-message-2"}],
        },
    )
    assert response.status_code == 400


def test_new_conversation_has_empty_ui_history(client: TestClient) -> None:
    conversation_id = str(uuid4())

    response = client.get(
        f"/v1/conversations/{conversation_id}/messages",
        headers={"X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_streaming_write_requires_and_honors_matching_approval(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    database_path = tmp_path / "approval.db"

    async def write_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            yield "Write completed"
            return
        yield {
            0: DeltaToolCall(
                name="write_file",
                json_args=json.dumps(
                    {"path": "approved.txt", "content": "approved by user\n"}
                ),
                tool_call_id="write-stream-1",
            )
        }

    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        service_api_key=None,
        web_search_enabled=False,
    )
    app = create_app(settings=settings, model=FunctionModel(stream_function=write_model))
    with TestClient(app) as approval_client:
        workspace_response = approval_client.post(
            "/v1/workspaces",
            headers={"X-Tenant-ID": "tenant-a"},
            json={"path": str(project)},
        )
        workspace_id = workspace_response.json()["id"]
        conversation_id = str(uuid4())
        headers = {
            "X-Tenant-ID": "tenant-a",
            "X-Workspace-ID": workspace_id,
            "Accept": "text/event-stream",
        }

        proposed = approval_client.post(
            "/v1/chat/stream",
            headers=headers,
            json={
                "trigger": "submit-message",
                "id": conversation_id,
                "messages": [
                    {
                        "id": "user-message-1",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Create approved.txt"}],
                    }
                ],
            },
        )
        assert proposed.status_code == 200, proposed.text
        assert "tool-approval-request" in proposed.text, proposed.text
        assert not (project / "approved.txt").exists()

        history = approval_client.get(
            f"/v1/conversations/{conversation_id}/messages",
            headers={"X-Tenant-ID": "tenant-a"},
        ).json()
        pending_message = history[-1]
        pending_part = pending_message["parts"][-1]
        assert pending_part["state"] == "approval-requested"
        approval_id = pending_part["approval"]["id"]
        pending_part["state"] = "approval-responded"
        pending_part["approval"] = {"id": approval_id, "approved": True}
        pending_part["input"] = {
            "path": "../escaped.txt",
            "content": "client-tampered content\n",
        }

        approved = approval_client.post(
            "/v1/chat/stream",
            headers=headers,
            json={
                "trigger": "submit-message",
                "id": conversation_id,
                "messages": [pending_message],
            },
        )

        assert approved.status_code == 200, approved.text
        assert '"type":"finish"' in approved.text
        assert (project / "approved.txt").read_text(encoding="utf-8") == "approved by user\n"
        assert not (tmp_path / "escaped.txt").exists()
