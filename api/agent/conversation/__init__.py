"""Contact-bound conversation core; imports never configure a model or start work."""

from .service import ContactContext, ConversationService

__all__ = ["ContactContext", "ConversationService"]
