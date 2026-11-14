"""Human-in-the-loop tools and the mapping between their interrupts and the run API.

Three tools pause the run for the family (ARCHITECTURE.md sections 2 and 6, CONTRACTS.md
Decision 9):

- `ask_teller` (interviewer): a follow-up question. Answered with AnswerRequest(kind="answer").
- `merge_people`: "is Uncle Joe the Joseph in the tree?". Answered with kind="merge".
- `confirm_props` (prop_master, defined in agent/tools/props.py): the prop review.
  Answered with kind="props".

The studio passes `INTERRUPT_ON` to `create_deep_agent(interrupt_on=...)`, which installs
langchain's `HumanInTheLoopMiddleware`. How that middleware behaves in the installed
langchain (1.5):

- deepagents builds it with the default `interrupt_mode="batched"`: after a model turn
  that calls any gated tool, the graph pauses with ONE interrupt whose value is an
  `HITLRequest` `{"action_requests": [{name, args, description}], "review_configs": [...]}`
  listing every gated call of that turn. It resumes with
  `Command(resume={"decisions": [...]})`, one decision per action request, by position.
- `"per_call"` mode (not used by deepagents today, but handled here) pauses once per gated
  call with a `ToolApprovalRequest` `{"type": "tool_approval", tool_call_id, name, args,
  description}` and resumes with a single bare decision (keyed by interrupt id when
  several are pending).
- `True` in `interrupt_on` allows all four decisions; we narrow them per tool instead:
  `respond` (the message becomes the ToolMessage and the tool body never runs), `reject`
  (the model is told the call was refused), `edit` (the tool runs with the reviewer's args),
  `approve` (the tool runs as called).

`interrupt_to_event` turns one interrupt value into the SSE event the web shows, and
`answer_to_resume` turns the family's AnswerRequest into the resume payload for that same
interrupt, so agent/runs.py only has to call these two.

The middleware pauses on the model's raw tool arguments, before the tool's args schema
is checked, so a pause can carry arguments the web cannot show (an empty question, a
prop chip with a size outside the vocabulary). `interrupt_to_event` raises ValueError
for those, with a short message written for the model; `reject_to_resume` builds the
payload that refuses the whole pause with that message, so runs.py can hand the call
back to the model to fix instead of ending the run.

If one model turn gates several calls (say two `ask_teller` calls in parallel), the FIRST
action request is the one shown to the family; the others are rejected with a note asking
the model to ask again, one thing at a time.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import InterruptOnConfig
from langchain_core.tools import tool
from pydantic import ValidationError

import schemas
from screenplay.schema import VOCABULARY, Model

NO_ANSWER = "no answer given"

ASK_TELLER = "ask_teller"
MERGE_PEOPLE = "merge_people"
CONFIRM_PROPS = "confirm_props"

INTERRUPT_ON: dict[str, InterruptOnConfig] = {
    ASK_TELLER: InterruptOnConfig(
        allowed_decisions=["respond", "reject"],
        description="The studio has a follow-up question for the teller.",
    ),
    MERGE_PEOPLE: InterruptOnConfig(
        allowed_decisions=["respond", "reject"],
        description="The studio wants to know whether two names are the same person.",
    ),
    CONFIRM_PROPS: InterruptOnConfig(
        allowed_decisions=["edit", "approve", "reject"],
        description="The studio found these props. Keep, rename, remove or replace them.",
    ),
}
"""Pass to create_deep_agent(interrupt_on=INTERRUPT_ON)."""

_DEFERRED = (
    "Not answered yet: the family can only answer one thing at a time. "
    "Ask this again, by itself, after reading the answer above."
)


@tool
def ask_teller(question: str) -> str:
    """Pause and ask the family member who told this story one short follow-up question.

    Use it only when the story cannot be staged without the answer: the setting, who was
    there, or what happened is missing. Ask in plain, warm words, one thing at a time.
    The tool result is the teller's answer.
    """
    # Runs only if a reviewer approves without answering; the real answer arrives as a
    # `respond` decision that replaces this result.
    return NO_ANSWER


@tool
def merge_people(name: str, person_id: str, candidate_name: str, question: str) -> str:
    """Ask the family whether a name in the story is a person already in the family tree.

    Args:
        name: The name as the story says it, e.g. "Uncle Joe".
        person_id: The id of the existing person in the tree who might be the same person.
        candidate_name: That existing person's name in the tree, e.g. "Joseph".
        question: The question to show, e.g. "Is Uncle Joe the Joseph in your tree?"

    The result starts with "approved:" (use person_id for this person) or "rejected:"
    (treat them as someone new).
    """
    return NO_ANSWER


# ------------------------------------------------------------------------- interrupts


def _unwrap(value: Any) -> Any:
    """Accept a langgraph `Interrupt` as well as its bare value."""
    if not isinstance(value, dict) and hasattr(value, "value") and hasattr(value, "id"):
        return value.value
    return value


def _pending(value: Any) -> tuple[str, dict[str, Any], list[dict[str, Any]], bool]:
    """Return (tool name, args, every action request, is_batched) for one interrupt value."""
    value = _unwrap(value)
    if not isinstance(value, dict):
        raise ValueError(f"not a human-in-the-loop interrupt: {type(value).__name__}")
    if value.get("type") == "tool_approval":  # per_call mode
        name, args = value.get("name"), value.get("args")
        actions = [{"name": name, "args": args}]
        batched = False
    elif "action_requests" in value:  # batched mode (HITLRequest)
        actions = value["action_requests"]
        if not isinstance(actions, list) or not actions:
            raise ValueError("interrupt has no action requests")
        first = actions[0]
        if not isinstance(first, dict):
            raise ValueError("malformed action request")
        name, args = first.get("name"), first.get("args")
        batched = True
    else:
        raise ValueError("not a human-in-the-loop interrupt: no action_requests or tool_approval")
    if not isinstance(name, str) or not isinstance(args, dict):
        raise ValueError("malformed action request: needs a tool name and an args object")
    return name, args, actions, batched


def _require_str(args: dict[str, Any], key: str, tool_name: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{tool_name} args need a non-empty string {key!r}")
    return value


_PROP_SIZES = ", ".join(VOCABULARY["sizes"])


def _chip_problem(error: ValidationError) -> str:
    """What is wrong with one prop chip, in a few words (never pydantic's own dump)."""
    problems: list[str] = []
    for item in error.errors():
        loc = item.get("loc") or ()
        field = str(loc[0]) if loc else "chip"
        kind = item.get("type", "")
        if field == "size" and kind in ("literal_error", "enum"):
            problem = f"'size' {item.get('input')!r} is not a prop size; use one of: {_PROP_SIZES}"
        elif kind == "missing":
            problem = f"{field!r} is missing"
        elif kind == "extra_forbidden":
            problem = f"{field!r} is not a prop chip field"
        else:
            problem = f"{field!r} has the wrong type"
        if problem not in problems:
            problems.append(problem)
    return "; ".join(problems) or "it is not a prop chip"


def _prop_chips(raw: list[Any]) -> list[schemas.PropChip]:
    chips: list[schemas.PropChip] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"confirm_props props[{index}] is not an object")
        try:
            chips.append(schemas.PropChip.model_validate(item))
        except ValidationError as e:
            chip_id = item.get("id")
            which = f"prop {chip_id!r}" if isinstance(chip_id, str) and chip_id else f"props[{index}]"
            raise ValueError(f"confirm_props {which}: {_chip_problem(e)}") from None
    return chips


def interrupt_to_event(value: Any) -> tuple[str, Model]:
    """Map one HITL interrupt (value or `Interrupt`) to a run event (name, payload model).

    ask_teller -> ("question", QuestionEvent); merge_people -> ("merge", MergeEvent);
    confirm_props -> ("props_found", PropsFoundEvent). Raises ValueError for any other
    tool or malformed args, with a short message the model can act on (for a malformed
    prop chip: the chip id, the bad field and, for a size, the allowed sizes).
    """
    name, args, _, _ = _pending(value)
    if name == ASK_TELLER:
        return "question", schemas.QuestionEvent(text=_require_str(args, "question", name))
    if name == MERGE_PEOPLE:
        return "merge", schemas.MergeEvent(
            text=_require_str(args, "question", name),
            person_id=_require_str(args, "person_id", name),
            candidate_name=_require_str(args, "candidate_name", name),
        )
    if name == CONFIRM_PROPS:
        raw = args.get("props")
        if not isinstance(raw, list):
            raise ValueError("confirm_props args need a 'props' list")
        return "props_found", schemas.PropsFoundEvent(props=_prop_chips(raw))
    raise ValueError(f"no run event for tool {name!r}")


def _decision_for(name: str, args: dict[str, Any], answer: schemas.AnswerRequest) -> dict[str, Any]:
    expected = {"answer": ASK_TELLER, "merge": MERGE_PEOPLE, "props": CONFIRM_PROPS}[answer.kind]
    if name != expected:
        raise ValueError(f"answer kind {answer.kind!r} does not match the pending {name!r}")

    if answer.kind == "answer":
        text = (answer.text or "").strip()
        if not text:
            raise ValueError("an answer needs text")
        return {"type": "respond", "message": text}

    if answer.kind == "merge":
        if answer.approve is None:
            raise ValueError("a merge answer needs approve: true or false")
        person_id = _require_str(args, "person_id", name)
        candidate = _require_str(args, "candidate_name", name)
        said = args.get("name")
        if not isinstance(said, str) or not said.strip():
            said = "this person"
        if answer.approve:
            message = f"approved: {person_id} is {candidate}; {said!r} in this story is that person"
        else:
            message = f"rejected: {said} is a different person from {candidate} ({person_id})"
        return {"type": "respond", "message": message}

    edits = [e.model_dump(mode="json", by_alias=True) for e in (answer.props or [])]
    return {"type": "edit", "edited_action": {"name": CONFIRM_PROPS, "args": {**args, "edits": edits}}}


def answer_to_resume(value: Any, answer: schemas.AnswerRequest) -> dict[str, Any]:
    """Build the resume payload for the pending interrupt from the family's answer.

    Batched (HITLRequest, what deepagents uses): returns {"decisions": [...]}, to send as
    Command(resume=...). The first action gets the family's decision, any others are
    rejected with a note to ask again. Per-call (ToolApprovalRequest): returns the single
    decision dict.

    kind="answer" -> respond with the text (ask_teller pending, text required);
    kind="merge" -> respond "approved: <person_id> is <candidate_name>; ..." or
    "rejected: <name> is a different person from ..." (merge_people pending);
    kind="props" -> edit confirm_props with args {**args, "edits": [PropEdit JSON...]}
    (confirm_props pending; an empty list keeps every prop).
    A kind that does not match the pending tool raises ValueError (the route sends 409).
    """
    name, args, actions, batched = _pending(value)
    decision = _decision_for(name, args, answer)
    if not batched:
        return decision
    rest = [{"type": "reject", "message": _DEFERRED} for _ in actions[1:]]
    return {"decisions": [decision, *rest]}


def reject_to_resume(value: Any, message: str) -> Any:
    """Build the resume payload that rejects every action of the pending interrupt.

    `message` is the rejection reason the model reads (the middleware sends it back as
    the tool's result, "User rejected the tool call for `<tool>` with reason: <message>").
    Batched (HITLRequest): {"decisions": [{"type": "reject", "message": message}, ...]},
    one per action request. Per-call (ToolApprovalRequest): the single reject decision.

    Unlike `answer_to_resume` it does not read the actions' args, so it works on exactly
    the pauses `interrupt_to_event` refuses. Raises ValueError only when `value` is not a
    human-in-the-loop interrupt at all. Every gated tool allows "reject" (INTERRUPT_ON).
    """
    value = _unwrap(value)
    decision = {"type": "reject", "message": message}
    if isinstance(value, dict):
        if value.get("type") == "tool_approval":
            return decision
        actions = value.get("action_requests")
        if isinstance(actions, list) and actions:
            return {"decisions": [dict(decision) for _ in actions]}
    raise ValueError("not a human-in-the-loop interrupt: nothing to reject")


__all__ = [
    "ASK_TELLER",
    "CONFIRM_PROPS",
    "INTERRUPT_ON",
    "MERGE_PEOPLE",
    "NO_ANSWER",
    "answer_to_resume",
    "ask_teller",
    "interrupt_to_event",
    "merge_people",
    "reject_to_resume",
]
