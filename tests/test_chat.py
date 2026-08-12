from uuid import uuid4

from fastapi.testclient import TestClient


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
