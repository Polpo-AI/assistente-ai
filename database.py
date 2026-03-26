"""
database.py — Facade di re-export per backward compatibility.

La logica è ora nel package db/:
  db/connection.py     — Supabase client singleton
  db/contacts.py       — CRUD contatti
  db/conversations.py  — Thread e conversazioni
  db/emails.py         — CRUD email e classificazioni
  db/drafts.py         — CRUD bozze e stati
  db/chat.py           — Chat history e pending edits
  db/queries.py        — Query analytics per l'assistente
"""

from db.connection import get_client  # noqa: F401

from db.contacts import (  # noqa: F401
    get_client_id_by_telegram_chat_id,
    upsert_contact,
    get_contact_by_email,
    get_existing_contact_types,
)

from db.conversations import (  # noqa: F401
    find_or_create_conversation,
    get_conversation_history,
)

from db.emails import (  # noqa: F401
    save_email,
    get_email_by_id,
    save_classification,
    persist_classified_email,
    save_outbound_email,
    get_pending_emails,
    get_contact_history,
)

from db.drafts import (  # noqa: F401
    save_draft,
    approve_draft,
    ignore_draft,
    update_draft_status,
    mark_email_no_reply,
    mark_draft_generation_failed,
    mark_draft_failed,
    mark_draft_sent,
    save_telegram_message_id,
    get_draft_by_id,
    get_draft_for_display,
    update_draft_body,
    get_approved_drafts,
)

from db.chat import (  # noqa: F401
    set_pending_edit,
    get_pending_edit,
    clear_pending_edit,
    save_chat_message,
    get_chat_history,
)

from db.queries import (  # noqa: F401
    q_contact_emails,
    q_emails_in_range,
    q_top_senders,
    q_unanswered_emails,
    q_drafts_by_status,
    q_drafts_sent_in_range,
    q_pending_older_than,
    q_intent_stats,
    q_daily_volume,
    add_sender_to_blacklist,
)
