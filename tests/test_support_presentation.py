import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import (
    DeferredToolRequests,
    DeferredToolResults,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_product.core.config import Settings
from agent_product.db.base import Base
from agent_product.services.agent import build_agent
from agent_product.services.agent_profiles import build_builtin_profile_registry
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.support import SupportService
from agent_product.services.support_eval_data import seed_support_eval_data
from agent_product.services.support_presentation import mask_tracking_number, support_tool_payload


@pytest.fixture(autouse=True)
def no_live_model_requests(monkeypatch):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("EVAL-TRACK-0003", "****0003"),
        ("SF1234567890123", "****0123"),
        ("JD-A9Z8", "****A9Z8"),
        ("  EVAL-TRACK-0012  ", "****0012"),
        ("12345", "****2345"),
        ("1234", "****"),
        ("1", "****"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_tracking_mask_matches_independent_golden_values(raw: str, expected: str) -> None:
    assert mask_tracking_number(raw) == expected


def test_projection_masks_nested_snapshot_without_mutating_source() -> None:
    source = {
        "shipments": [{"tracking_number": "EVAL-TRACK-0003", "status": "in_transit"}],
        "eligibility_snapshot": {"shipment": {"tracking_number": "SF1234567890123"}},
        "tracking_number": "EVAL-TRACK-0012",
        "tracking_number_display": "untrusted-old-display",
        "no_shipment": None,
    }
    original = copy.deepcopy(source)
    projected = support_tool_payload(source)

    assert source == original
    assert projected == {
        "shipments": [{"tracking_number_display": "****0003", "status": "in_transit"}],
        "eligibility_snapshot": {"shipment": {"tracking_number_display": "****0123"}},
        "tracking_number_display": "****0012",
        "no_shipment": None,
    }
    projected["shipments"][0]["status"] = "changed"
    assert source["shipments"][0]["status"] == "in_transit"
    assert "EVAL-TRACK" not in json.dumps(projected)


@pytest.mark.parametrize("raw", [None, 12345678, {"secret": "raw"}])
def test_non_string_tracking_fails_closed(raw) -> None:
    assert support_tool_payload({"tracking_number": raw}) == {"tracking_number_display": None}


def test_already_projected_display_is_not_masked_again() -> None:
    source = {"shipment": {"tracking_number_display": "****0003"}}
    assert support_tool_payload(source) == source


def _support_agent(model: FunctionModel):
    settings = Settings(_env_file=None, ai_model="test", web_search_enabled=False)
    profile = build_builtin_profile_registry(settings).get("support")
    return build_agent(settings, model=model, profile=profile)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["find_my_orders", "lookup_my_order", "assess_after_sales_options", "create_support_case"],
)
async def test_all_support_tools_mask_payloads_but_preserve_service_data(
    tmp_path: Path,
    tool_name: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'support.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        data = await seed_support_eval_data(factory)
        service = SupportService(factory)
        args = {"order_number": data.orders["K"]}
        if tool_name == "find_my_orders":
            args = {"product_hint": "键盘"}
        elif tool_name in {"assess_after_sales_options", "create_support_case"}:
            args.update(line_number=1, issue_type="damaged")
        if tool_name == "create_support_case":
            args.update(
                requested_resolution="refund", summary="顾客反馈键盘损坏，申请退款人工审核。"
            )
        observed = []

        def scripted(messages, info):
            del info
            returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if returns:
                observed.append(returns[-1].content)
                return ModelResponse(parts=[TextPart("已读取脱敏结果。")])
            return ModelResponse(
                parts=[ToolCallPart(tool_name, args, tool_call_id="tracking-call")]
            )

        agent = _support_agent(FunctionModel(scripted))
        dependencies = AgentDependencies(
            tenant_id="eval",
            customer_id="eval-a",
            request_id="mask-test",
            support_service=service,
        )
        result = await agent.run("核验此订单", deps=dependencies)
        if tool_name == "create_support_case":
            assert isinstance(result.output, DeferredToolRequests)
            assert await service.list_cases("eval", "eval-a") == []
            result = await agent.run(
                deps=dependencies,
                message_history=result.all_messages(),
                deferred_tool_results=DeferredToolResults(approvals={"tracking-call": True}),
            )
        assert result.output == "已读取脱敏结果。"
        assert len(observed) == 1
        serialized = json.dumps(observed[0], ensure_ascii=False)
        assert "****0001" in serialized
        assert "EVAL-TRACK-0001" not in serialized
        assert '"tracking_number":' not in serialized
        raw = await service.lookup_order("eval", "eval-a", data.orders["K"])
        assert raw["shipments"][0]["tracking_number"] == "EVAL-TRACK-0001"
        if tool_name == "create_support_case":
            cases = await service.list_cases("eval", "eval-a")
            assert (
                cases[0]["eligibility_snapshot"]["shipment"]["tracking_number"] == "EVAL-TRACK-0001"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "invalid"),
    [
        ("find_my_orders", {"status_hint": "未送达"}),
        ("assess_after_sales_options", {"issue_type": "return_request"}),
        ("create_support_case", {"issue_type": "return_request"}),
        ("create_support_case", {"requested_resolution": "pay_now"}),
    ],
)
async def test_invalid_enum_arguments_are_rejected_before_service_execution(
    tool_name: str,
    invalid: dict,
) -> None:
    service = SimpleNamespace(
        find_orders=AsyncMock(), assess_after_sales_options=AsyncMock(), create_case=AsyncMock()
    )
    args = {"order_number": "EC202610170001", "line_number": 1, "issue_type": "damaged"}
    if tool_name == "find_my_orders":
        args = {}
    if tool_name == "create_support_case":
        args.update(requested_resolution="refund", summary="顾客反馈键盘损坏，申请退款人工审核。")
    args.update(invalid)
    schemas = {}
    retries = []

    def scripted(messages, info):
        schemas.update({tool.name: tool.parameters_json_schema for tool in info.function_tools})
        retries.extend(
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.outcome == "retried"
        )
        if retries:
            return ModelResponse(parts=[TextPart("参数无效，停止操作。")])
        return ModelResponse(parts=[ToolCallPart(tool_name, args, tool_call_id="invalid-call")])

    agent = _support_agent(FunctionModel(scripted))
    dependencies = AgentDependencies(
        tenant_id="eval",
        customer_id="eval-a",
        request_id="enum-test",
        support_service=service,
    )
    result = await agent.run("申请售后", deps=dependencies)
    if isinstance(result.output, DeferredToolRequests):
        result = await agent.run(
            deps=dependencies,
            message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(approvals={"invalid-call": True}),
        )
    assert result.output == "参数无效，停止操作。"
    assert retries
    service.find_orders.assert_not_called()
    service.assess_after_sales_options.assert_not_called()
    service.create_case.assert_not_called()
    for tool in ("assess_after_sales_options", "create_support_case"):
        assert schemas[tool]["properties"]["issue_type"]["enum"] == [
            "damaged",
            "delivery",
            "missing",
            "other",
        ]
    assert schemas["create_support_case"]["properties"]["requested_resolution"]["enum"] == [
        "refund",
        "replacement",
        "manual_review",
    ]
    status_types = schemas["find_my_orders"]["properties"]["status_hint"]["anyOf"]
    assert next(item["enum"] for item in status_types if "enum" in item) == [
        "processing",
        "in_transit",
        "delivered",
        "not_delivered",
    ]
