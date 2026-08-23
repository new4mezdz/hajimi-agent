import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import (
    DeferredToolRequests,
    DeferredToolResults,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.db.base import Base
from agent_product.db.session import build_engine, build_session_factory
from agent_product.main import create_app
from agent_product.services.agent import AgentDependencies, build_agent
from agent_product.services.agent_profiles import build_builtin_profile_registry
from agent_product.services.support import SupportError, SupportService, seed_support_demo_data

TENANT = "tenant-a"
CUSTOMER = "customer-demo-a"


async def make_support_service(tmp_path: Path) -> tuple[SupportService, object]:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'commerce.db').as_posix()}",
        web_search_enabled=False,
        support_demo_seed_enabled=False,
    )
    engine = build_engine(settings)
    factory = build_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed_support_demo_data(factory, TENANT, CUSTOMER)
    return SupportService(factory), engine


async def order_number_for(service: SupportService, product_hint: str) -> str:
    orders = await service.find_orders(
        TENANT,
        CUSTOMER,
        days=365,
        product_hint=product_hint,
    )
    assert len(orders) == 1
    return orders[0]["order_number"]


@pytest.mark.asyncio
async def test_find_my_orders_uses_customer_time_and_product_scope(tmp_path: Path) -> None:
    service, engine = await make_support_service(tmp_path)
    try:
        headphones = await service.find_orders(
            TENANT,
            CUSTOMER,
            days=2,
            product_hint="耳机",
        )
        other_customer = await service.find_orders(TENANT, "customer-other", days=365)
        second_customer = await service.find_orders(TENANT, "customer-demo-b", days=365)

        assert len(headphones) == 1
        assert headphones[0]["status"] == "in_transit"
        assert headphones[0]["items"][0]["name"] == "降噪耳机"
        assert headphones[0]["order_number"].startswith("EC")
        assert other_customer == []
        assert len(second_customer) == 1
        assert second_customer[0]["items"][0]["name"] == "无线键盘"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_order_projection_contains_customer_payment_shipment_and_inventory(
    tmp_path: Path,
) -> None:
    service, engine = await make_support_service(tmp_path)
    try:
        order_number = await order_number_for(service, "键盘")
        order = await service.lookup_order(TENANT, CUSTOMER, order_number)
        item = order["items"][0]

        assert order["order_number"] == order_number
        assert order["customer_id"] == CUSTOMER
        assert order["customer"] == "李晓晴（演示）"
        assert order["shipping_address"]["phone"] == "138****5678"
        assert item["line_number"] == 1
        assert item["inventory"][0]["available"] == 7
        assert order["payments"][0]["method"] == "Visa •••• 4242"
        assert order["shipments"][0]["status"] == "delivered"

        with pytest.raises(SupportError):
            await service.lookup_order(TENANT, "customer-other", order_number)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_after_sales_options_cover_three_realistic_scenarios(tmp_path: Path) -> None:
    service, engine = await make_support_service(tmp_path)
    try:
        keyboard = await order_number_for(service, "键盘")
        accessory = await order_number_for(service, "配件")
        headphones = await order_number_for(service, "耳机")
        eligible = await service.assess_after_sales_options(
            TENANT, CUSTOMER, keyboard, 1, "damaged"
        )
        expired = await service.assess_after_sales_options(
            TENANT, CUSTOMER, accessory, 1, "damaged"
        )
        in_transit = await service.assess_after_sales_options(
            TENANT, CUSTOMER, headphones, 1, "delivery"
        )

        assert eligible["refund"]["available"] is True
        assert eligible["replacement"]["available_stock"] == 7
        assert expired["refund"]["reasons"] == ["refund-window-expired"]
        assert expired["replacement"]["reasons"] == ["replacement-out-of-stock"]
        assert expired["manual_review"]["required"] is True
        assert in_transit["refund"]["available"] is False
        assert in_transit["shipment"]["status"] == "in_transit"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cases_use_public_numbers_and_customer_scope(tmp_path: Path) -> None:
    service, engine = await make_support_service(tmp_path)
    try:
        order_number = await order_number_for(service, "配件")
        created = await service.create_case(
            TENANT,
            CUSTOMER,
            order_number,
            1,
            "damaged",
            "manual_review",
            "顾客请求对超过退款窗口且无换货库存的订单进行人工复核。",
        )

        assert created["case_number"].startswith("AS")
        assert created["order_number"] == order_number
        assert (await service.get_case(TENANT, CUSTOMER, created["case_number"]))[
            "customer_id"
        ] == CUSTOMER
        assert await service.list_cases(TENANT, "customer-other") == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_automatic_resolution_is_rolled_back(tmp_path: Path) -> None:
    service, engine = await make_support_service(tmp_path)
    try:
        order_number = await order_number_for(service, "配件")
        with pytest.raises(SupportError, match="manual_review"):
            await service.create_case(
                TENANT,
                CUSTOMER,
                order_number,
                1,
                "damaged",
                "replacement",
                "顾客请求对无可用库存的商品直接创建自动换货工单。",
            )
        assert await service.list_cases(TENANT, CUSTOMER) == []
        await seed_support_demo_data(service.session_factory, TENANT, CUSTOMER)
        assert len(await service.find_orders(TENANT, CUSTOMER, days=365)) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_support_case_tool_requires_explicit_approval(tmp_path: Path) -> None:
    service, engine = await make_support_service(tmp_path)
    order_number = await order_number_for(service, "配件")

    def support_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(parts=[TextPart(content="Support case created for review.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="create_support_case",
                    args={
                        "order_number": order_number,
                        "line_number": 1,
                        "issue_type": "damaged",
                        "requested_resolution": "manual_review",
                        "summary": "顾客请求对超过退款窗口且无换货库存的订单进行人工复核。",
                    },
                    tool_call_id="support-case-approval-1",
                )
            ]
        )

    try:
        settings = Settings(ai_model="test", web_search_enabled=False)
        profile = build_builtin_profile_registry(settings).get("support")
        agent = build_agent(settings, model=FunctionModel(support_model), profile=profile)
        dependencies = AgentDependencies(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            request_id="request-1",
            support_service=service,
            profile_id="support",
        )
        proposed = await agent.run("Create a support case", deps=dependencies)

        assert isinstance(proposed.output, DeferredToolRequests)
        assert await service.list_cases(TENANT, CUSTOMER) == []

        approved = await agent.run(
            "Continue",
            deps=dependencies,
            message_history=proposed.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={proposed.output.approvals[0].tool_call_id: True}
            ),
        )
        assert approved.output == "Support case created for review."
        assert len(await service.list_cases(TENANT, CUSTOMER)) == 1
    finally:
        await engine.dispose()


def test_support_api_uses_public_numbers_and_authenticated_customer(tmp_path: Path) -> None:
    database_path = tmp_path / "support.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        web_search_enabled=False,
        support_demo_tenant_id=TENANT,
        support_demo_customer_id=CUSTOMER,
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    headers = {"X-Tenant-ID": TENANT, "X-Customer-ID": CUSTOMER}

    with TestClient(app) as client:
        found = client.get(
            "/v1/support/orders",
            params={"days": 365, "product_hint": "键盘"},
            headers=headers,
        )
        order_number = found.json()[0]["order_number"]
        order = client.get(f"/v1/support/orders/{order_number}", headers=headers)
        options = client.get(
            f"/v1/support/orders/{order_number}/items/1/after-sales-options",
            params={"issue_type": "damaged"},
            headers=headers,
        )
        other_customer = client.get(
            f"/v1/support/orders/{order_number}",
            headers={"X-Tenant-ID": TENANT, "X-Customer-ID": "customer-other"},
        )

    assert found.status_code == 200
    assert order.json()["customer_id"] == CUSTOMER
    assert order.json()["items"][0]["inventory"][0]["available"] == 7
    assert options.json()["replacement"]["available"] is True
    assert other_customer.status_code == 404


def test_streaming_case_approval_ignores_client_tampering(tmp_path: Path) -> None:
    selected: dict[str, str] = {}

    async def support_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            yield "Case queued for human review"
            return
        yield {
            0: DeltaToolCall(
                name="create_support_case",
                json_args=json.dumps(
                    {
                        "order_number": selected["order_number"],
                        "line_number": 1,
                        "issue_type": "damaged",
                        "requested_resolution": "manual_review",
                        "summary": "顾客请求对超过退款窗口且无换货库存的订单进行人工复核。",
                    },
                    ensure_ascii=False,
                ),
                tool_call_id="support-stream-1",
            )
        }

    database_path = tmp_path / "support-stream.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        web_search_enabled=False,
        support_demo_tenant_id=TENANT,
        support_demo_customer_id=CUSTOMER,
    )
    app = create_app(settings=settings, model=FunctionModel(stream_function=support_model))
    conversation_id = str(uuid4())
    headers = {
        "X-Tenant-ID": TENANT,
        "X-Customer-ID": CUSTOMER,
        "X-Agent-Profile": "support",
        "Accept": "text/event-stream",
    }
    payload = {
        "trigger": "submit-message",
        "id": conversation_id,
        "messages": [
            {
                "id": "support-user-1",
                "role": "user",
                "parts": [{"type": "text", "text": "请建立人工退款复核工单"}],
            }
        ],
    }

    with TestClient(app) as client:
        found = client.get(
            "/v1/support/orders",
            params={"days": 365, "product_hint": "配件"},
            headers=headers,
        )
        selected["order_number"] = found.json()[0]["order_number"]
        proposed = client.post("/v1/chat/stream", headers=headers, json=payload)
        history = client.get(
            f"/v1/conversations/{conversation_id}/messages",
            headers=headers,
        ).json()
        pending_message = history[-1]
        pending_part = pending_message["parts"][-1]
        pending_part["state"] = "approval-responded"
        pending_part["approval"] = {
            "id": pending_part["approval"]["id"],
            "approved": True,
        }
        pending_part["input"] = {
            "order_number": "EC-TAMPERED",
            "line_number": 999,
            "issue_type": "other",
            "requested_resolution": "refund",
            "summary": "tampered client input",
        }
        approved = client.post(
            "/v1/chat/stream",
            headers=headers,
            json={**payload, "messages": [pending_message]},
        )
        cases = client.get("/v1/support/cases", headers=headers)

    assert proposed.status_code == 200
    assert approved.status_code == 200
    assert len(cases.json()) == 1
    assert cases.json()[0]["order_number"] == selected["order_number"]
