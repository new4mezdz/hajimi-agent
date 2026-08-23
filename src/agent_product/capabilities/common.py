from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic_ai import Agent, RunContext

from agent_product.capabilities.base import (
    CapabilityContext,
    CapabilityPack,
    ToolPolicy,
    tool_options,
)
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.calculator import CalculatorError, calculate

CURRENT_TIME_POLICY = ToolPolicy(
    name="current_time",
    category="common",
    risk="read",
    approval="automatic",
    concurrency="parallel",
    timeout_seconds=5,
)
CALCULATOR_POLICY = ToolPolicy(
    name="calculator",
    category="common",
    risk="read",
    approval="automatic",
    concurrency="parallel",
    timeout_seconds=5,
)


def prompt(context: CapabilityContext) -> str:
    del context
    return "Use calculator for arithmetic instead of estimating results in prose."


def register(agent: Agent, context: CapabilityContext) -> None:
    del context

    @agent.tool(**tool_options(CURRENT_TIME_POLICY))
    async def current_time(
        ctx: RunContext[AgentDependencies], timezone_name: str = "UTC"
    ) -> str:
        """Return the current ISO-8601 time in an IANA timezone such as Asia/Shanghai."""
        del ctx
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            return f"Unknown timezone: {timezone_name}"
        return datetime.now(timezone).isoformat()

    @agent.tool(**tool_options(CALCULATOR_POLICY))
    async def calculator(
        ctx: RunContext[AgentDependencies], expression: str
    ) -> dict[str, Any]:
        """Evaluate basic arithmetic without variables, functions, or arbitrary code."""
        del ctx
        try:
            return {"expression": expression, "result": calculate(expression)}
        except CalculatorError as exc:
            return {"expression": expression, "error": str(exc)}


PACK = CapabilityPack(
    id="common",
    version="1",
    order=10,
    tools=(CURRENT_TIME_POLICY, CALCULATOR_POLICY),
    prompt=prompt,
    register=register,
)
