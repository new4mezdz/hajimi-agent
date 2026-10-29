from __future__ import annotations

from typing import Any, Literal

from pydantic_ai import Agent, RunContext

from agent_product.capabilities.base import (
    CapabilityContext,
    CapabilityPack,
    ToolPolicy,
    tool_options,
)
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.support import SupportError
from agent_product.services.support_presentation import support_tool_payload

IssueType = Literal["damaged", "delivery", "missing", "other"]
RequestedResolution = Literal["refund", "replacement", "manual_review"]
OrderSearchStatus = Literal["processing", "in_transit", "delivered", "not_delivered"]

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
        "with find_my_orders. product_hint is a literal substring, not semantic search. "
        "For a single-item after-sales request with no product, order number, or purchase clue, "
        "ask for one non-sensitive identifying clue before searching. Do not retrieve the whole "
        "order history just to ask which item the customer means. This does not apply when the "
        "customer explicitly asks to list several orders. "
        "After an empty product search, try one meaningfully broader keyword or omit the product "
        "filter before asking for another clue; never repeat an unchanged empty query. "
        "days filters the purchase date, not the delivery date. Increase it for older purchases. "
        "Use status_hint='not_delivered' for all pending deliveries (processing or in_transit); "
        "never use Chinese status strings. Use limit=20 when listing several orders. "
        "If several orders match an individual-item request, ask the user to choose; never guess. "
        "Call "
        "lookup_my_order before making order claims and assess_after_sales_options before "
        "discussing refund or "
        "replacement. The options function is authoritative. create_support_case is the only "
        "action tool and "
        "requires approval; it creates a human-review case, not a payment refund. "
        "When the user already explicitly requests an action or chooses a resolution and the "
        "order/line is clear, propose create_support_case now. Its deferred approval is the "
        "mandatory final authorization; do not add another chat-level yes/no question first. "
        "Consultation alone does not authorize proposing a case. Ask only for essential missing "
        "order/line or resolution choices. Use issue_type='other' for an unspecified reason and "
        "state that it is unspecified in the summary; never invent damage. "
        "Choose requested_resolution from verified eligibility, not just the user's wording. "
        "If refund.available is false, never request refund, including when delivery date is "
        "missing. Explain alternatives. If the user requests human review, or repeats the refund "
        "application request after you explain that only manual review can consider an exception, "
        "propose manual_review and describe the refund goal in summary. Do not silently switch "
        "to replacement. After an approval rejection do not automatically retry. "
        "Check the conversation for an existing case before proposing another; a reminder or "
        "request to submit the same issue again must refer to that case, not create a duplicate. "
        "Never invent "
        "an order or case id. Shipment tools provide tracking_number_display already masked; "
        "copy this display value exactly when reporting a tracking number. Never reconstruct "
        "the full number or change its masking.\n"
        "顾客身份来自认证会话。先用 find_my_orders 根据时间、商品和状态定位用户自己的订单；"
        "用户指某一件商品且有多笔候选时才追问；用户要求列出多笔订单时，逐笔核实后列出，不要求择一。"
        "create_support_case 只会"
        "创建人工处理工单且需要批准，不会执行支付退款。用户已经明确要求办理且订单与方案已确定时，"
        "直接发起工具审批，不再追加聊天确认；实际写入仍必须经过工具审批。只咨询时不建单。"
        "退款不可用时，不得把退款诉求直接填成 refund；说明人工复核路径并取得明确申请意愿后，"
        "使用 manual_review。已有工单时，催办或再次提交请求不应重复建单。"
        "不得编造订单号、工单号或处理结果。"
        "物流号由程序脱敏，展示时原样使用 tracking_number_display，不得自行改写或还原完整单号。"
    )


def register(agent: Agent, context: CapabilityContext) -> None:
    del context

    @agent.tool(**tool_options(FIND_ORDERS_POLICY))
    async def find_my_orders(
        ctx: RunContext[AgentDependencies],
        days: int = 30,
        product_hint: str | None = None,
        status_hint: OrderSearchStatus | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]] | dict[str, str]:
        """Find this customer's orders using purchase age and literal product substrings.

        status_hint uses English values; not_delivered includes processing and in_transit.
        Omit status_hint for all states. Broaden an empty product search instead of repeating it.
        """
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            result = await ctx.deps.support_service.find_orders(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                days=days,
                product_hint=product_hint,
                status_hint=status_hint,
                limit=limit,
            )
            return support_tool_payload(result)
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
            result = await ctx.deps.support_service.lookup_order(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
            )
            return support_tool_payload(result)
        except SupportError as exc:
            return {"error": str(exc)}

    @agent.tool(**tool_options(ASSESS_REFUND_POLICY))
    async def assess_after_sales_options(
        ctx: RunContext[AgentDependencies],
        order_number: str,
        line_number: int,
        issue_type: IssueType,
    ) -> dict[str, Any]:
        """Return deterministic refund, replacement and manual-review options."""
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            result = await ctx.deps.support_service.assess_after_sales_options(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
                line_number,
                issue_type,
            )
            return support_tool_payload(result)
        except SupportError as exc:
            return {"error": str(exc)}

    @agent.tool(**tool_options(CREATE_CASE_POLICY))
    async def create_support_case(
        ctx: RunContext[AgentDependencies],
        order_number: str,
        line_number: int,
        issue_type: IssueType,
        requested_resolution: RequestedResolution,
        summary: str,
    ) -> dict[str, Any]:
        """Propose a human-processing case and pause for mandatory approval before writing.

        An explicit customer action request is enough to propose this tool; no extra chat
        confirmation is needed. Select refund/replacement only when assess marks it available.
        For an accepted exception-review request use manual_review, even if the goal is refund.
        """
        if ctx.deps is None or ctx.deps.support_service is None:
            return {"error": "The support service is not available"}
        try:
            result = await ctx.deps.support_service.create_case(
                ctx.deps.tenant_id,
                ctx.deps.customer_id,
                order_number,
                line_number,
                issue_type,
                requested_resolution,
                summary,
            )
            return support_tool_payload(result)
        except SupportError as exc:
            return {"error": str(exc)}


PACK = CapabilityPack(
    id="support",
    version="4",
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
