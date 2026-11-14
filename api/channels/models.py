"""The version-one text transport boundary; no family identity comes from input."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from db.media import MAX_BYTES

MAX_BODY_BYTES = 16384
MAX_PHOTO_BASE64 = 4 * ((MAX_BYTES + 2) // 3)
MAX_PHOTO_BODY_BYTES = MAX_PHOTO_BASE64 + MAX_BODY_BYTES
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


class AuthorizeContact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: Literal["terminal", "imessage"]
    senderId: str

    @field_validator("senderId")
    @classmethod
    def identifier(cls, value: str) -> str:
        return bounded_text(value, 256)


class InboundPhoto(AuthorizeContact):
    messageId: str
    spaceId: str
    attachmentId: str
    mimeType: str
    dataBase64: str
    caption: str = ""

    @field_validator("messageId", "spaceId", "attachmentId")
    @classmethod
    def photo_identifier(cls, value: str) -> str:
        return bounded_text(value, 256)

    @field_validator("mimeType")
    @classmethod
    def mime(cls, value: str) -> str:
        return bounded_text(value, 100)

    @field_validator("dataBase64")
    @classmethod
    def encoded_size(cls, value: str) -> str:
        if not value or len(value) > MAX_PHOTO_BASE64 or not value.isascii():
            raise ValueError("Invalid encoded photo length")
        return value


    @field_validator("caption")
    @classmethod
    def caption_size(cls, value: str) -> str:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Invalid caption Unicode") from None
        if size > 8000:
            raise ValueError("Caption is too long")
        return value


class InboundVoice(AuthorizeContact):
    messageId: str
    spaceId: str
    attachmentId: str
    mimeType: str
    dataBase64: str

    @field_validator("messageId", "spaceId", "attachmentId")
    @classmethod
    def voice_identifier(cls, value: str) -> str:
        return bounded_text(value, 256)

    @field_validator("mimeType")
    @classmethod
    def voice_mime(cls, value: str) -> str:
        return bounded_text(value, 100)

    @field_validator("dataBase64")
    @classmethod
    def voice_size(cls, value: str) -> str:
        if not value or len(value) > MAX_PHOTO_BASE64 or not value.isascii():
            raise ValueError("Invalid encoded voice length")
        return value
