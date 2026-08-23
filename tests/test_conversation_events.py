from fastapi.testclient import TestClient


def test_chat_records_replayable_request_and_completion_events(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"message": "Hello", "profile_id": "general"},
    )
    conversation_id = created.json()["conversation_id"]

    response = client.get(
        f"/v1/conversations/{conversation_id}/events",
        headers={"X-Tenant-ID": "tenant-a"},
    )

    assert response.status_code == 200
    events = response.json()
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "conversation.created"
    assert {
        "turn.started",
        "request.prepared",
        "message.persisted",
        "turn.completed",
    } <= set(event_types)
    snapshot = next(
        event["payload"] for event in events if event["event_type"] == "request.prepared"
    )
    assert snapshot["profile"]["id"] == "general"
    assert snapshot["profile"]["composition_hash"]
    assert snapshot["active_capability_packs"] == ["common"]
    assert {tool["name"] for tool in snapshot["tools"]} == {
        "current_time",
        "calculator",
    }
    assert all(tool["approval"] == "automatic" for tool in snapshot["tools"])
    assert snapshot["prompt_hash"]
    assert snapshot["static_system_prompt"]
    assert snapshot["tool_schema_hash"]
    calculator_schema = next(
        schema for schema in snapshot["tool_schemas"] if schema["name"] == "calculator"
    )
    assert calculator_schema["parameters"]["required"] == ["expression"]
    completed = next(
        event["payload"] for event in events if event["event_type"] == "turn.completed"
    )
    assert completed["duration_ms"] >= 0
    assert completed["usage"]["requests"] >= 1


def test_event_stream_is_tenant_scoped_and_supports_incremental_reads(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "tenant-a"},
        json={"message": "Hello"},
    )
    conversation_id = created.json()["conversation_id"]
    first_page = client.get(
        f"/v1/conversations/{conversation_id}/events?limit=2",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    last_id = first_page.json()[-1]["id"]
    second_page = client.get(
        f"/v1/conversations/{conversation_id}/events?after_id={last_id}",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    forbidden = client.get(
        f"/v1/conversations/{conversation_id}/events",
        headers={"X-Tenant-ID": "tenant-b"},
    )

    assert len(first_page.json()) == 2
    assert all(event["id"] > last_id for event in second_page.json())
    assert forbidden.status_code == 404


def test_request_snapshot_records_profile_visible_skill_catalog(
    client: TestClient,
) -> None:
    created = client.post(
        "/v1/chat",
        headers={"X-Tenant-ID": "local", "X-Customer-ID": "customer-demo-a"},
        json={"message": "我的键盘坏了", "profile_id": "support"},
    )
    events = client.get(
        f"/v1/conversations/{created.json()['conversation_id']}/events",
        headers={"X-Tenant-ID": "local"},
    ).json()
    snapshot = next(
        event["payload"] for event in events if event["event_type"] == "request.prepared"
    )

    assert {skill["name"] for skill in snapshot["skill_catalog"]} == {
        "order-delivery-status",
        "after-sales-resolution",
        "refund-exception-review",
        "delivery-exception-triage",
    }
