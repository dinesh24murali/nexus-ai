"""Memory by chat, the parts that are pure (docs/02_outputs/08 sections 2.5 and 7; TASK-039).

`manage_memory` answers from templates, never from a model: what to list, how a saved text is
worded and filed, which memories a "forget ..." request may mean, and what the reply says. Nothing
here touches a store, so the rules are tested without one. Memory text is user data: it is shown as
plain text (angle brackets escaped), and nothing here logs it.
"""

import re
from collections.abc import Sequence
from html import escape

from app.memory import policy
from app.memory.store import Memory

FORGET_MIN_SIMILARITY = 0.75  # a candidate must be at least this close to what the user typed
FORGET_CLEAR_MARGIN = 0.1  # the best match is "the one" when it leads the next by this much
FORGET_MAX_CANDIDATES = 3
LIST_LIMIT = 15  # memories shown in a list reply; Settings -> Memory shows all of them

SETTINGS_HINT = "You can review and edit them in Settings → Memory."

NOTICE_OFF = "Memory is turned off for your account. You can turn it on in Settings → Memory."
NOTICE_WRITES_OFF = "Saving and forgetting memories is switched off right now. Listing still works."
USAGE_SAVE = "Tell me what to remember, for example: `/memory remember I prefer metric units`."
USAGE_FORGET = "Tell me what to forget, for example: `/memory forget my timezone`."
NOT_FOUND = (
    "I couldn't find a saved memory that matches that. Say `/memory` to see what I remember."
)
KEPT = "OK, I didn't delete anything."
NOT_DELETED = "That memory was already gone, so nothing was deleted."

_REFUSALS = {
    "empty": "There was nothing to save.",
    "too_long": "That is too long for a memory (500 characters at most). Try a shorter note.",
    "instruction_like": (
        "I can't save that: a memory is a fact about you, not an instruction for me. "
        "Tell me a fact instead, for example `/memory remember I prefer metric units`."
    ),
}
_SENSITIVE = (
    "I can't save that: it looks like sensitive personal information (health, religion, "
    "sexuality, politics, an exact location, or credentials). I don't keep those."
)

# "I can ...", "I don't ...": not a plain verb, so the sentence is kept as it is.
_NOT_A_VERB = frozenset(
    {"do", "don", "can", "cannot", "will", "won", "would", "should", "could", "had"}
)
_IRREGULAR = {"have": "has", "do": "does", "go": "goes", "am": "is"}
_APOSTROPHES = "'\u2019"  # a typed or a typographic apostrophe
_SIMPLE_VERB = re.compile(r"^I ([A-Za-z]+)(?= )")
_PREFERENCE = re.compile(
    r"\b(?:prefer\w*|favou?rite|like|likes|love\w*|enjoy\w*|dislike\w*|hate\w*)\b", re.I
)
_GOAL = re.compile(
    r"\b(?:goal|aim|want to|wants to|plan to|plans to|planning to|trying to|learning|studying)\b",
    re.I,
)
_CONTEXT = re.compile(
    r"\b(?:working on|currently|this (?:week|month|quarter|year)|my project)\b", re.I
)


def _s_form(verb: str) -> str:
    lower = verb.lower()
    if lower in _IRREGULAR:
        return _IRREGULAR[lower]
    if re.search(r"(?:s|sh|ch|x|z|o)$", lower):
        return lower + "es"
    if re.search(r"[^aeiou]y$", lower):
        return lower[:-1] + "ies"
    return lower + "s"


def third_person(text: str) -> str:
    """ "I prefer Go" -> "User prefers Go.": the form the extraction job writes, so memories read
    alike. Text that is not a simple first-person sentence is kept and marked as a note about the
    user. Deterministic, no model."""
    text = policy.normalise(text).rstrip(".!? ")
    if re.match(r"^user\b", text, re.I):
        out = text
    elif re.match(rf"^I[{_APOSTROPHES}]m\b", text):
        out = re.sub(rf"^I[{_APOSTROPHES}]m\b", "User is", text)
    elif (m := _SIMPLE_VERB.match(text)) and m.group(1).lower() not in _NOT_A_VERB:
        out = "User " + _s_form(m.group(1)) + text[m.end() :]
    elif re.match(r"^my\b", text, re.I):
        out = re.sub(r"^my\b", "User's", text, flags=re.I)
    else:
        out = f"User: {text}"
    out = re.sub(r"\b[Mm]y\b", "their", out)
    out = re.sub(r"\b[Mm]e\b", "them", out)
    return out + "."


def guess_category(content: str) -> str:
    """A category for a saved text, from words alone (the extraction job asks a model; this path
    must not). A wrong guess only changes how the memory is ranked, never what it says."""
    if _PREFERENCE.search(content):
        return "preference"
    if _GOAL.search(content):
        return "goal"
    if _CONTEXT.search(content):
        return "context"
    return "fact"


def choose_candidates(found: Sequence[Memory]) -> list[Memory]:
    """The memories a "forget ..." request may mean, best first.

    Only memories at least `FORGET_MIN_SIMILARITY` close count. One clear match (it leads the
    next one by `FORGET_CLEAR_MARGIN`, or it is the only one) is offered alone; otherwise up to
    `FORGET_MAX_CANDIDATES`, so the user picks."""
    close = sorted(
        (m for m in found if (m.score or 0.0) >= FORGET_MIN_SIMILARITY),
        key=lambda m: m.score or 0.0,
        reverse=True,
    )[:FORGET_MAX_CANDIDATES]
    if len(close) <= 1:
        return close
    lead = (close[0].score or 0.0) - (close[1].score or 0.0)
    # 0.86 - 0.76 is 0.0999... in floating point: the margin is meant to be inclusive.
    return close[:1] if lead >= FORGET_CLEAR_MARGIN - 1e-9 else close


def _line(text: str) -> str:
    return escape(policy.normalise(text), quote=False)


def render_list(memories: Sequence[Memory]) -> str:
    if not memories:
        return (
            "I haven't saved anything about you yet. Say `/memory remember <text>` to add "
            "something, or just tell me things like your preferred tools; I pick up lasting "
            "facts on my own."
        )
    newest = sorted(memories, key=lambda m: m.updated_at, reverse=True)[:LIST_LIMIT]
    noun = "thing" if len(memories) == 1 else "things"
    lines = "\n".join(f"- {_line(m.content)}" for m in newest)
    more = (
        f"\n\nShowing the {len(newest)} most recent of {len(memories)}."
        if len(memories) > len(newest)
        else ""
    )
    return f"I remember {len(memories)} {noun} about you:\n\n{lines}{more}\n\n{SETTINGS_HINT}"


def saved_reply(content: str) -> str:
    return f"Saved: {_line(content)}\n\n{SETTINGS_HINT}"


def duplicate_reply() -> str:
    return "I already have that saved."


def refusal_reply(reason: str) -> str:
    if reason.startswith("sensitive"):
        return _SENSITIVE
    return _REFUSALS.get(reason, _REFUSALS["empty"])


def forgot_reply(content: str) -> str:
    return f"Forgot: {_line(content)}"
