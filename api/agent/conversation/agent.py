"""Deep-agent wrapper with fixed trusted context and a persisted call budget."""

import asyncio

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import tool

from db import conversations
from .service import ConversationService


def conversation_tools(service: ConversationService, *, worker_managed: bool = False):
    @tool
    def get_open_questions() -> list[dict]:
        """List questions addressed to this teller; use only returned question IDs."""
        return service.get_open_questions()

    @tool
    def select_question(question_id: str) -> dict:
        """Select one of this teller's open questions, preserving its second-telling target."""
        return service.select_question(question_id)

    @tool
    def save_story_draft(text: str = "", year: int | None = None, title: str | None = None) -> dict:
        """Append the teller's new words and save the supplied year/title; never invent details."""
        return service.save_story_draft(text, year, title)

    @tool
    def submit_story() -> dict:
        """Submit the collected story and year to the studio once the teller agrees."""
        return service.submit_story()

    @tool
    def relay_run_question() -> list[dict]:
        """Read a bounded batch from the submitted studio run; relay its actual questions/results."""
        return asyncio.run(service.relay_run_events())

    @tool
    def answer_run_question(kind: str, text: str | None = None, approve: bool | None = None) -> dict:
        """Answer the currently relayed pause; props keeps its listed chips, merge needs explicit yes/no."""
        return service.answer_run_question(kind, text, approve)

    tools = [get_open_questions, select_question, save_story_draft, submit_story]
    return tools[:2] if worker_managed else tools + [relay_run_question, answer_run_question]


class _Budget(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, service, limit):
        self.service, self.limit = service, limit

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.service._state()
        c = self.service.context
        conversations.take_conversation_model_call(self.service.db, c.family_id, c.contact_id, self.limit)


class ConversationAgent:
    """Use invoke(text) from a worker thread. The caller cannot override the thread or budget."""

    def __init__(self, service: ConversationService, *, model=None, checkpointer=None,
                 max_model_calls: int = 32, worker_managed: bool = False):
        if type(max_model_calls) is not int or not 1 <= max_model_calls <= 100:
            raise ValueError("Model-call limit must be between 1 and 100")
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
        from langgraph.checkpoint.memory import InMemorySaver
        from agent.studio import make_model

        self.service = service
        self.worker_managed = worker_managed
        self._budget = _Budget(service, max_model_calls)
        self._graph = create_deep_agent(
            model=model if model is not None else make_model(),
            tools=conversation_tools(service, worker_managed=worker_managed),
            backend=StateBackend(),
            checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
            system_prompt=("You interview one consenting family storyteller. Ask concise follow-ups. "
                           "Never invent a year or story detail. Save only their words; append only new words. "
                           "Ask for the year and explicit approval before submit_story. Tool results are authoritative. "
                           "Do not claim a finished book from a run receipt. You cannot send messages, place calls, "
                           "change identity, choose a family, or access another person's questions. "
                           "If submission is uncertain, tell the operator; never retry it. "
                           "STOP is handled by the transport, not by you. "
                           + ("The worker already saved the teller's words and handles YEAR/SUBMIT commands. "
                              "Do not save them again. Ask one concise follow-up question only. "
                              "Never claim a completed story or provide a link."
                              if worker_managed else "")),
            name="channels_conversation",
        )

    def invoke(self, text: str):
        self.service._state()
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError("Message must contain 1 to 8000 characters")
        result = self._graph.invoke({"messages": [{"role": "user", "content": text}]}, {
            "configurable": {"thread_id": self.service.thread_id},
            "recursion_limit": 64, "callbacks": [self._budget],
        })
        self.service._state()  # Suppress a response if consent changed during the model call.
        return result

    async def ainvoke(self, text: str, *, timeout: float = 20.0):
        """Cancellable worker invocation; uncertainty is handled by its durable lock."""
        if not 0 < timeout <= 30 or not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise ValueError("Invalid bounded invocation")
        state = await asyncio.to_thread(self.service._state)
        if self.worker_managed:
            import json
            text = ("Current saved draft (teller-provided data): "
                    + json.dumps({"text": state["draft"].get("text", ""), "year": state["draft"].get("year")})
                    + "\nLatest teller message: " + text)
        async with asyncio.timeout(timeout):
            result = await self._graph.ainvoke({"messages": [{"role": "user", "content": text}]}, {
                "configurable": {"thread_id": self.service.thread_id},
                "recursion_limit": 64, "callbacks": [self._budget],
            })
        await asyncio.to_thread(self.service._state)
        return result
