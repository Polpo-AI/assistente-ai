"""CRUD bozze e gestione stati draft_responses."""

import logging
from datetime import datetime, timezone
from typing import Optional
from .connection import get_client

logger = logging.getLogger("polpo.database")


def save_draft(
    email_id:          str,
    client_id:         str,
    subject:           str,
    body:              str,
    suggested_actions: list[str],
    final_intent:      str,
    reclassified:      bool = False,
    warning:           Optional[str] = None,
    status:            str = "pending",
    approved_by:       Optional[str] = None,
) -> dict:
    """Salva la bozza generata dal Responder."""
    db = get_client()
    data = {
        "email_id":          email_id,
        "client_id":         client_id,
        "subject":           subject,
        "body":              body,
        "suggested_actions": suggested_actions,
        "final_intent":      final_intent,
        "reclassified":      reclassified,
        "warning":           warning,
        "status":            status,
    }
    if approved_by:
        data["approved_by"] = approved_by
        data["approved_at"] = datetime.now(timezone.utc).isoformat()
    result = db.table("draft_responses").insert(data).execute()
    return result.data[0]


def approve_draft(draft_id: str, approved_by: str) -> dict:
    """Marca una bozza come approvata."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status":      "approved",
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", draft_id).execute()
    return result.data[0]


def ignore_draft(draft_id: str) -> dict:
    """Marca una bozza come ignorata."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status": "ignored",
    }).eq("id", draft_id).execute()
    return result.data[0]


def update_draft_status(draft_id: str, status: str) -> dict:
    """Aggiorna lo stato di una bozza."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status": status,
    }).eq("id", draft_id).execute()
    return result.data[0] if result.data else {}


def mark_email_no_reply(email_id: str, summary: str = "") -> dict:
    """Crea voce in draft_responses con stato 'no_reply_needed' per email Priority 0."""
    db = get_client()
    email_res = db.table("emails").select("client_id, subject").eq("id", email_id).execute()
    if not email_res.data:
        return {}
    email_data = email_res.data[0]
    existing = db.table("draft_responses").select("id").eq("email_id", email_id).execute()
    data = {
        "email_id":    email_id,
        "client_id":   email_data["client_id"],
        "subject":     email_data["subject"],
        "body":        "[Nessuna risposta necessaria - Sistema Polpo AI]",
        "status":      "sent",
        "final_intent": "cortesia",
        "warning":     summary or "Email automatica o di cortesia.",
    }
    if existing.data:
        result = db.table("draft_responses").update(data).eq("id", existing.data[0]["id"]).execute()
    else:
        result = db.table("draft_responses").insert(data).execute()
    return result.data[0] if result.data else {}


def mark_draft_generation_failed(email_id: str, error: str) -> None:
    try:
        get_client().table("emails").update({
            "draft_generation_status": "failed",
        }).eq("id", email_id).execute()
        logger.warning("mark_draft_generation_failed | email_id=%s errore: %s", email_id, error[:200])
    except Exception as e:
        logger.error("mark_draft_generation_failed | email_id=%s: %s", email_id, e)


def mark_draft_failed(draft_id: str, error: str) -> None:
    """Marca una bozza come fallita (errore SMTP)."""
    try:
        get_client().table("draft_responses").update({
            "status":     "send_failed",
            "send_error": error[:500],
        }).eq("id", draft_id).execute()
    except Exception as e:
        logger.error("mark_draft_failed | draft_id=%s: %s", draft_id, e)


def mark_draft_sent(draft_id: str, sent_message_id: str = "") -> dict:
    """Marca una bozza come inviata, salva il Message-ID SMTP generato."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status":          "sent",
        "sent_at":         datetime.now(timezone.utc).isoformat(),
        "sent_message_id": sent_message_id,
    }).eq("id", draft_id).execute()
    return result.data[0]


def save_telegram_message_id(draft_id: str, message_id: int) -> None:
    """Salva l'ID del messaggio Telegram per editarlo in seguito."""
    db = get_client()
    db.table("draft_responses").update({
        "telegram_message_id": message_id,
    }).eq("id", draft_id).execute()


def get_draft_by_id(draft_id: str) -> Optional[dict]:
    """Recupera una bozza con credenziali SMTP (usata solo per l'invio)."""
    db = get_client()
    result = (
        db.table("draft_responses")
        .select("*, emails(sender_email, sender_name, subject, body, message_id), "
                "clients(telegram_chat_id, llm_tone, signature, name, "
                "smtp_host, smtp_port, smtp_user, smtp_password)")
        .eq("id", draft_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_draft_for_display(draft_id: str) -> Optional[dict]:
    """Recupera una bozza per visualizzazione — senza credenziali SMTP."""
    db = get_client()
    result = (
        db.table("draft_responses")
        .select("*, emails(sender_email, sender_name, subject, body, message_id), "
                "clients(telegram_chat_id, llm_tone, signature, name)")
        .eq("id", draft_id)
        .execute()
    )
    return result.data[0] if result.data else None


def update_draft_body(draft_id: str, subject: str, body: str) -> dict:
    """Aggiorna soggetto e corpo di una bozza dopo modifica via Telegram."""
    db = get_client()
    result = db.table("draft_responses").update({
        "subject": subject,
        "body":    body,
    }).eq("id", draft_id).execute()
    return result.data[0]


def get_approved_drafts() -> list[dict]:
    """Cerca bozze approvate che devono essere inviate."""
    db = get_client()
    result = (
        db.table("draft_responses")
        .select("id, client_id, subject, body, email_id, telegram_message_id, "
                "emails(sender_email, sender_name, subject, message_id), "
                "clients(smtp_host, smtp_port, smtp_user, smtp_password, name, telegram_chat_id)")
        .eq("status", "approved")
        .execute()
    )
    return result.data
