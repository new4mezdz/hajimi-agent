from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import TypeAlias

Number: TypeAlias = int | float

MAX_EXPRESSION_LENGTH = 200
MAX_AST_NODES = 64
MAX_INT_BITS = 4096
MAX_ABS_EXPONENT = 100


class CalculatorError(ValueError):
    """Raised when an arithmetic expression is invalid or unsafe to evaluate."""


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[Number], Number]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _checked_number(value: object) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorError("Only integer and floating-point numbers are allowed")
    if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
        raise CalculatorError("The integer result is too large")
    if isinstance(value, float) and not math.isfinite(value):
        raise CalculatorError("The numeric result is not finite")
    return value


def _evaluate_node(node: ast.expr, depth: int = 0) -> Number:
    if depth > MAX_AST_NODES:
        raise CalculatorError("The expression is too deeply nested")

    if isinstance(node, ast.Constant):
        return _checked_number(node.value)

    if isinstance(node, ast.UnaryOp):
        operation = _UNARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise CalculatorError("This unary operator is not allowed")
        return _checked_number(operation(_evaluate_node(node.operand, depth + 1)))

    if isinstance(node, ast.BinOp):
        operation = _BINARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise CalculatorError("This binary operator is not allowed")

        left = _evaluate_node(node.left, depth + 1)
        right = _evaluate_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            if abs(right) > MAX_ABS_EXPONENT:
                raise CalculatorError("The exponent is too large")
            if isinstance(left, int) and isinstance(right, int) and right >= 0:
                estimated_bits = max(1, left.bit_length()) * right
                if estimated_bits > MAX_INT_BITS:
                    raise CalculatorError("The integer result is too large")

        try:
            return _checked_number(operation(left, right))
        except ZeroDivisionError as exc:
            raise CalculatorError("Division by zero is not allowed") from exc
        except (OverflowError, ValueError) as exc:
            raise CalculatorError("The numeric operation is outside the supported range") from exc

    raise CalculatorError("Only numbers, parentheses, and basic arithmetic operators are allowed")


def calculate(expression: str) -> Number:
    """Safely evaluate a bounded expression containing only basic arithmetic."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(
            f"The expression is limited to {MAX_EXPRESSION_LENGTH} characters"
        )
    normalized = expression.strip()
    if not normalized:
        raise CalculatorError("The expression must not be empty")

    try:
        tree = ast.parse(normalized, mode="eval")
    except (SyntaxError, UnicodeEncodeError, ValueError) as exc:
        raise CalculatorError("The expression is not valid arithmetic") from exc

    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise CalculatorError("The expression contains too many operations")
    return _evaluate_node(tree.body)
