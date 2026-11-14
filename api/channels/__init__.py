"""Optional Channels integration. Importing this package starts no services."""


def ensure_channel_indexes(db):
    """Startup hook for the API owner; call only when Channels is enabled."""
    from db.contacts import ensure_contact_indexes
    from db.conversations import ensure_message_indexes
    from db.question_bank import ensure_question_indexes

    ensure_contact_indexes(db)
    ensure_message_indexes(db)
    ensure_question_indexes(db)
