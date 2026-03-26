"""Gestione conversazioni e thread email."""

import re
import logging
from datetime import datetime, timezone
from typing import Optional
from .connection import get_client

logger = logging.getLogger("polpo.database")


def _normalize_subject(subject: str) -> str:
    """Rimuove Re:/Fwd: per raggruppare thread."""
    return re.sub(r"^(re|fwd|r|fw|inoltro):\s*", "", subject.strip(), flags=re.IGNORECASE).strip()


def find_or_create_conversation(
    client_id:   str,
    contact_id:  str,
    subject:     str,
    in_reply_to: str = "",
) -> dict:
    """
    Trova conversazione esistente o ne crea una nuova.

    Strategia (in ordine di priorità):
    1. RFC822 in_reply_to → cerca l'email con quel message_id e usa la sua conversation_id.
    2. Nessun in_reply_to → nuova email indipendente → nuova conversazione.
    """
    db = get_client()

    if in_reply_to:
        parent = (
            db.table("emails")
            .select("conversation_id")
            .eq("client_id", client_id)
            .eq("message_id", in_reply_to)
            .limit(1)
            .execute()
        )
        if parent.data and parent.data[0].get("conversation_id"):
            conv_id = parent.data[0]["conversation_id"]
            conv = db.table("conversations").select("*").eq("id", conv_id).execute()
            if conv.data:
                c = conv.data[0]
                db.table("conversations").update({
                    "email_count":   c["email_count"] + 1,
                    "last_email_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", conv_id).execute()
                c["email_count"] += 1
                return c

    thread = _normalize_subject(subject)
    result = db.table("conversations").insert({
        "client_id":      client_id,
        "contact_id":     contact_id,
        "subject_thread": thread,
    }).execute()
    return result.data[0]


def get_conversation_history(conversation_id: str, limit: int = 6) -> list[dict]:
    """
    Ultime N email di una conversazione (inbound + outbound), ordinate cronologicamente.
    Include le email inviate dal bot per dare a Sonnet il contesto completo.
    """
    db = get_client()
    result = (
        db.table("emails")
        .select("id, received_at, subject, body, direction, email_classifications(intent, priority, summary)")
        .eq("conversation_id", conversation_id)
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(result.data))
