"""Deterministic decimal calculator with no code-evaluation surface."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re
from typing import Any, Sequence


MAX_EXPRESSION_CHARACTERS = 512
MAX_OPERATIONS = 50
MAX_PARENTHESES_DEPTH = 20
DEFAULT_DIVISION_DECIMAL_PLACES = 12
MAX_DECIMAL_PLACES = 24


class CalculationError(ValueError):
    """A safe calculator input could not be parsed or executed."""


@dataclass(frozen=True)
class CalculationRecord:
    input_text: str
    normalized_expression: str
    operands: tuple[str, ...]
    operators: tuple[str, ...]
    operation: str
    result: str
    unit: str | None
    rounding_rule: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["operands"] = list(self.operands)
        payload["operators"] = list(self.operators)
        return payload

    def render(self) -> str:
        suffix = "%" if self.unit == "%" else f" {self.unit}" if self.unit else ""
        return f"{self.normalized_expression} = {self.result}{suffix}"


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+"
_NUMBER_PATTERN = re.compile(rf"(?<![\w.])[-+]?(?:{_NUMBER})(?![\w.])")
_ROUNDING_PATTERN = re.compile(
    r"(?:,?\s*)?(?:rounded?|round)\s+to\s+(\d+)\s+decimal\s+places?",
    re.IGNORECASE,
)


def infer_calculation_operation(query: str) -> str | None:
    """Return one unambiguous allow-listed operation named by the user."""
    lowered = " ".join(query.casefold().split())
    matches: set[str] = set()
    cues = {
        "growth_rate": (r"\bgrowth\s+rate\b", r"\bpercentage\s+(?:increase|decrease|change)\b"),
        "difference": (r"\bdifference\b", r"\bsubtract\b", r"\bhow\s+much\s+(?:higher|lower)\b"),
        "ratio": (r"\bratio\b", r"\bdivide\b"),
        "percentage": (r"\bas\s+a\s+percentage\s+of\b", r"\bpercent\s+of\b"),
        "sum": (r"\bsum\b", r"\btotal\s+of\b", r"\badd\b"),
    }
    for operation, patterns in cues.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            matches.add(operation)
    return next(iter(matches)) if len(matches) == 1 else None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _parse_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value.replace(",", ""))
    except InvalidOperation as error:
        raise CalculationError("The calculation contains an invalid number.") from error
    if not parsed.is_finite():
        raise CalculationError("Calculator operands must be finite decimal numbers.")
    return parsed


def parse_evidence_number(value: str) -> Decimal:
    """Parse one quoted filing value without accepting surrounding prose."""
    normalized = value.strip()
    negative = normalized.startswith("(") and normalized.endswith(")")
    if negative:
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"^(?:US)?[$€£]\s*", "", normalized, flags=re.I)
    normalized = normalized.rstrip("%").strip()
    if not re.fullmatch(rf"[-+]?(?:{_NUMBER})", normalized):
        raise CalculationError("An evidence operand is not a plain decimal value.")
    parsed = _parse_decimal(normalized)
    return -parsed if negative else parsed


class _ExpressionParser:
    def __init__(self, expression: str) -> None:
        self.tokens = self._tokenize(expression)
        self.position = 0
        self.operands: list[str] = []
        self.operators: list[str] = []
        self.depth = 0

    @staticmethod
    def _tokenize(expression: str) -> list[_Token]:
        if not expression or len(expression) > MAX_EXPRESSION_CHARACTERS:
            raise CalculationError("The calculation expression is empty or too long.")
        tokens: list[_Token] = []
        position = 0
        while position < len(expression):
            if expression[position].isspace():
                position += 1
                continue
            number = re.match(_NUMBER, expression[position:])
            if number:
                value = number.group(0)
                tokens.append(_Token("number", value))
                position += len(value)
                continue
            value = expression[position]
            if value in "+-*/()":
                tokens.append(_Token(value, value))
                position += 1
                continue
            raise CalculationError("The calculation contains unsupported characters.")
        tokens.append(_Token("end", ""))
        return tokens

    def parse(self) -> Decimal:
        with localcontext() as context:
            context.prec = 64
            result = self._expression()
        if self.current.kind != "end":
            raise CalculationError("The calculation expression is malformed.")
        if not self.operators:
            raise CalculationError("The calculation must contain an operation.")
        if len(self.operators) > MAX_OPERATIONS:
            raise CalculationError("The calculation contains too many operations.")
        return result

    @property
    def current(self) -> _Token:
        return self.tokens[self.position]

    def consume(self, kind: str) -> _Token:
        if self.current.kind != kind:
            raise CalculationError("The calculation expression is malformed.")
        token = self.current
        self.position += 1
        return token

    def _expression(self) -> Decimal:
        result = self._term()
        while self.current.kind in {"+", "-"}:
            operator = self.consume(self.current.kind).value
            self.operators.append(operator)
            operand = self._term()
            result = result + operand if operator == "+" else result - operand
        return result

    def _term(self) -> Decimal:
        result = self._factor()
        while self.current.kind in {"*", "/"}:
            operator = self.consume(self.current.kind).value
            self.operators.append(operator)
            operand = self._factor()
            if operator == "/":
                if operand == 0:
                    raise CalculationError("Division by zero is not allowed.")
                result /= operand
            else:
                result *= operand
        return result

    def _factor(self) -> Decimal:
        if self.current.kind in {"+", "-"}:
            operator = self.consume(self.current.kind).value
            value = self._factor()
            return value if operator == "+" else -value
        if self.current.kind == "number":
            raw = self.consume("number").value
            value = _parse_decimal(raw)
            self.operands.append(_decimal_text(value))
            return value
        if self.current.kind == "(":
            self.depth += 1
            if self.depth > MAX_PARENTHESES_DEPTH:
                raise CalculationError("The calculation is nested too deeply.")
            self.consume("(")
            value = self._expression()
            self.consume(")")
            self.depth -= 1
            return value
        raise CalculationError("The calculation expression is malformed.")


def _extract_rounding(query: str) -> tuple[str, int | None]:
    match = _ROUNDING_PATTERN.search(query)
    if not match:
        return query, None
    decimal_places = int(match.group(1))
    if decimal_places > MAX_DECIMAL_PLACES:
        raise CalculationError(
            f"Rounding is limited to {MAX_DECIMAL_PLACES} decimal places."
        )
    return (query[: match.start()] + query[match.end() :]).strip(), decimal_places


def _numbers(text: str) -> list[str]:
    return [match.group(0) for match in _NUMBER_PATTERN.finditer(text)]


def _expression_from_query(query: str) -> tuple[str, str, str | None]:
    normalized = query.strip().rstrip("?.!").strip()
    lowered = normalized.casefold()
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "percentage",
            re.compile(rf"^\D*?({_NUMBER})\s+as\s+a\s+percentage\s+of\s+({_NUMBER})$", re.I),
        ),
        (
            "percentage_of",
            re.compile(rf"^\D*?({_NUMBER})\s*%\s+of\s+({_NUMBER})$", re.I),
        ),
        (
            "difference",
            re.compile(rf"^\D*?difference\s+between\s+({_NUMBER})\s+and\s+({_NUMBER})$", re.I),
        ),
        (
            "ratio",
            re.compile(rf"^\D*?ratio\s+of\s+({_NUMBER})\s+to\s+({_NUMBER})$", re.I),
        ),
        (
            "subtract",
            re.compile(rf"^\D*?subtract\s+({_NUMBER})\s+from\s+({_NUMBER})$", re.I),
        ),
        (
            "multiply",
            re.compile(rf"^\D*?multiply\s+({_NUMBER})\s+by\s+({_NUMBER})$", re.I),
        ),
        (
            "divide",
            re.compile(rf"^\D*?divide\s+({_NUMBER})\s+by\s+({_NUMBER})$", re.I),
        ),
    )
    for operation, pattern in patterns:
        match = pattern.fullmatch(normalized)
        if not match:
            continue
        first, second = match.groups()
        if operation == "percentage":
            return f"({first} / {second}) * 100", operation, "%"
        if operation == "percentage_of":
            return f"({first} / 100) * {second}", operation, None
        if operation in {"difference", "subtract"}:
            left, right = (second, first) if operation == "subtract" else (first, second)
            return f"{left} - {right}", operation, None
        if operation == "ratio":
            return f"{first} / {second}", operation, None
        operator = "*" if operation == "multiply" else "/"
        return f"{first} {operator} {second}", operation, None

    if re.search(r"\b(?:sum|total)\s+of\b", lowered):
        values = _numbers(normalized.split("of", 1)[1])
        if len(values) < 2:
            raise CalculationError("A sum needs at least two numeric operands.")
        return " + ".join(values), "sum", None

    expression = re.sub(
        r"^(?:what\s+is|calculate|compute)\s+", "", normalized, flags=re.I
    ).strip()
    expression = expression.replace("×", "*").replace("÷", "/")
    if not expression:
        raise CalculationError("No arithmetic expression was provided.")
    return expression, "expression", None


class CalculatorTool:
    """Execute bounded arithmetic and return an auditable typed record."""

    def calculate_query(self, query: str) -> CalculationRecord:
        without_rounding, requested_places = _extract_rounding(query)
        expression, operation, unit = _expression_from_query(without_rounding)
        parser = _ExpressionParser(expression)
        result = parser.parse()
        places = requested_places
        if places is None and "/" in parser.operators and result.as_tuple().exponent < -12:
            places = DEFAULT_DIVISION_DECIMAL_PLACES
        if places is not None:
            quantum = Decimal(1).scaleb(-places)
            result = result.quantize(quantum, rounding=ROUND_HALF_EVEN)
            rounding_rule = f"round-half-even to {places} decimal places"
        else:
            rounding_rule = "exact decimal result"
        normalized_expression = " ".join(expression.replace(",", "").split())
        return CalculationRecord(
            input_text=query,
            normalized_expression=normalized_expression,
            operands=tuple(parser.operands),
            operators=tuple(parser.operators),
            operation=operation,
            result=_decimal_text(result),
            unit=unit,
            rounding_rule=rounding_rule,
        )

    def calculate_operation(
        self,
        operation: str,
        operands: Sequence[str],
        *,
        unit: str | None = None,
        decimal_places: int | None = None,
        input_text: str = "evidence-derived calculation",
    ) -> CalculationRecord:
        if len(operands) < 2:
            raise CalculationError("Evidence-derived arithmetic needs at least two operands.")
        if operation == "difference" and len(operands) == 2:
            expression = f"{operands[0]} - {operands[1]}"
        elif operation == "ratio" and len(operands) == 2:
            expression = f"{operands[0]} / {operands[1]}"
        elif operation == "percentage" and len(operands) == 2:
            expression = f"({operands[0]} / {operands[1]}) * 100"
            unit = "%"
        elif operation == "growth_rate" and len(operands) == 2:
            expression = f"(({operands[1]} - {operands[0]}) / {operands[0]}) * 100"
            unit = "%"
        elif operation == "sum":
            expression = " + ".join(operands)
        else:
            raise CalculationError("The requested calculation operation is unsupported.")
        query = expression
        if decimal_places is not None:
            query += f" round to {decimal_places} decimal places"
        record = self.calculate_query(query)
        return CalculationRecord(
            input_text=input_text,
            normalized_expression=record.normalized_expression,
            operands=record.operands,
            operators=record.operators,
            operation=operation,
            result=record.result,
            unit=unit,
            rounding_rule=record.rounding_rule,
        )
