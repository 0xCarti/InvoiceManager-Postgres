"""Utility helpers for parsing numeric input and math expressions."""

from __future__ import annotations

from decimal import Decimal, DivisionByZero, InvalidOperation
import ast
import operator
import re
from typing import Any, Optional


class ExpressionParsingError(ValueError):
    """Raised when a math expression cannot be parsed or evaluated."""


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_EXPRESSION_CHARS_RE = re.compile(r"[+\-*/()]")
_ALLOWED_EXPRESSION_RE = re.compile(r"^[0-9+\-*/().\s]+$")


def evaluate_math_expression(expression: str) -> Decimal:
    """Safely evaluate a restricted arithmetic expression and return a Decimal."""

    try:
        parsed = ast.parse(expression, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - ast gives limited info
        raise ExpressionParsingError(
            "Enter a valid equation using numbers, +, -, *, /, and parentheses."
        ) from exc
    return _evaluate_ast_node(parsed.body)


def _evaluate_ast_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise ExpressionParsingError("Use only +, -, *, and / in equations.")
        left = _evaluate_ast_node(node.left)
        right = _evaluate_ast_node(node.right)
        try:
            return _ALLOWED_BINOPS[op_type](left, right)
        except DivisionByZero as exc:
            raise ExpressionParsingError("Division by zero is not allowed.") from exc
        except InvalidOperation as exc:
            raise ExpressionParsingError("Enter a valid numerical equation.") from exc
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARYOPS:
            raise ExpressionParsingError("Use only + or - as unary operators.")
        operand = _evaluate_ast_node(node.operand)
        return _ALLOWED_UNARYOPS[op_type](operand)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ExpressionParsingError(
                "Only numeric values are allowed in equations."
            )
        return Decimal(str(node.value))
    if isinstance(node, ast.Num):  # pragma: no cover - legacy for Python <3.8
        return Decimal(str(node.n))
    raise ExpressionParsingError("Enter a valid numerical equation.")


def looks_like_expression(value: str) -> bool:
    """Return ``True`` if a value appears to be a math expression."""

    stripped = value.strip()
    if stripped.startswith(("+", "-")):
        stripped = stripped[1:].lstrip()
    return bool(_EXPRESSION_CHARS_RE.search(stripped))


def parse_decimal_string(value: str, expression_prefix: str = "=") -> Decimal:
    """Parse a string into a :class:`~decimal.Decimal`.

    Values that start with ``expression_prefix`` are evaluated as math expressions.
    ``ExpressionParsingError`` is raised when a value cannot be parsed.
    """

    if value is None:
        raise ExpressionParsingError("Enter a value.")

    text = str(value).strip()
    if not text:
        raise ExpressionParsingError("Enter a value.")

    if text.startswith(expression_prefix):
        expression = text[len(expression_prefix) :].strip()
        if not expression:
            raise ExpressionParsingError("Enter a calculation after '='.")
        if not _ALLOWED_EXPRESSION_RE.match(expression):
            raise ExpressionParsingError(
                "Use only numbers, parentheses, and +, -, *, /."
            )
        return evaluate_math_expression(expression)

    if looks_like_expression(text):
        raise ExpressionParsingError("To enter a calculation, start the value with '='.")

    def _normalize_number_string(raw: str) -> str:
        # Remove common whitespace characters used as thousands separators.
        normalized = raw.replace("\u00A0", " ")
        sign = ""
        if normalized and normalized[0] in "+-":
            sign, normalized = normalized[0], normalized[1:]
        normalized = normalized.strip().replace(" ", "")
        if not normalized:
            return sign + normalized

        last_comma = normalized.rfind(",")
        last_dot = normalized.rfind(".")

        decimal_sep: str | None = None
        if last_comma != -1 and last_dot != -1:
            decimal_sep = "," if last_comma > last_dot else "."
        elif last_comma != -1:
            decimal_sep = ","
        elif last_dot != -1:
            decimal_sep = "."

        digits = normalized
        if decimal_sep == ",":
            digits = digits.replace(".", "")
            digits = digits.replace(",", ".")
        elif decimal_sep == ".":
            digits = digits.replace(",", "")
        else:
            digits = digits.replace(",", "").replace(".", "")

        return sign + digits

    normalized_text = _normalize_number_string(text)

    try:
        return Decimal(normalized_text)
    except InvalidOperation as exc:
        raise ExpressionParsingError("Enter a valid number.") from exc


def coerce_decimal(
    raw_value: Any,
    *,
    default: Optional[Decimal] = None,
    expression_prefix: str = "=",
) -> Optional[Decimal]:
    """Best-effort conversion of user-provided values to :class:`Decimal`.

    ``default`` is returned if the value is empty or cannot be parsed.
    """

    if raw_value is None:
        return default

    if isinstance(raw_value, Decimal):
        return raw_value

    if isinstance(raw_value, (int, float)):
        try:
            return Decimal(str(raw_value))
        except InvalidOperation:
            return default

    text = str(raw_value).strip()
    if not text:
        return default

    try:
        return parse_decimal_string(text, expression_prefix=expression_prefix)
    except ExpressionParsingError:
        return default


def coerce_float(
    raw_value: Any,
    *,
    default: Optional[float] = None,
    expression_prefix: str = "=",
) -> Optional[float]:
    """Convert user-provided input to ``float`` while supporting equations."""

    decimal_value = coerce_decimal(
        raw_value, default=None, expression_prefix=expression_prefix
    )
    if decimal_value is None:
        return default
    try:
        return float(decimal_value)
    except (TypeError, ValueError, InvalidOperation):
        return default
