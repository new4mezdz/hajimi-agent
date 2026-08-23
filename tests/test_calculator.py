import pytest

from agent_product.services.calculator import CalculatorError, calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("-5 + +2", -3),
        ("7 / 2", 3.5),
        ("7 // 2", 3),
        ("2 ** 10", 1024),
    ],
)
def test_calculator_evaluates_basic_arithmetic(
    expression: str, expected: int | float
) -> None:
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("", "must not be empty"),
        ("1 / 0", "Division by zero"),
        ("2 ** 101", "exponent is too large"),
        ("1e309", "not finite"),
        ("abs(-1)", "basic arithmetic"),
        ("__import__('os').getcwd()", "basic arithmetic"),
        ("True + 1", "Only integer"),
        ("\ud800", "not valid arithmetic"),
    ],
)
def test_calculator_rejects_unsafe_or_unbounded_expressions(
    expression: str, message: str
) -> None:
    with pytest.raises(CalculatorError, match=message):
        calculate(expression)


def test_calculator_limits_expression_size_and_result_size() -> None:
    with pytest.raises(CalculatorError, match="200 characters"):
        calculate("1+" * 101 + "1")
    with pytest.raises(CalculatorError, match="200 characters"):
        calculate(" " * 201)
    with pytest.raises(CalculatorError, match="integer result is too large"):
        calculate("99999999999999999999 ** 100")
