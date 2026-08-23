from __future__ import annotations

from typing import Any

from pydantic_ai import Agent, RunContext

from agent_product.capabilities.base import (
    CapabilityContext,
    CapabilityPack,
    ToolPolicy,
    tool_options,
)
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.support import SupportError

FIND_ORDERS_POLICY = ToolPolicy(
    name="find_my_orders",
    category="support",
    risk="read",
    approval="automatic",
    concurrency="parallel",
    timeout_seconds=10,
)
LOOKUP_ORDER_POLICY = ToolPolicy(
    name="lookup_my_order",
    category="support",
    risk="read",
    approval="automatic",
    concurrency="parallel",
    timeout_seconds=10,
)
ASSESS_REFUND_POLICY = ToolPolicy(
    name="assess_after_sales_options",
    category="support",
    risk="read",
    approval="automatic",
    concurrency="parallel",
    timeout_seconds=10,
)
CREATE_CASE_POLICY = ToolPolicy(
    name="create_support_case",
    category="support",
    risk="write",
    approval="required",
    concurrency="exclusive",
    timeout_seconds=15,
)


def prompt(context: CapabilityContext) -> str:
    del context
    return (
        "The customer identity comes from the authenticated session and is never a tool argument. "
        "Resolve natural-language references such as yesterday, headphones, or my recent order "
        "with find_my_orders. If several orders match, ask the user to choose; never guess. Call "
        "lookup_my_order before making order claims and assess_after_sales_options before "
        "discussing refund or "
        "replacement. The options function is authoritative. create_support_case is the only "
        "action tool and "
        "requires approval; it creates a human-review case, not a payment refund. Never invent "
        "an order or case id.\n"
        "顾客身份来自认证会话。先用 find_my_orders 根据时间、商品和状态定位用户自己的订单；"
        "多笔匹配时必须请用户确认。create_support_case 只会"
        "创建人工处理工单且需要批准，不会执行支付退款。不得编造订单号、工单号或处理结果。"
    )


def register(agent: Agent, context: CapabilityContext) -> None:
    del context

    @agent.tool(**tool_options(FIND_ORDERS_POLICY))
    async def find_my_orders(
        ctx: RunContext[AgentDependencies],
        days: int = 30,
        product_hint: str | None = None,
        status_hint: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]] | dict[str, str]:
        """Find the authenticated customer's recent orders from natural-language hints."""
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            return await ctx.deps.support_service.find_orders(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                days=days,
                product_hint=product_hint,
                status_hint=status_hint,
                limit=limit,
            )
        except SupportError as exc:
            return {"error": str(exc)}

    @agent.tool(**tool_options(LOOKUP_ORDER_POLICY))
    async def lookup_my_order(
        ctx: RunContext[AgentDependencies], order_number: str
    ) -> dict[str, Any]:
        """Look up one public order number owned by the authenticated customer."""
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            return await ctx.deps.support_service.lookup_order(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
            )
        except SupportError as exc:
            return {"error": str(exc)}

    @agent.tool(**tool_options(ASSESS_REFUND_POLICY))
    async def assess_after_sales_options(
        ctx: RunContext[AgentDependencies],
        order_number: str,
        line_number: int,
        issue_type: str,
    ) -> dict[str, Any]:
        """Return deterministic refund, replacement and manual-review options."""
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            return await ctx.deps.support_service.assess_after_sales_options(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
                line_number,
                issue_type,
            )
        except SupportError as exc:
            return {"error": str(exc)}

    @agent.tool(**tool_options(CREATE_CASE_POLICY))
    async def create_support_case(
        ctx: RunContext[AgentDependencies],
        order_number: str,
        line_number: int,
        issue_type: str,
        requested_resolution: str,
        summary: str,
    ) -> dict[str, Any]:
        """Create an approved human-review case; this does not execute the requested action."""
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            return await ctx.deps.support_service.create_case(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
                line_number,
                issue_type,
                requested_resolution,
                summary,
            )
        except SupportError as exc:
            return {"error": str(exc)}


PACK = CapabilityPack(
    id="support",
    version="1",
    order=60,
    tools=(
        FIND_ORDERS_POLICY,
        LOOKUP_ORDER_POLICY,
        ASSESS_REFUND_POLICY,
        CREATE_CASE_POLICY,
    ),
    prompt=prompt,
    register=register,
)
