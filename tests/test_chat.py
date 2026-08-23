import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from agent_product.api.routes.chat import (
    WORKSPACE_BINDING_METADATA_KEY,
    _history_with_unique_ui_tool_call_ids,
    _pending_tool_calls,
)
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


def test_invalid_customer_identity_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "tenant-a", "X-Customer-ID": "not valid!"},
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


def test_reused_tool_call_ids_keep_the_latest_call_pending_in_ui() -> None:
    call_id = "provider-reused-id"
    history = [
        ModelResponse(
            parts=[ToolCallPart("create_file", {"path": "a.txt"}, call_id)],
            metadata={WORKSPACE_BINDING_METADATA_KEY: "workspace-a"},
        ),
        ModelRequest(
            parts=[ToolReturnPart("create_file", {"ok": True}, call_id)]
        ),
        ModelResponse(
            parts=[ToolCallPart("create_file", {"path": "b.txt"}, call_id)],
            metadata={WORKSPACE_BINDING_METADATA_KEY: "workspace-a"},
        ),
    ]

    pending = _pending_tool_calls(history)
    ui_messages = VercelAIAdapter.dump_messages(
        _history_with_unique_ui_tool_call_ids(history),
        sdk_version=7,
    )
    tool_states = [
        part.state
        for message in ui_messages
        for part in message.parts
        if hasattr(part, "state") and part.type == "tool-create_file"
    ]

    assert set(pending) == {call_id}
    assert tool_states == ["output-available", "approval-requested"]


def test_streaming_create_requires_and_honors_matching_approval(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    other_project = tmp_path / "other-project"
    other_project.mkdir()
    database_path = tmp_path / "approval.db"

    async def write_model(messages, info):
        del info
        if any(
            isinstance(part, (ToolReturnPart, RetryPromptPart))
            for message in messages
            for part in message.parts
        ):
            yield "Write completed"
            return
        yield {
            0: DeltaToolCall(
                name="create_file",
                json_args=json.dumps(
                    {"path": "approved.txt", "content": "approved by user\n"}
                ),
                tool_call_id="create-stream-1",
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
        other_workspace_response = approval_client.post(
            "/v1/workspaces",
            headers={"X-Tenant-ID": "tenant-a"},
            json={"path": str(other_project)},
        )
        other_workspace_id = other_workspace_response.json()["id"]
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

        second_prompt = approval_client.post(
            "/v1/chat/stream",
            headers=headers,
            json={
                "trigger": "submit-message",
                "id": conversation_id,
                "messages": [
                    {
                        "id": "user-message-2",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Do something else"}],
                    }
                ],
            },
        )
        assert second_prompt.status_code == 409

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

        mismatched_workspace = approval_client.post(
            "/v1/chat/stream",
            headers={**headers, "X-Workspace-ID": other_workspace_id},
            json={
                "trigger": "submit-message",
                "id": conversation_id,
                "messages": [pending_message],
            },
        )
        assert mismatched_workspace.status_code == 400
        assert not (project / "approved.txt").exists()
        assert not (other_project / "approved.txt").exists()

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
        event_response = approval_client.get(
            f"/v1/conversations/{conversation_id}/events",
            headers={"X-Tenant-ID": "tenant-a"},
        )
        event_types = {event["event_type"] for event in event_response.json()}
        assert {"approval.requested", "approval.decided"} <= event_types
        request_event = next(
            event
            for event in event_response.json()
            if event["event_type"] == "request.prepared"
        )
        create_policy = next(
            tool
            for tool in request_event["payload"]["tools"]
            if tool["name"] == "create_file"
        )
        assert create_policy["risk"] == "write"
        assert create_policy["approval"] == "required"
        assert create_policy["concurrency"] == "exclusive"

        rejected_conversation_id = str(uuid4())
        rejected_proposal = approval_client.post(
            "/v1/chat/stream",
            headers=headers,
            json={
                "trigger": "submit-message",
                "id": rejected_conversation_id,
                "messages": [
                    {
                        "id": "reject-user-message",
                        "role": "user",
                        "parts": [{"type": "text", "text": "Create then reject"}],
                    }
                ],
            },
        )
        assert rejected_proposal.status_code == 200
        rejected_history = approval_client.get(
            f"/v1/conversations/{rejected_conversation_id}/messages",
            headers={"X-Tenant-ID": "tenant-a"},
        ).json()
        rejected_message = rejected_history[-1]
        rejected_part = rejected_message["parts"][-1]
        rejected_part["state"] = "approval-responded"
        rejected_part["approval"] = {
            "id": rejected_part["approval"]["id"],
            "approved": False,
            "reason": "User rejected this file write",
        }

        rejected = approval_client.post(
            "/v1/chat/stream",
            headers={**headers, "X-Workspace-ID": other_workspace_id},
            json={
                "trigger": "submit-message",
                "id": rejected_conversation_id,
                "messages": [rejected_message],
            },
        )
        assert rejected.status_code == 200, rejected.text
        assert not (other_project / "approved.txt").exists()
