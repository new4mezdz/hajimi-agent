import json
from pathlib import Path

import pytest
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from agent_product.core.config import Settings
from agent_product.services.support_eval import (
    DEFAULT_TASKS,
    EvalLimits,
    evaluate_support,
    run_support_eval_task,
    summarize_support_results,
)
from agent_product.services.support_eval_tasks import load_support_eval_tasks


@pytest.fixture(autouse=True)
def no_live_requests(monkeypatch):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", False)


def eval_settings() -> Settings:
    return Settings(_env_file=None, ai_model="test", openai_api_key=None, deepseek_api_key=None)


def task_number(number: int) -> dict:
    return next(task for task in load_support_eval_tasks(DEFAULT_TASKS) if task["number"] == number)


def refund_model(*, bad_after_approval: bool = False) -> FunctionModel:
    def scripted(messages, info):
        del info
        users = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, UserPromptPart)
        ]
        assert len(users) <= 2  # No synthetic "Continue" user message on approval.
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        last = returns[-1] if returns else None
        if last is None:
            tool, args = "find_my_orders", {"product_hint": "键盘"}
        elif last.tool_name == "find_my_orders":
            tool, args = "lookup_my_order", {"order_number": last.content[0]["order_number"]}
        elif last.tool_name == "lookup_my_order":
            tool, args = (
                "assess_after_sales_options",
                {
                    "order_number": last.content["order_number"],
                    "line_number": 1,
                    "issue_type": "damaged",
                },
            )
        elif last.tool_name == "assess_after_sales_options":
            tool, args = (
                "create_support_case",
                {
                    "order_number": last.content["order_number"],
                    "line_number": 1,
                    "issue_type": "damaged",
                    "requested_resolution": "refund",
                    "summary": "顾客反馈无线键盘按键失灵，申请退款并等待人工处理。",
                },
            )
        else:
            reply = "工单已提交人工审核，请等待处理。"
            if bad_after_approval:
                reply = "退款已到账。"
            elif not isinstance(last.content, dict):
                reply = "您已拒绝本次操作，没有创建工单。"
            return ModelResponse(
                parts=[TextPart(reply)], usage=RequestUsage(input_tokens=10, output_tokens=5)
            )
        return ModelResponse(
            parts=[ToolCallPart(tool, args, tool_call_id=f"call-{len(returns)}")],
            usage=RequestUsage(input_tokens=10, output_tokens=5),
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_approved_refund_tracks_calls_once_and_usage_across_turns() -> None:
    result = await run_support_eval_task(task_number(6), eval_settings(), model=refund_model())
    assert result["error"] is None
    assert result["passed"], result["failures"]
    assert len(result["cases"]) == 1
    assert result["cases"][0]["requested_resolution"] == "refund"
    calls = [call for turn in result["turns"] for call in turn["tool_calls"]]
    assert [call["tool"] for call in calls].count("create_support_case") == 1
    assert len(result["turns"][0]["approvals"]) == 1
    assert result["turns"][0]["approvals"][0]["approved"] is True
    assert result["usage"]["requests"] == 6
    assert result["usage"]["input_tokens"] == 60
    assert result["usage"]["output_tokens"] == 30
    for field in ("requests", "input_tokens", "output_tokens"):
        assert sum(run["usage"][field] for run in result["runs"]) == result["usage"][field]


@pytest.mark.asyncio
async def test_rejected_approval_keeps_attempt_but_creates_no_case() -> None:
    result = await run_support_eval_task(task_number(21), eval_settings(), model=refund_model())
    assert result["error"] is None
    assert result["passed"], result["failures"]
    assert result["cases"] == []
    assert result["turns"][0]["approvals"][0]["approved"] is False
    assert (
        sum(call["tool"] == "create_support_case" for call in result["turns"][0]["tool_calls"]) == 1
    )


@pytest.mark.asyncio
async def test_bad_agent_is_failed_even_when_case_was_created() -> None:
    result = await run_support_eval_task(
        task_number(6),
        eval_settings(),
        model=refund_model(bad_after_approval=True),
    )
    assert result["error"] is None
    assert not result["passed"]
    assert len(result["cases"]) == 1


@pytest.mark.asyncio
async def test_request_limit_retains_failure_trace_and_usage() -> None:
    result = await run_support_eval_task(
        task_number(6),
        eval_settings(),
        model=refund_model(),
        limits=EvalLimits(max_requests=2),
    )
    assert not result["passed"]
    assert "UsageLimitExceeded" in result["error"]
    assert result["usage"]["requests"] == 2
    assert result["turns"][0]["tool_calls"]
    assert result["cases"] == []


@pytest.mark.asyncio
async def test_runs_are_isolated_and_reports_checkpointed(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    report = await evaluate_support(
        eval_settings(),
        only=[6],
        repeat=2,
        output=output,
        model_factory=refund_model,
    )
    assert report["complete"]
    assert all(result["passed"] for result in report["results"])
    assert all(len(result["cases"]) == 1 for result in report["results"])
    assert len({result["cases"][0]["case_number"] for result in report["results"]}) == 2
    assert json.loads(output.read_text(encoding="utf-8"))["complete"] is True
    assert report["summary"]["test"]["pass_power_k"] == 1
    assert report["summary"]["test"]["usage"]["requests"] == 12


@pytest.mark.asyncio
async def test_poison_is_isolated_and_exposure_requires_returned_instruction() -> None:
    def poison_model(messages, info):
        del info
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_knowledge_document",
                        {"document_id": "support/eval-notice"},
                        tool_call_id="poison-read",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("应以系统判定为准，提交工单进入人工审核。")])

    exposed = await run_support_eval_task(
        task_number(29),
        eval_settings(),
        model=FunctionModel(poison_model),
    )
    assert exposed["error"] is None
    assert exposed["coverage"]["poison_instruction_exposed"]
    unexposed = await run_support_eval_task(
        task_number(29),
        eval_settings(),
        model=TestModel(call_tools=[], custom_output_text="待核实。"),
    )
    assert not unexposed["coverage"]["poison_instruction_exposed"]
    assert not (DEFAULT_TASKS.parents[2] / "knowledge/support/eval-notice.md").exists()


def test_metrics_keep_failures_and_require_all_repetitions() -> None:
    rows = []
    for task_id, statuses in (("a", [True, False, True]), ("b", [True, True, True])):
        for attempt, passed in enumerate(statuses, start=1):
            rows.append(
                {
                    "model": "test",
                    "id": task_id,
                    "group": "refund",
                    "repeat": attempt,
                    "passed": passed,
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    "elapsed_seconds": 1,
                    "error": None if passed else "failure",
                }
            )
    summary = summarize_support_results(rows, repeat=3)["test"]
    assert summary["pass_at_1"] == 1
    assert summary["overall_pass_rate"] == 5 / 6
    assert summary["pass_power_k"] == 0.5
    assert summary["runtime_errors"] == 1
    assert summary["usage"]["total_tokens"] == 18
    assert summarize_support_results(rows[:-1], repeat=3)["test"]["pass_power_k"] == 0


@pytest.mark.asyncio
async def test_setup_errors_are_failed_and_checkpointed(tmp_path: Path, monkeypatch) -> None:
    def broken_copy(*args, **kwargs):
        raise OSError("Fixture copy failed")

    monkeypatch.setattr("agent_product.services.support_eval.shutil.copytree", broken_copy)
    output = tmp_path / "failed.json"
    report = await evaluate_support(
        eval_settings(),
        only=[1, 6],
        output=output,
        model_factory=refund_model,
    )
    assert report["complete"]
    assert len(report["results"]) == 2
    assert all(not result["passed"] for result in report["results"])
    assert all("Fixture copy failed" in result["error"] for result in report["results"])
    assert report["summary"]["test"]["runtime_errors"] == 2
    assert report["summary"]["test"]["usage"]["requests"] == 0
    assert json.loads(output.read_text(encoding="utf-8"))["complete"]


@pytest.mark.asyncio
async def test_inert_model_cannot_pass_any_shipped_task() -> None:
    report = await evaluate_support(
        eval_settings(),
        model_factory=lambda: TestModel(
            call_tools=[], custom_output_text="这是评测运行器的离线检查。"
        ),
    )
    assert report["complete"]
    assert len(report["results"]) == 30
    assert all(row["error"] is None for row in report["results"])
    assert not any(row["passed"] for row in report["results"])
    assert report["metadata"]["rubric_version"] == 4
    assert len(report["task_definitions"]) == 30


@pytest.mark.asyncio
async def test_shipping_uses_backend_display_and_independent_golden_expectation() -> None:
    def shipping_model(messages, info):
        del info
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returns:
            part = ToolCallPart(
                "find_my_orders", {"product_hint": "耳机"}, tool_call_id="find-headphones"
            )
        elif returns[-1].tool_name == "find_my_orders":
            order = returns[-1].content[0]
            assert order["shipment"]["tracking_number_display"] == "****0003"
            assert "tracking_number" not in order["shipment"]
            part = ToolCallPart(
                "lookup_my_order",
                {"order_number": order["order_number"]},
                tool_call_id="lookup-headphones",
            )
        else:
            shipment = returns[-1].content["shipments"][0]
            assert shipment["tracking_number_display"] == "****0003"
            assert "tracking_number" not in shipment
            # A literal expected display keeps this check independent of the masking helper.
            part = TextPart("您的降噪耳机正在运输中，物流单号：****0003。")
        return ModelResponse(parts=[part])

    result = await run_support_eval_task(
        task_number(1),
        eval_settings(),
        model=FunctionModel(shipping_model),
    )
    assert result["error"] is None
    assert result["passed"], result["failures"]
    assert result["tool_error_count"] == 0
    assert "EVAL-TRACK-" not in json.dumps(result["turns"])


@pytest.mark.asyncio
async def test_duplicate_scenario_requires_an_initial_successful_submission() -> None:
    result = await run_support_eval_task(task_number(24), eval_settings(), model=refund_model())
    assert result["passed"], result["failures"]
    assert [
        sum(call["tool"] == "create_support_case" for call in turn["tool_calls"])
        for turn in result["turns"]
    ] == [1, 0]
    assert len(result["cases"]) == 1


def test_late_first_submission_does_not_prove_duplicate_prevention() -> None:
    from agent_product.services.support_eval_data import EvalData
    from agent_product.services.support_eval_grading import grade_support_run

    data = EvalData(
        orders={"K": "EC202610170001"}, tracking={}, foreign_values=(), created_at="test"
    )
    task = task_number(24)
    trace = {
        "turns": [
            {
                "turn": 1,
                "user": task["turns"][0],
                "reply": "是否确认创建人工工单？",
                "tool_calls": [],
                "tool_returns": [],
                "events": [],
                "approvals": [],
            },
            {
                "turn": 2,
                "user": task["turns"][1],
                "reply": "已提交人工审核工单。",
                "tool_calls": [
                    {
                        "tool": "create_support_case",
                        "call_id": "late",
                        "args": {
                            "order_number": data.orders["K"],
                            "requested_resolution": "refund",
                        },
                    }
                ],
                "tool_returns": [],
                "events": [],
                "approvals": [],
            },
        ],
        "cases": [
            {"order_number": data.orders["K"], "line_number": 1, "requested_resolution": "refund"}
        ],
        "error": None,
    }
    failures = grade_support_run(task, trace, data)
    assert any(failure["rule"] == "missing_tool_call" for failure in failures)
