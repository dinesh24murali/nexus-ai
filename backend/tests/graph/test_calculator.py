"""The calculator tool: exact arithmetic, a syntax whitelist, and no way to run code (LLM-T15)."""

# ruff: noqa: RUF001 - the tests feed the unicode operators on purpose

import builtins
import contextlib
import random
import time
import warnings
from pathlib import Path

import pytest
from app.graph.tools.calculator import (
    MAX_EXPRESSION_CHARS,
    CalculatorArgs,
    CalculatorError,
    evaluate,
    run_calculator,
    tool_spec,
)
from pydantic import ValidationError


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2+2", "4"),
        ("0.1+0.2", "0.3"),  # decimal arithmetic: no binary floating point noise
        ("0.175 * 2348901", "411057.675"),
        ("2**10", "1024"),
        ("2**-2", "0.25"),
        ("10 % 3", "1"),
        ("-7 // 2", "-4"),
        ("(1 + 2) * (3 + 4)", "21"),
        ("--3", "3"),
        ("+4", "4"),
        ("6×7", "42"),
        ("10÷4", "2.5"),
        ("−5 + 2", "-3"),
        ("sqrt(16)", "4"),
        ("abs(-2.5)", "2.5"),
        ("round(3.14159, 2)", "3.14"),
        ("round(2.5)", "3"),  # half up, what a person expects
        ("round(-2.5)", "-3"),
        ("log(1)", "0"),
        ("log(1000, 10)", "3"),
        ("2**100", "1267650600228229401496703205376"),
        ("1 # a comment", "1"),
    ],
)
def test_it_computes_exactly(expression: str, expected: str) -> None:
    assert evaluate(expression) == expected


def test_a_repeating_fraction_is_marked_as_approximate() -> None:
    answer = evaluate("1/3")

    assert answer.startswith("approximately 0.33333333333")
    assert len(answer) < 80


def test_a_result_that_does_not_fit_the_precision_is_marked_as_approximate() -> None:
    assert evaluate("2**200").startswith("approximately 1606938044258990275541962092341162602522")


def test_an_irrational_result_is_marked_as_approximate() -> None:
    assert evaluate("sqrt(2)").startswith("approximately 1.41421356237309504880")


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "   ",
        "1/0",
        "1 % 0",
        "1 // 0",
        "sqrt(-1)",
        "log(0)",
        "log(-1)",
        "log(10, 1)",
        "0**0",
        "(-8)**0.5",
        "round(1, 2, 3)",
        "sqrt()",
        "sqrt(1, 2)",
        "1 +",
        "2,348,901",
        "^2",
        "2 ^ 3",
        "x",
        "pi",
    ],
)
def test_bad_input_is_a_calculator_error(expression: str) -> None:
    with pytest.raises(CalculatorError):
        evaluate(expression)


def test_an_error_message_never_echoes_the_expression() -> None:
    with pytest.raises(CalculatorError) as caught:
        evaluate("secret_name + 1")

    assert "secret_name" not in str(caught.value)


@pytest.mark.parametrize(
    "expression",
    [
        "9**9**9",
        "9**9**9**9",
        "10**10000",
        "2**1000000",
        "(10**100)**100",
        "1e999999999",
        "1e400 * 1e400",
        "10**1000 * 10",
        "2 ** (10**10)",
    ],
)
def test_a_result_too_large_to_hold_is_refused_quickly(expression: str) -> None:
    started = time.perf_counter()
    with pytest.raises(CalculatorError):
        evaluate(expression)
    assert time.perf_counter() - started < 1.0


def test_an_expression_over_the_limit_is_refused() -> None:
    with pytest.raises(CalculatorError):
        evaluate("1+" * 120 + "1")  # 241 chars
    assert MAX_EXPRESSION_CHARS == 200


def test_deep_nesting_is_refused() -> None:
    with pytest.raises(CalculatorError):
        evaluate("-(" * 60 + "1" + ")" * 60)


def test_parentheses_alone_are_harmless() -> None:
    assert evaluate("(" * 99 + "1" + ")" * 99) == "1"


def test_too_many_operations_are_refused() -> None:
    with pytest.raises(CalculatorError):
        evaluate("+".join(["1"] * 99))  # 197 chars, 99 numbers


# -- the safety property: nothing but arithmetic is ever evaluated --------------------------------

ATTACKS = [
    "__import__('os').system('id')",
    "__import__('os')",
    "().__class__",
    "().__class__.__bases__[0].__subclasses__()",
    "[].__class__.__base__",
    "(1).real",
    "(1).__add__(2)",
    "sqrt.__call__(4)",
    "sqrt.__globals__",
    "sqrt(4)(5)",
    "abs.__self__",
    "print(1)",
    "open('x')",
    "eval('1')",
    "exec('1')",
    "compile('1', 'x', 'eval')",
    "getattr(1, 'real')",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "input()",
    "breakpoint()",
    "lambda: 1",
    "(lambda: 1)()",
    "[1, 2]",
    "(1, 2)",
    "{1: 2}",
    "{1, 2}",
    "'a' * 5",
    '"abc"',
    "b'1'",
    "1j",
    "True",
    "False",
    "None",
    "...",
    "1 if 1 else 2",
    "1 < 2",
    "1 == 1",
    "not 1",
    "1 and 2",
    "1 or 2",
    "1 in [1]",
    "1 is 1",
    "[x for x in range(3)]",
    "{x: x for x in range(3)}",
    "(x for x in range(3))",
    "f'{1}'",
    "f'{__import__(\"os\")}'",
    "(x := 1)",
    "sqrt(*[4])",
    "sqrt(**{'x': 4})",
    "sqrt(x=4)",
    "round(1, ndigits=2)",
    "1 @ 2",
    "1 << 2",
    "1 >> 2",
    "~1",
    "1 | 2",
    "1 & 2",
    "1 ^ 2",
    "1[0]",
    "'1'[0]",
    "[1][0]",
    "await x",
    "yield",
    "1; import os",
    "import os",
    "x = 1",
    "x += 1",
    "del x",
    "assert 1",
    "pass",
    "1\n2",
    "1 +\n 2 +\n __import__('os')",
    "*a",
    "**a",
    "sqrt",
    "SQRT(4)",
    "Sqrt(4)",
    "math.sqrt(4)",
    "decimal.Decimal(1)",
    "Decimal(1)",
    "Num('1')",
]


@pytest.mark.parametrize("expression", ATTACKS)
def test_every_attack_is_refused(expression: str) -> None:
    with pytest.raises(CalculatorError):
        evaluate(expression)


def _forbidden(*_: object, **__: object) -> None:
    raise AssertionError("the calculator must never call this builtin")


def test_it_never_uses_eval_or_exec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builtins, "eval", _forbidden)
    monkeypatch.setattr(builtins, "exec", _forbidden)

    assert evaluate("2*(3+4)") == "14"
    for attack in ATTACKS:
        with pytest.raises(CalculatorError):
            evaluate(attack)


TOKENS = [
    *"0123456789",
    "+", "-", "*", "/", "//", "%", "**", "(", ")", ",", ".", " ", "×", "÷", "−",
    "sqrt", "log", "round", "abs", "__import__", "open", "eval", "exec", "os", "x",
    "'", '"', "[", "]", "{", "}", ":", "=", "lambda", "for", "in", "if", "else", "import",
    "\n", ";", "#", "@", "<", ">", "!", "~", "&", "|", "^", "e", "j", "1e9", "9" * 30,
]  # fmt: skip


def test_random_input_either_computes_or_is_refused(tmp_path: Path) -> None:
    """Seeded fuzzing: 4000 pieces of token soup. The result is a string or a CalculatorError,
    never another exception, never slow, and no file appears (an `open` that ran would show)."""
    rng = random.Random(20261121)
    sentinel = tmp_path / "pwned"
    started = time.perf_counter()
    computed = 0
    for _ in range(4000):
        text = "".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 40)))
        try:
            assert isinstance(evaluate(text), str)
            computed += 1
        except CalculatorError:
            pass
    assert time.perf_counter() - started < 20
    assert not sentinel.exists()
    assert computed > 0  # the generator does produce valid arithmetic now and then


def test_random_attack_shapes_never_write_a_file(tmp_path: Path) -> None:
    sentinel = tmp_path / "pwned"
    rng = random.Random(7)
    shapes = [
        "open({p!r}, 'w')",
        "__import__('pathlib').Path({p!r}).touch()",
        "[open({p!r}, 'w') for _ in [1]]",
        "(lambda: open({p!r}, 'w'))()",
        "().__class__.__base__.__subclasses__()[0]",
        "sqrt(open({p!r}, 'w'))",
        "1 + open({p!r}, 'w')",
    ]
    for _ in range(300):
        expression = rng.choice(shapes).format(p=str(sentinel))
        padding = rng.choice(["", " ", "1 + ", "2 * ", "(", "((", "-"])
        with pytest.raises(CalculatorError):
            evaluate(padding + expression)
    assert not sentinel.exists()


# -- the tool ------------------------------------------------------------------------------------


def test_the_tool_definition_matches_the_documented_arguments() -> None:
    spec = tool_spec()

    assert spec.name == "calculator"
    assert set(spec.args) == {"expression"}
    assert "exactly" in spec.description.lower()


def test_the_arguments_are_bounded() -> None:
    assert CalculatorArgs(expression="1+1").expression == "1+1"
    with pytest.raises(ValidationError):
        CalculatorArgs(expression="")
    with pytest.raises(ValidationError):
        CalculatorArgs(expression="1" * 201)


async def test_the_tool_returns_the_result_as_plain_text() -> None:
    outcome = await run_calculator({"expression": "17.5 * 3"})

    assert outcome.status == "completed"
    assert outcome.content == "52.5"
    assert outcome.summary == "= 52.5"


async def test_a_refused_expression_is_an_error_the_model_can_read() -> None:
    outcome = await run_calculator({"expression": "__import__('os')"})

    assert outcome.status == "failed"
    assert "__import__" not in outcome.content
    assert "arithmetic" in outcome.content.lower()


async def test_bad_arguments_name_the_field_and_not_the_value() -> None:
    outcome = await run_calculator({"expression": "1" * 300})

    assert outcome.status == "failed"
    assert "expression" in outcome.content
    assert "1" * 50 not in outcome.content


async def test_a_missing_argument_is_an_error_not_a_crash() -> None:
    outcome = await run_calculator({})

    assert outcome.status == "failed"
    assert "expression" in outcome.content


async def test_a_slow_evaluation_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.graph.tools.calculator as module

    def slow(_: str) -> str:
        time.sleep(0.5)
        return "1"

    monkeypatch.setattr(module, "evaluate", slow)

    outcome = await run_calculator({"expression": "1+1"}, timeout_s=0.05)

    assert outcome.status == "timed_out"


@pytest.mark.parametrize("expression", ["1if 2else 3", "0x1for x in y", "1_000a", "1jx", "1 is 1"])
def test_odd_input_produces_no_python_warnings(expression: str) -> None:
    """The parser warns about things like `1if`; model-written junk must not fill the logs."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with contextlib.suppress(CalculatorError):
            evaluate(expression)

    assert [str(w.message) for w in caught] == []
