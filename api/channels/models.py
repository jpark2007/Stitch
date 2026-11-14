"""The version-one text transport boundary; no family identity comes from input."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

MAX_BODY_BYTES = 16384
WHITESPACE = "\u0009\u000a\u000b\u000c\u000d\u001c\u001d\u001e\u001f\u0020\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def bounded_text(value: str, limit: int) -> str:
    if not value.strip(WHITESPACE):
        raise ValueError("must not be blank")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("must contain valid Unicode scalar values") from None
    if size > limit:
        raise ValueError("UTF-8 byte limit exceeded")
    return value


class InboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    messageId: str
    provider: Literal["terminal", "imessage"]
    spaceId: str
    senderId: str
    text: str

    @field_validator("messageId", "spaceId", "senderId")
    @classmethod
    def identifier(cls, value: str) -> str:
        return bounded_text(value, 256)

    @field_validator("text")
    @classmethod
    def message_text(cls, value: str) -> str:
        return bounded_text(value, 8000)


class Tick(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
