"""CRUD email, classificazioni e pipeline persist_classified_email."""

import logging
from datetime import datetime, timezone
from typing import Optional
from .connection import get_client
from .contacts import upsert_contact, get_contact_by_email
from .conversations import find_or_create_conversation

logger = logging.getLogger("polpo.database")


def save_email(
    client_id:       str,
    sender_email:    str,
    sender_name:     str,
    subject:         str,
    body:            str,
    attachments:     list[str],
    contact_id:      Optional[str] = None,
    conversation_id: Optional[str] = None,
    message_id:      Optional[str] = None,
) -> dict:
    db = get_client()
    data = {
        "client_id":       client_id,
        "sender_email":    sender_email.lower(),
        "sender_name":     sender_name,
        "subject":         subject,
        "body":            body,
        "attachments":     attachments,
        "contact_id":      contact_id,
        "conversation_id": conversation_id,
        "received_at":     datetime.now(timezone.utc).isoformat(),
    }
    if message_id:
        data["message_id"] = message_id
    result = db.table("emails").insert(data).execute()
    return result.data[0]


def get_email_by_id(email_id: str) -> Optional[dict]:
    """Recupera un'email per ID."""
    db = get_client()
    result = db.table("emails").select("*").eq("id", email_id).execute()
    return result.data[0] if result.data else None


def save_classification(
    email_id:        str,
    contact_type:    str,
    intent:          str,
    priority:        int,
    confidence:      float,
    classified_by:   str,
    summary:         str,
    estimated_value: Optional[float] = None,
) -> dict:
    db = get_client()
    result = db.table("email_classifications").insert({
        "email_id":        email_id,
        "contact_type":    contact_type,
        "intent":          intent,
        "priority":        priority,
        "confidence":      confidence,
        "classified_by":   classified_by,
        "summary":         summary,
        "estimated_value": estimated_value,
    }).execute()
    return result.data[0]


def persist_classified_email(
    client_id:       str,
    sender_email:    str,
    sender_name:     str,
    subject:         str,
    body:            str,
    attachments:     list[str],
    contact_type:    str,
    intent:          str,
    priority:        int,
    confidence:      float,
    classified_by:   str,
    summary:         str,
    estimated_value: Optional[float] = None,
    in_reply_to:     str = "",
    message_id:      Optional[str] = None,
) -> dict:
    """
    Pipeline salvataggio completa:
    1. Dedup per message_id (se fornito)
    2. Upsert contatto
    3. Trova o crea conversazione (via RFC822 in_reply_to se disponibile)
    4. Salva email
    5. Salva classificazione
    """
    db = get_client()

    # Dedup: se message_id già presente, ritorna i db_ids esistenti senza duplicare
    if message_id:
        existing = db.table("emails").select("id, contact_id, conversation_id").eq("message_id", message_id).execute()
        if existing.data:
            row = existing.data[0]
            logger.info("persist_classified_email | dedup message_id=%s — email già presente: %s", message_id[:30], row["id"][:8])
            existing_cls = db.table("email_classifications").select("id").eq("email_id", row["id"]).execute()
            return {
                "client_id":         client_id,
                "contact_id":        row.get("contact_id"),
                "conversation_id":   row.get("conversation_id"),
                "email_id":          row["id"],
                "classification_id": existing_cls.data[0]["id"] if existing_cls.data else None,
            }

    contact = upsert_contact(client_id, sender_email, sender_name, contact_type)
    contact_id = contact["id"]

    conversation = find_or_create_conversation(client_id, contact_id, subject, in_reply_to)
    conversation_id = conversation["id"]

    email_record = save_email(
        client_id=client_id,
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body=body,
        attachments=attachments,
        contact_id=contact_id,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    email_id = email_record["id"]

    classification_record = save_classification(
        email_id=email_id,
        contact_type=contact_type,
        intent=intent,
        priority=priority,
        confidence=confidence,
        classified_by=classified_by,
        summary=summary,
        estimated_value=estimated_value,
    )

    return {
        "client_id":         client_id,
        "contact_id":        contact_id,
        "conversation_id":   conversation_id,
        "email_id":          email_id,
        "classification_id": classification_record["id"],
    }


def save_outbound_email(
    client_id:       str,
    draft_id:        str,
    sent_message_id: str,
    in_reply_to:     str,
    references_ids:  list,
    sender_email:    str,
    sender_name:     str,
    recipient_email: str,
    subject:         str,
    body:            str,
) -> None:
    """Crea un record per ogni email inviata (direction='outbound') per ricostruzione thread."""
    try:
        get_client().table("emails").insert({
            "client_id":      client_id,
            "direction":      "outbound",
            "message_id":     sent_message_id,
            "in_reply_to":    in_reply_to,
            "references_ids": references_ids,
            "sender_email":   sender_email,
            "sender_name":    sender_name,
            "subject":        subject,
            "body":           body,
            "intent":         "outbound",
            "priority":       0,
            "classified_by":  "outbound",
            "contact_type":   "bot",
            "confidence":     1.0,
            "summary":        f"Email inviata in risposta a {recipient_email}",
        }).execute()
        logger.info("save_outbound_email | Salvata email outbound msg_id=%s", sent_message_id)
    except Exception as e:
        logger.error("save_outbound_email | Errore: %s", e)


def get_pending_emails(client_id: str, priority: Optional[int] = None, limit: int = 20) -> list[dict]:
    """Email classificate in attesa di gestione per un cliente specifico."""
    db = get_client()
    query = (
        db.table("v_emails_classified")
        .select("*")
        .eq("client_id", client_id)
        .eq("draft_status", "pending")
    )
    if priority:
        query = query.eq("priority", priority)
    result = query.order("received_at", desc=True).limit(limit).execute()
    return result.data


def get_contact_history(client_id: str, email: str, limit: int = 5) -> list[dict]:
    """Ultime N email di un contatto — contesto per il Responder."""
    db = get_client()
    contact = get_contact_by_email(client_id, email)
    if not contact:
        return []
    result = (
        db.table("emails")
        .select("id, received_at, subject, body, email_classifications(intent, priority, summary)")
        .eq("client_id", client_id)
        .eq("contact_id", contact["id"])
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data
