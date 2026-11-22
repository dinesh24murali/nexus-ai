"""The pipelines a message can be routed to (docs/02_outputs/08 section 2.5, ADR-0010).

`PIPELINES` is what this build implements. The router may name an intent that is not in it
(`generate_document`, `deep_research` arrive with TASK-041 and TASK-043); dispatch then answers as
`general_chat`, so a later build only has to add the name here.
"""

from typing import Literal, get_args

Intent = Literal["general_chat", "manage_memory", "generate_document", "deep_research"]
INTENTS: tuple[str, ...] = get_args(Intent)
FALLBACK_INTENT: Intent = "general_chat"

PIPELINES: frozenset[str] = frozenset({"general_chat", "manage_memory"})
