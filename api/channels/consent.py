"""Deterministic opt-out commands shared by text and photo captions."""

import unicodedata

from db import contacts, conversations

COMMANDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
POLITE_SUFFIXES = {(), ("please",), ("thanks",), ("thank", "you")}


def is_stop_command(text: str) -> bool:
    """Recognize a whole command, not an incidental word inside a story.

    Compatibility letters and invisible format characters normalize; punctuation
    and emoji separate tokens. Unicode variation selectors are ignored; other
    combining marks remain meaningful. Quoted standalone commands also count. Only the
    documented polite suffixes are allowed, so ``I told him to stop`` stays text.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = "".join(
        "" if (unicodedata.category(char) == "Cf"
               or "\ufe00" <= char <= "\ufe0f"
               or "\U000e0100" <= char <= "\U000e01ef") else
        " " if unicodedata.category(char)[0] in {"P", "S"} else char
        for char in normalized
    )
    tokens = normalized.split()
    return bool(tokens and tokens[0] in COMMANDS and tuple(tokens[1:]) in POLITE_SUFFIXES)


def receive_stop(db, message: dict, text: str) -> dict | None:
    """Revoke every matching mapping before any best-effort receipt or media work."""
    if not is_stop_command(text):
        return None
    rows = contacts.matching_contacts(db, message["provider"], message["senderId"])
    if not rows:
        raise LookupError("Unknown contact")
    contacts.stop_contacts(db, message["provider"], message["senderId"], rows)
    # Photo receipts intentionally retain no attachment bytes or media metadata.
    receipt = {key: message[key] for key in ("messageId", "provider", "spaceId", "senderId")}
    receipt["text"] = text
    duplicates = []
    for row in rows:
        try:
            duplicates.append(conversations.save_inbound(db, row, receipt, stopped=True))
        except Exception:
            # Receipt collisions/storage failures cannot undo consent revocation.
            duplicates.append(False)
    return {"accepted": True, "duplicate": all(duplicates), "stopped": True}
