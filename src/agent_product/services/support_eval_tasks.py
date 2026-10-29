"""Strict, provider-independent loading of the scripted support evaluation suite."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agent_product.services.support_eval_data import ORDER_ALIASES

Issue = Literal["damaged", "delivery", "missing", "other"]
Resolution = Literal["refund", "replacement", "manual_review"]
PositiveInt = Annotated[int, Field(gt=0)]
NonnegativeInt = Annotated[int, Field(ge=0)]
NonemptyText = Annotated[str, Field(min_length=1)]
PLACEHOLDER = re.compile(r"\{(tracking:|tracking_display:)?([A-Za-z][A-Za-z0-9_-]*)\}")
TOOL_ARGUMENTS = {
    "find_my_orders": {"days", "product_hint", "status_hint", "limit"},
    "lookup_my_order": {"order", "order_number"},
    "assess_after_sales_options": {"order", "order_number", "line_number", "issue_type"},
    "create_support_case": {
        "order",
        "order_number",
        "line_number",
        "issue_type",
        "requested_resolution",
        "summary",
    },
}


def _alias(value: str) -> str:
    if value not in ORDER_ALIASES:
        raise ValueError(f"Unknown order alias: {value}")
    return value


def resolve_eval_text(
    text: str,
    orders: dict[str, str],
    tracking: dict[str, str],
    *,
    regex: bool = False,
) -> str:
    """Expand named placeholders without treating regex quantifiers as aliases."""
    for start in re.finditer(r"\{[A-Za-z]", text):
        match = PLACEHOLDER.match(text, start.start())
        if match is None or text[match.end() :].startswith("}"):
            raise ValueError("Malformed named placeholder")
        if start.start() and text[start.start() - 1] == "{":
            raise ValueError("Malformed named placeholder")
    if re.search(r"(?<![\w{])(?:tracking(?:_display)?:)?[A-Z][A-Z0-9_-]*\}", text):
        # A named closing reference must belong to an opening placeholder.
        without_placeholders = PLACEHOLDER.sub("", text)
        if re.search(r"(?:tracking(?:_display)?:)?[A-Z][A-Z0-9_-]*\}", without_placeholders):
            raise ValueError("Malformed named placeholder")

    def replace(match: re.Match[str]) -> str:
        kind, alias = match.groups()
        _alias(alias)
        mapping = tracking if kind else orders
        if alias not in mapping:
            raise ValueError(f"No {'tracking number' if kind else 'order'} for {alias}")
        value = mapping[alias]
        if kind == "tracking_display:":
            # An independent expected-value rule, not the production presenter.
            value = ("****" + value[-4:] if len(value) > 4 else "****") if value else ""
        return re.escape(value) if regex else value

    return PLACEHOLDER.sub(replace, text)


def _validate_text(text: str, *, regex: bool = False) -> str:
    if not text.strip():
        raise ValueError("Text must not be blank")
    for match in re.finditer(r"\{tracking(?:_display)?:[^{}]*\}", text):
        if not PLACEHOLDER.fullmatch(match.group()):
            raise ValueError("Malformed tracking placeholder")
    expanded = resolve_eval_text(
        text,
        {alias: "EC202601010001" for alias in ORDER_ALIASES},
        {alias: "EVAL-TRACK-0001" for alias in ORDER_ALIASES if alias != "S"},
        regex=regex,
    )
    if regex:
        re.compile(expanded)
    return text


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Case(_StrictModel):
    order: str
    line_number: PositiveInt | None = None
    issue_type: Issue | None = None
    requested_resolution: Resolution | None = None

    _check_order = field_validator("order")(_alias)

    @model_validator(mode="after")
    def valid_line(self) -> _Case:
        if self.line_number is not None and self.line_number > (2 if self.order == "V" else 1):
            raise ValueError("Line does not exist in the evaluation order")
        return self


class _ForbiddenCase(_Case):
    turn: PositiveInt | None = None


class _ApprovalCondition(_StrictModel):
    order: str | None = None
    line_number: PositiveInt | None = None
    issue_type: Issue | None = None
    requested_resolution: Resolution | None = None

    @field_validator("order")
    @classmethod
    def valid_order(cls, value: str | None) -> str | None:
        return _alias(value) if value is not None else None

    @model_validator(mode="after")
    def valid_line(self) -> _ApprovalCondition:
        maximum = 2 if self.order in {None, "V"} else 1
        if self.line_number is not None and self.line_number > maximum:
            raise ValueError("Approval line does not exist in the evaluation order")
        return self


class _Approval(_StrictModel):
    when: _ApprovalCondition = Field(default_factory=_ApprovalCondition)
    approve: bool


class _TurnLimit(_StrictModel):
    turn: PositiveInt
    max: NonnegativeInt


class _ToolExpectation(_StrictModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    turn: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_arguments(self) -> _ToolExpectation:
        if self.tool not in TOOL_ARGUMENTS:
            raise ValueError(f"Unknown support tool: {self.tool}")
        if set(self.args) - TOOL_ARGUMENTS[self.tool]:
            raise ValueError(f"Unknown argument for {self.tool}")
        if "order" in self.args and "order_number" in self.args:
            raise ValueError("Specify order or order_number, not both")
        for name, value in self.args.items():
            if name in {"days", "limit", "line_number"}:
                upper = {"days": 365, "limit": 20, "line_number": 2}[name]
                if isinstance(value, dict):
                    if set(value) != {"gt"} or type(value["gt"]) is not int:
                        raise ValueError("Numeric comparisons must have one integer gt field")
                    if value["gt"] < 0 or value["gt"] >= upper:
                        raise ValueError("Numeric comparison cannot match a valid tool argument")
                elif type(value) is not int or not 1 <= value <= upper:
                    raise ValueError(f"Invalid {name}")
            elif name == "order":
                if not isinstance(value, str):
                    raise ValueError("Order alias must be a string")
                _alias(value)
            elif name == "issue_type":
                if not isinstance(value, str) or value not in {
                    "damaged",
                    "delivery",
                    "missing",
                    "other",
                }:
                    raise ValueError("Invalid issue_type")
            elif name == "requested_resolution":
                if not isinstance(value, str) or value not in {
                    "refund",
                    "replacement",
                    "manual_review",
                }:
                    raise ValueError("Invalid requested_resolution")
            elif not isinstance(value, str):
                raise ValueError(f"Argument {name} must be a string")
            else:
                _validate_text(value)
        line = self.args.get("line_number")
        alias = self.args.get("order")
        if alias and type(line) is int and line > (2 if alias == "V" else 1):
            raise ValueError("Tool line does not exist in the evaluation order")
        return self


class _ReplyPatterns(_StrictModel):
    turn: PositiveInt | None = None
    patterns: list[NonemptyText] = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def valid_patterns(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_text(value, regex=True)
        return values


class _ReplyOrders(_StrictModel):
    turn: PositiveInt | None = None
    orders: list[str] = Field(min_length=1)

    @field_validator("orders")
    @classmethod
    def valid_orders(cls, values: list[str]) -> list[str]:
        for value in values:
            _alias(value)
        return values


class _ReplyTracking(_StrictModel):
    order: str
    turn: PositiveInt | None = None

    @field_validator("order")
    @classmethod
    def valid_order(cls, value: str) -> str:
        _alias(value)
        if value == "S":
            raise ValueError("Order S has no shipment or tracking number")
        return value


class _Expectations(_StrictModel):
    cases: list[_Case] | None = None
    case_count: NonnegativeInt | None = None
    forbidden_case_requests: list[_ForbiddenCase] = Field(default_factory=list)
    max_case_requests: NonnegativeInt | None = None
    turn_max_case_requests: list[_TurnLimit] = Field(default_factory=list)
    tool_called: list[_ToolExpectation] = Field(default_factory=list)
    reply_any: list[_ReplyPatterns] = Field(default_factory=list)
    reply_none: list[_ReplyPatterns] = Field(default_factory=list)
    reply_contains_orders: list[_ReplyOrders] = Field(default_factory=list)
    reply_excludes_orders: list[_ReplyOrders] = Field(default_factory=list)
    reply_tracking: list[_ReplyTracking] = Field(default_factory=list)
    asks_question: list[PositiveInt] = Field(default_factory=list)
    no_tracking: list[PositiveInt] = Field(default_factory=list)
    no_orders: list[PositiveInt] = Field(default_factory=list)
    forbidden_sensitive: list[NonemptyText] = Field(default_factory=list)
    require_denied_approval: bool = False
    require_case_requests: bool = False

    @model_validator(mode="after")
    def consistent_counts(self) -> _Expectations:
        if self.cases is not None:
            if self.case_count is not None and self.case_count != len(self.cases):
                raise ValueError("cases and case_count disagree")
            if self.max_case_requests is not None and self.max_case_requests < len(self.cases):
                raise ValueError("More required cases than allowed case requests")
        if self.max_case_requests == 0 and (
            self.require_case_requests or self.require_denied_approval
        ):
            raise ValueError("Required case requests conflict with max_case_requests=0")
        return self


class _Task(_StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]*$")]
    number: Annotated[int, Field(ge=1, le=30)]
    group: Literal["shipping", "refund", "replacement", "clarification", "approval", "security"]
    customer: Literal["eval-a", "eval-b", "eval-c"] = "eval-a"
    turns: list[NonemptyText] = Field(min_length=1)
    approvals: list[_Approval] = Field(default_factory=list)
    poison_knowledge: bool = False
    expect: _Expectations

    @field_validator("turns")
    @classmethod
    def valid_prompts(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_text(value)
        return values

    @model_validator(mode="after")
    def valid_turn_references(self) -> _Task:
        expect = self.expect.model_dump(exclude_none=True)
        references = [
            entry["turn"]
            for entries in expect.values()
            if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict) and "turn" in entry
        ]
        for name in ("asks_question", "no_tracking", "no_orders"):
            references.extend(expect[name])
        if any(turn > len(self.turns) for turn in references):
            raise ValueError("Expectation refers to a nonexistent turn")
        for rule in self.approvals[:-1]:
            if not rule.when.model_dump(exclude_none=True):
                raise ValueError("An unconditional approval rule must be last")
        return self


def load_support_eval_tasks(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Load a suite or subset; reject typos before any model request can be made."""
    tasks: list[dict[str, Any]] = []
    ids: set[str] = set()
    numbers: set[int] = set()
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line, object_pairs_hook=_unique_object)
            task = _Task.model_validate(payload).model_dump(exclude_none=True)
        except (TypeError, ValueError, re.error) as exc:
            raise ValueError(f"Invalid support eval task on line {line_number}: {exc}") from exc
        if task["id"] in ids or task["number"] in numbers:
            raise ValueError(f"Duplicate support eval id or number on line {line_number}")
        ids.add(task["id"])
        numbers.add(task["number"])
        tasks.append(task)
    if not tasks:
        raise ValueError("Support evaluation dataset is empty")
    return tuple(tasks)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result
