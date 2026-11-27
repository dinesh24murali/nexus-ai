"""Daily spend budgets: the policy half (docs/02_outputs/02 FS-008, 08 section 10).

Two limits in USD per UTC day: one per user and one for the whole instance. Both are checked before
a run starts; a run that has started is never cut off, so spend can end slightly over a budget.
Reading the spend from the database is `services/usage.py`; this module has no I/O.
"""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from app.core.errors import ApiError

Scope = Literal["user", "global"]

_MESSAGES: dict[Scope, str] = {
    "user": "You've reached your daily usage limit. It resets at 00:00 UTC.",
    "global": "Nexus has reached its daily limit for everyone. It resets at 00:00 UTC.",
}


def next_reset(now: datetime) -> datetime:
    """The next 00:00 UTC after `now`."""
    today = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=1)


def retry_after_s(now: datetime) -> int:
    return max(1, math.ceil((next_reset(now) - now).total_seconds()))


def exceeded_scope(
    *,
    user_spend: Decimal,
    user_budget: Decimal,
    global_spend: Decimal,
    global_budget: Decimal,
) -> Scope | None:
    """Which limit blocks a new run, or None. At exactly the budget a run is blocked. The global
    cap is reported first: it stops everyone, so it is the more useful explanation."""
    if global_spend >= global_budget:
        return "global"
    if user_spend >= user_budget:
        return "user"
    return None


def budget_exceeded(scope: Scope, now: datetime) -> ApiError:
    return ApiError(
        429,
        "BUDGET_EXCEEDED",
        _MESSAGES[scope],
        details={
            "scope": scope,
            "resets_at": next_reset(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        headers={"Retry-After": str(retry_after_s(now))},
    )
