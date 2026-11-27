from datetime import UTC, datetime
from decimal import Decimal

from app.core.budgets import budget_exceeded, exceeded_scope, next_reset, retry_after_s
from app.core.config import Settings


def test_budgets_default_to_the_documented_amounts() -> None:
    s = Settings(_env_file=None)
    assert s.user_daily_budget_usd == Decimal("0.50")
    assert s.global_daily_budget_usd == Decimal("5.00")
    assert s.chat_rate_limit_per_min == 30


def test_budgets_are_read_from_the_environment(monkeypatch: object) -> None:
    import pytest

    assert isinstance(monkeypatch, pytest.MonkeyPatch)
    monkeypatch.setenv("USER_DAILY_BUDGET_USD", "1.25")
    monkeypatch.setenv("GLOBAL_DAILY_BUDGET_USD", "9")
    s = Settings(_env_file=None)
    assert s.user_daily_budget_usd == Decimal("1.25")
    assert s.global_daily_budget_usd == Decimal("9")


def test_the_budget_resets_at_the_next_utc_midnight() -> None:
    now = datetime(2026, 11, 21, 23, 59, 30, tzinfo=UTC)
    assert next_reset(now) == datetime(2026, 11, 22, tzinfo=UTC)
    assert retry_after_s(now) == 30


def test_retry_after_rounds_up_and_is_never_zero() -> None:
    assert retry_after_s(datetime(2026, 11, 21, 23, 59, 59, 500_000, tzinfo=UTC)) == 1
    assert retry_after_s(datetime(2026, 11, 22, 0, 0, 0, tzinfo=UTC)) == 86_400


def test_under_both_budgets_nothing_is_exceeded() -> None:
    assert (
        exceeded_scope(
            user_spend=Decimal("0.49"),
            user_budget=Decimal("0.50"),
            global_spend=Decimal("4.99"),
            global_budget=Decimal("5"),
        )
        is None
    )


def test_spending_exactly_the_budget_blocks() -> None:
    assert (
        exceeded_scope(
            user_spend=Decimal("0.50"),
            user_budget=Decimal("0.50"),
            global_spend=Decimal(0),
            global_budget=Decimal("5"),
        )
        == "user"
    )


def test_the_global_cap_is_reported_before_the_user_budget() -> None:
    assert (
        exceeded_scope(
            user_spend=Decimal("9"),
            user_budget=Decimal("0.50"),
            global_spend=Decimal("5"),
            global_budget=Decimal("5"),
        )
        == "global"
    )


def test_the_error_names_the_scope_and_when_it_resets() -> None:
    now = datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
    err = budget_exceeded("user", now)
    assert (err.status_code, err.code) == (429, "BUDGET_EXCEEDED")
    assert err.details == {"scope": "user", "resets_at": "2026-11-22T00:00:00Z"}
    assert err.headers == {"Retry-After": str(6 * 3600)}
    assert budget_exceeded("global", now).details == {
        "scope": "global",
        "resets_at": "2026-11-22T00:00:00Z",
    }
    assert budget_exceeded("user", now).message != budget_exceeded("global", now).message
