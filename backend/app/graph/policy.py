"""Which tools need the user's approval before they run (doc 08 section 2.2).

`web_search` sends text to a third party, so every call waits for the user (v1: per call).
Nothing defaults to "no": `tests/graph/test_policy.py` fails when a tool the graph can offer has no
entry here, so a new tool cannot ship without somebody deciding.
"""

from typing import Literal

Approval = Literal["always", "never"]

TOOL_POLICY: dict[str, Approval] = {
    "search_my_files": "never",
    "calculator": "never",
    "web_search": "always",
}


def needs_approval(tool_name: str) -> bool:
    """True when every call to `tool_name` must be approved. An unknown name needs none: it never
    runs (the `tools` node answers it with an error), so there is nothing to approve."""
    return TOOL_POLICY.get(tool_name) == "always"
