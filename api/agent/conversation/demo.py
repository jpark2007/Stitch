"""Explicit offline conversation script; it is not a live model substitute."""

class ScriptedConversation:
    def __init__(self, service):
        self.service = service

    async def ainvoke(self, text: str, *, timeout: float = 20):
        return "What else do you remember about that day?"
