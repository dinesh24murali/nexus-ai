"""`calculator` (doc 08 section 5): exact arithmetic with no way to run code (LLM-T15).

The expression is parsed to an AST and walked by a whitelist: numbers, `+ - * / // % **`, unary
plus and minus, parentheses, and the functions `sqrt`, `log`, `round` and `abs`. Every other node
(names, attributes, calls to anything else, subscripts, lambdas, comprehensions, strings, ...) is
refused, and nothing is ever passed to `eval`, `exec` or `compile` for execution. Numbers are
`Decimal`, so `0.1 + 0.2` is `0.3`; a result that does not fit the precision is marked
"approximately". Limits keep every input cheap: 200 characters, 120 nodes, depth 40, results below
1e300 (so `9**9**9` is refused at once instead of computed).
"""

import ast
import asyncio
import warnings
from collections.abc import Callable
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    Overflow,
)
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.graph.tools.outcome import ToolOutcome, invalid_arguments

TOOL_NAME = "calculator"
MAX_EXPRESSION_CHARS = 200
MAX_NODES = 120
MAX_DEPTH = 40
PRECISION = 50
MAX_EXPONENT = 300  # results are below 1e300 (and above 1e-300)
TIMEOUT_S = 1.0  # doc 08 section 5
_MAX_ROUND_DIGITS = 30
_SNAP = Decimal(10) ** -(PRECISION - 8)

_SYNTAX_HELP = (
    "Could not evaluate that. Only arithmetic is supported: numbers, + - * / // % ** "
    "(power), parentheses and the functions sqrt, log, round and abs."
)
_OPERATOR_SYMBOLS = str.maketrans({"×": "*", "÷": "/", "−": "-"})  # noqa: RUF001


class CalculatorError(ValueError):
    """The expression is not supported arithmetic. The message is safe to show the model: it
    never contains the expression (it is model-supplied text)."""


def _refuse(reason: str = _SYNTAX_HELP) -> CalculatorError:
    return CalculatorError(reason)


class _Evaluator:
    def __init__(self) -> None:
        self.ctx = Context(
            prec=PRECISION,
            Emax=MAX_EXPONENT,
            Emin=-MAX_EXPONENT,
            rounding=ROUND_HALF_UP,
            traps=[InvalidOperation, Overflow],
        )
        self.ctx.clear_flags()
        self.functions: dict[str, Callable[..., Decimal]] = {
            "sqrt": self._sqrt,
            "log": self._log,
            "round": self._round,
            "abs": self._abs,
        }

    # -- walking -----------------------------------------------------------------------------

    def value(self, node: ast.AST, depth: int = 0) -> Decimal:
        if depth > MAX_DEPTH:
            raise _refuse("That expression is nested too deeply.")
        match node:
            case ast.Expression(body=body):
                return self.value(body, depth + 1)
            case ast.Constant(value=value):
                return self._number(value)
            case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                return self.ctx.plus(self.value(operand, depth + 1))
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return self.ctx.minus(self.value(operand, depth + 1))
            case ast.BinOp(left=left, op=op, right=right):
                return self._binary(op, self.value(left, depth + 1), self.value(right, depth + 1))
            case ast.Call(func=ast.Name(id=name), args=args, keywords=[]) if (
                name in self.functions and not any(isinstance(a, ast.Starred) for a in args)
            ):
                return self.functions[name](*(self.value(a, depth + 1) for a in args))
            case _:
                raise _refuse()

    def _number(self, value: object) -> Decimal:
        # bool is an int subclass: `True` must not become 1
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise _refuse()
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise _refuse("That number is too large.")
            return self.ctx.create_decimal(repr(value))
        return self.ctx.create_decimal(value)

    def _binary(self, op: ast.operator, left: Decimal, right: Decimal) -> Decimal:
        ctx = self.ctx
        match op:
            case ast.Add():
                return ctx.add(left, right)
            case ast.Sub():
                return ctx.subtract(left, right)
            case ast.Mult():
                return ctx.multiply(left, right)
            case ast.Div():
                if right == 0:
                    raise _refuse("Division by zero.")
                return ctx.divide(left, right)
            case ast.FloorDiv():
                if right == 0:
                    raise _refuse("Division by zero.")
                return self._floor_divide(left, right)
            case ast.Mod():
                if right == 0:
                    raise _refuse("Division by zero.")
                return self._modulo(left, right)
            case ast.Pow():
                return ctx.power(left, right)
            case _:
                raise _refuse()

    def _floor_divide(self, left: Decimal, right: Decimal) -> Decimal:
        # Python semantics (rounds toward minus infinity); Decimal's divide_int truncates
        quotient = self.ctx.divide_int(left, right)
        remainder = self.ctx.remainder(left, right)
        if remainder != 0 and (remainder < 0) != (right < 0):
            return self.ctx.subtract(quotient, Decimal(1))
        return quotient

    def _modulo(self, left: Decimal, right: Decimal) -> Decimal:
        # Python semantics (the result takes the sign of the divisor), unlike Decimal's remainder
        remainder = self.ctx.remainder(left, right)
        if remainder != 0 and (remainder < 0) != (right < 0):
            return self.ctx.add(remainder, right)
        return remainder

    # -- functions ---------------------------------------------------------------------------

    def _sqrt(self, x: Decimal) -> Decimal:
        if x < 0:
            raise _refuse("The square root of a negative number is not a real number.")
        return self.ctx.sqrt(x)

    def _abs(self, x: Decimal) -> Decimal:
        return self.ctx.abs(x)

    def _log(self, x: Decimal, base: Decimal | None = None) -> Decimal:
        if x <= 0 or (base is not None and (base <= 0 or base == 1)):
            raise _refuse("The logarithm needs a positive number (and a base above 0 and not 1).")
        wide = Context(prec=PRECISION + 10, Emax=MAX_EXPONENT, Emin=-MAX_EXPONENT)
        result = wide.ln(x) if base is None else wide.divide(wide.ln(x), wide.ln(base))
        nearest = result.to_integral_value()
        if abs(result - nearest) < _SNAP:  # log(1000, 10) is 3, not 2.99999...
            return self.ctx.plus(nearest)
        self.ctx.flags[Inexact] = True
        return self.ctx.plus(result)

    def _round(self, x: Decimal, digits: Decimal = Decimal(0)) -> Decimal:
        places = int(digits) if digits == digits.to_integral_value() else None
        if places is None or not -_MAX_ROUND_DIGITS <= places <= _MAX_ROUND_DIGITS:
            raise _refuse("round() needs a whole number of digits between -30 and 30.")
        approximate_before = self.ctx.flags[Inexact]
        rounded = self.ctx.quantize(x, Decimal(1).scaleb(-places))
        # Dropping digits is what round() was asked to do, not a loss of precision
        self.ctx.flags[Inexact] = approximate_before
        return rounded


def _depth_and_size_ok(tree: ast.AST) -> None:
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise _refuse("That expression is too long. Split it into smaller steps.")


def _format(value: Decimal, *, inexact: bool) -> str:
    if value == 0:
        text = "0"
    else:
        text = format(value.normalize(Context(prec=PRECISION, Emax=MAX_EXPONENT)), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
    return f"approximately {text}" if inexact else text


def evaluate(expression: str) -> str:
    """The value of an arithmetic expression as text. Raises `CalculatorError` for anything else."""
    text = expression.strip().translate(_OPERATOR_SYMBOLS)
    if not text:
        raise _refuse("The expression is empty.")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise _refuse(f"The expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise _refuse()  # one line only: no newlines, no control characters
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # `1if x else y` and friends: junk in, no log noise
            tree = ast.parse(text, mode="eval")
    except SyntaxError, ValueError, MemoryError, RecursionError:
        raise _refuse() from None
    _depth_and_size_ok(tree)
    evaluator = _Evaluator()
    try:
        result = evaluator.value(tree)
    except CalculatorError:
        raise
    except RecursionError:
        raise _refuse("That expression is nested too deeply.") from None
    except DecimalException, ArithmeticError, TypeError, ValueError, OverflowError:
        raise _refuse("That result is not a number this calculator can give.") from None
    return _format(result, inexact=bool(evaluator.ctx.flags[Inexact]))


class CalculatorArgs(BaseModel):
    """Arguments the model may pass. Unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    expression: str = Field(
        min_length=1,
        max_length=MAX_EXPRESSION_CHARS,
        description="An arithmetic expression, for example (17.5 / 100) * 2348901.",
    )


def tool_spec() -> StructuredTool:
    """The tool definition bound to the chat model; the `tools` node runs `run_calculator`."""

    def unused(**_: Any) -> str:
        raise RuntimeError("calculator runs in the tools node")

    return StructuredTool.from_function(
        func=unused,
        name=TOOL_NAME,
        description=(
            "Evaluate an arithmetic expression exactly. Supports + - * / // % ** (power), "
            "parentheses and sqrt, log, round, abs. Use it for any calculation that is not "
            "trivial instead of computing in your head."
        ),
        args_schema=CalculatorArgs,
    )


async def run_calculator(args: dict[str, Any], *, timeout_s: float = TIMEOUT_S) -> ToolOutcome:
    try:
        parsed = CalculatorArgs.model_validate(args)
    except ValidationError as error:
        return invalid_arguments(error)
    try:
        async with asyncio.timeout(timeout_s):
            answer = await asyncio.to_thread(evaluate, parsed.expression)
    except TimeoutError:
        return ToolOutcome("The calculation timed out.", "timed_out", "timed out")
    except CalculatorError as error:
        return ToolOutcome(str(error), "failed", "could not calculate")
    return ToolOutcome(answer, "completed", f"= {answer}")
