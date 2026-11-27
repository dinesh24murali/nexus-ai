"""Which tools need the user's approval (doc 08 section 2.2)."""

import pytest
from app.graph import policy
from app.graph.tools import calculator, search_my_files, web_search


def test_every_web_search_needs_approval() -> None:
    assert policy.needs_approval("web_search") is True


@pytest.mark.parametrize("name", ["search_my_files", "calculator"])
def test_the_other_tools_run_without_approval(name: str) -> None:
    assert policy.needs_approval(name) is False


def test_an_unknown_tool_needs_no_approval_because_it_never_runs() -> None:
    assert policy.needs_approval("delete_everything") is False


def test_every_tool_the_graph_can_offer_has_an_explicit_policy() -> None:
    """A new tool must be given a decision here, or this fails (nothing defaults to "no")."""
    offered = {search_my_files.TOOL_NAME, web_search.TOOL_NAME, calculator.TOOL_NAME}

    assert set(policy.TOOL_POLICY) == offered
    assert set(policy.TOOL_POLICY.values()) <= {"always", "never"}
