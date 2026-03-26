"""Query analytics e ricerche per l'assistente conversazionale Telegram."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from .connection import get_client
from .contacts import get_contact_by_email
from constants import DraftStatus

logger = logging.getLogger("polpo.database")


def q_contact_emails(client_id: str, contact_email: str, limit: int = 10) -> list[dict]:
    db = get_client()
    res = (
        db.table("emails")
        .select("id, received_at, sender_name, sender_email, subject, body")
        .eq("client_id", client_id)
        .ilike("sender_email", f"%{contact_email}%")
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_emails_in_range(client_id: str, date_from: str, date_to: str, limit: int = 50) -> list[dict]:
    db = get_client()
    res = (
        db.table("emails")
        .select("id, received_at, sender_name, sender_email, subject")
        .eq("client_id", client_id)
        .gte("received_at", f"{date_from}T00:00:00Z")
        .lte("received_at", f"{date_to}T23:59:59Z")
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_top_senders(client_id: str, date_from: str, date_to: str, limit: int = 5) -> list[dict]:
    db = get_client()
    res = db.rpc("rpc_top_senders", {
        "p_client_id": client_id,
        "p_date_from": f"{date_from}T00:00:00Z",
        "p_date_to":   f"{date_to}T23:59:59Z",
        "p_limit":     limit,
    }).execute()
    return res.data


def q_unanswered_emails(client_id: str, limit: int = 20) -> list[dict]:
    db = get_client()
    res = (
        db.table("v_unanswered_emails")
        .select("*")
        .eq("client_id", client_id)
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_drafts_by_status(client_id: str, status: str, limit: int = 20) -> list[dict]:
    db = get_client()
    res = (
        db.table("draft_responses")
        .select("id, created_at, subject, body, final_intent, status")
        .eq("client_id", client_id)
        .eq("status", status)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_drafts_sent_in_range(client_id: str, date_from: str, date_to: str, limit: int = 50) -> list[dict]:
    db = get_client()
    res = (
        db.table("draft_responses")
        .select("id, sent_at, subject, body, final_intent, status")
        .eq("client_id", client_id)
        .eq("status", DraftStatus.SENT)
        .gte("sent_at", f"{date_from}T00:00:00Z")
        .lte("sent_at", f"{date_to}T23:59:59Z")
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_pending_older_than(client_id: str, hours: int, limit: int = 20) -> list[dict]:
    db = get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = (
        db.table("draft_responses")
        .select("*")
        .eq("client_id", client_id)
        .eq("status", DraftStatus.PENDING)
        .lte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return res.data


def q_intent_stats(client_id: str, date_from: str, date_to: str) -> list[dict]:
    db = get_client()
    res = (
        db.table("email_classifications")
        .select("intent, emails!inner(client_id, received_at)")
        .eq("emails.client_id", client_id)
        .gte("emails.received_at", f"{date_from}T00:00:00Z")
        .lte("emails.received_at", f"{date_to}T23:59:59Z")
        .execute()
    )
    counts: dict[str, int] = {}
    for r in res.data:
        i = r.get("intent", "altro")
        counts[i] = counts.get(i, 0) + 1
    return [{"intent": k, "count": v} for k, v in counts.items()]


def q_daily_volume(client_id: str, date_from: str, date_to: str) -> list[dict]:
    db = get_client()
    res = db.rpc("rpc_daily_volume", {
        "p_client_id": client_id,
        "p_date_from": f"{date_from}T00:00:00Z",
        "p_date_to":   f"{date_to}T23:59:59Z",
    }).execute()
    return res.data


def add_sender_to_blacklist(client_id: str, sender_email: str) -> bool:
    """Aggiunge un mittente alle custom_spam_keywords del tenant."""
    db = get_client()
    res = db.table("clients").select("custom_spam_keywords").eq("id", client_id).execute()
    if not res.data:
        return False
    current_keywords = res.data[0].get("custom_spam_keywords", [])
    if sender_email not in current_keywords:
        current_keywords.append(sender_email)
        db.table("clients").update({"custom_spam_keywords": current_keywords}).eq("id", client_id).execute()
    return True
