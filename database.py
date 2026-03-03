"""
database.py — Layer Supabase per Polpo AI Email Bot (v2 Multi-Tenant)

Ogni operazione richiede client_id per isolare i dati tra clienti.

Dipendenze:
    pip install supabase python-dotenv

Variabili d'ambiente (.env):
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_KEY=your-service-role-key
"""

import os
import re
import logging
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

logger = logging.getLogger("polpo.database")

# ─────────────────────────────────────────────
# Client singleton
# ─────────────────────────────────────────────

_supabase: Optional[Client] = None

def get_client() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_KEY"]
        _supabase = create_client(url, key)
    return _supabase


def get_client_id_by_telegram_chat_id(telegram_chat_id: str) -> Optional[str]:
    db = get_client()
    result = db.table("clients").select("id").eq("telegram_chat_id", telegram_chat_id).execute()
    return result.data[0]["id"] if result.data else None


# ─────────────────────────────────────────────
# CONTACTS
# ─────────────────────────────────────────────

def upsert_contact(client_id: str, email: str, name: str, contact_type: str) -> dict:
    """
    Inserisce o aggiorna un contatto per uno specifico cliente.
    NON sovrascrive contact_type impostato manualmente.
    """
    db = get_client()

    existing = (
        db.table("contacts")
        .select("*")
        .eq("client_id", client_id)
        .eq("email", email.lower())
        .execute()
    )

    if existing.data:
        contact = existing.data[0]
        if contact["name"] != name:
            db.table("contacts").update({"name": name}).eq("id", contact["id"]).execute()
            contact["name"] = name
        return contact

    result = db.table("contacts").insert({
        "client_id":    client_id,
        "email":        email.lower(),
        "name":         name,
        "contact_type": contact_type,
    }).execute()

    return result.data[0]


def get_contact_by_email(client_id: str, email: str) -> Optional[dict]:
    """Cerca contatto per email e cliente. Restituisce None se non trovato."""
    db = get_client()
    result = (
        db.table("contacts")
        .select("*")
        .eq("client_id", client_id)
        .eq("email", email.lower())
        .execute()
    )
    return result.data[0] if result.data else None


# ─────────────────────────────────────────────
# CONVERSATIONS
# ─────────────────────────────────────────────

def _normalize_subject(subject: str) -> str:
    """Rimuove Re:/Fwd: per raggruppare thread."""
    return re.sub(r"^(re|fwd|r|fw|inoltro):\s*", "", subject.strip(), flags=re.IGNORECASE).strip()


def find_or_create_conversation(client_id: str, contact_id: str, subject: str) -> dict:
    """Trova conversazione esistente per thread oppure ne crea una nuova."""
    db = get_client()
    thread = _normalize_subject(subject)

    existing = (
        db.table("conversations")
        .select("*")
        .eq("client_id", client_id)
        .eq("contact_id", contact_id)
        .eq("subject_thread", thread)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if existing.data:
        conv = existing.data[0]
        db.table("conversations").update({
            "email_count":  conv["email_count"] + 1,
            "last_email_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", conv["id"]).execute()
        conv["email_count"] += 1
        return conv

    result = db.table("conversations").insert({
        "client_id":     client_id,
        "contact_id":    contact_id,
        "subject_thread": thread,
    }).execute()

    return result.data[0]


def get_conversation_history(conversation_id: str, limit: int = 3) -> list[dict]:
    """Ultime N email di una conversazione con classificazione."""
    db = get_client()
    result = (
        db.table("emails")
        .select("id, received_at, subject, body, email_classifications(*)")
        .eq("conversation_id", conversation_id)
        .order("received_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


# ─────────────────────────────────────────────
# EMAILS
# ─────────────────────────────────────────────

def save_email(
    client_id:       str,
    sender_email:    str,
    sender_name:     str,
    subject:         str,
    body:            str,
    attachments:     list[str],
    contact_id:      Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> dict:
    db = get_client()
    result = db.table("emails").insert({
        "client_id":       client_id,
        "sender_email":    sender_email.lower(),
        "sender_name":     sender_name,
        "subject":         subject,
        "body":            body,
        "attachments":     attachments,
        "contact_id":      contact_id,
        "conversation_id": conversation_id,
        "received_at":     datetime.now(timezone.utc).isoformat(),
    }).execute()
    return result.data[0]


# ─────────────────────────────────────────────
# CLASSIFICATIONS
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# DRAFT RESPONSES
# ─────────────────────────────────────────────

def save_draft(
    email_id:          str,
    client_id:         str,
    subject:           str,
    body:              str,
    suggested_actions: list[str],
    final_intent:      str,
    reclassified:      bool = False,
    warning:           Optional[str] = None,
) -> dict:
    """Salva la bozza generata dal Responder."""
    db = get_client()
    result = db.table("draft_responses").insert({
        "email_id":          email_id,
        "client_id":         client_id,
        "subject":           subject,
        "body":              body,
        "suggested_actions": suggested_actions,
        "final_intent":      final_intent,
        "reclassified":      reclassified,
        "warning":           warning,
        "status":            "pending",
    }).execute()
    return result.data[0]


def approve_draft(draft_id: str, approved_by: str) -> dict:
    """Marca una bozza come approvata (chiamata dalla dashboard o da Telegram)."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status":      "approved",
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", draft_id).execute()
    return result.data[0]


def ignore_draft(draft_id: str) -> dict:
    """Marca una bozza come ignorata (definitivo, solo da Telegram)."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status": "ignored",
    }).eq("id", draft_id).execute()
    return result.data[0]


def mark_draft_sent(draft_id: str) -> dict:
    """Marca una bozza come inviata."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status":  "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", draft_id).execute()
    return result.data[0]


def save_telegram_message_id(draft_id: str, message_id: int) -> None:
    """Salva l'ID del messaggio Telegram per editarlo in seguito."""
    db = get_client()
    db.table("draft_responses").update({
        "telegram_message_id": message_id,
    }).eq("id", draft_id).execute()


def get_draft_by_id(draft_id: str) -> Optional[dict]:
    """Recupera una bozza completa per ID (usata dai callback Telegram)."""
    db = get_client()
    result = (
        db.table("draft_responses")
        .select("*, emails(sender_email, sender_name, subject, body), clients(telegram_chat_id, llm_tone, signature, name)")
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


# ─────────────────────────────────────────────
# FUNZIONE PRINCIPALE — salva tutto in una volta
# ─────────────────────────────────────────────

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
) -> dict:
    """
    Pipeline salvataggio completa:
    1. Upsert contatto
    2. Trova o crea conversazione
    3. Salva email
    4. Salva classificazione
    """
    contact = upsert_contact(client_id, sender_email, sender_name, contact_type)
    contact_id = contact["id"]

    conversation = find_or_create_conversation(client_id, contact_id, subject)
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


# ─────────────────────────────────────────────
# QUERY per dashboard e Responder
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# QUERY ASSISTENTE CONVERSAZIONALE (Telegram)
# ─────────────────────────────────────────────

def q_contact_emails(client_id: str, contact_email: str, limit: int = 10) -> list[dict]:
    db = get_client()
    res = db.table("emails").select("id, received_at, sender_name, sender_email, subject, body").eq("client_id", client_id).ilike("sender_email", f"%{contact_email}%").order("received_at", desc=True).limit(limit).execute()
    return res.data

def q_emails_in_range(client_id: str, date_from: str, date_to: str, limit: int = 50) -> list[dict]:
    db = get_client()
    res = db.table("emails").select("*").eq("client_id", client_id).gte("received_at", f"{date_from}T00:00:00Z").lte("received_at", f"{date_to}T23:59:59Z").order("received_at", desc=True).limit(limit).execute()
    return res.data

def q_top_senders(client_id: str, date_from: str, date_to: str, limit: int = 5) -> list[dict]:
    # Non essendoci GROUP BY nativo nell'API Supabase free-tier, recuperiamo le email e raggruppiamo in Python
    # Per grosse moli andrebbe fatta una stored procedure RPC.
    rows = q_emails_in_range(client_id, date_from, date_to, limit=1000)
    counts = {}
    for r in rows:
        email = r.get("sender_email", "sconosciuto")
        counts[email] = counts.get(email, 0) + 1
    sorted_senders = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [{"sender_email": k, "count": v} for k, v in sorted_senders]

def q_unanswered_emails(client_id: str, limit: int = 20) -> list[dict]:
    db = get_client()
    res = db.table("emails").select("*, draft_responses!left(status)").eq("client_id", client_id).order("received_at", desc=True).limit(limit).execute()
    # Filtriamo dove non ci sono bozze o lo status non è approved/sent (cioè ignorate o nessuna/pending)
    unanswered = [r for r in res.data if not r.get("draft_responses") or all(d.get("status") not in ("approved", "sent") for d in r["draft_responses"])]
    return unanswered[:limit]

def q_drafts_by_status(client_id: str, status: str, limit: int = 20) -> list[dict]:
    db = get_client()
    res = db.table("draft_responses").select("id, created_at, subject, body, final_intent, status").eq("client_id", client_id).eq("status", status).order("created_at", desc=True).limit(limit).execute()
    return res.data

def q_drafts_sent_in_range(client_id: str, date_from: str, date_to: str, limit: int = 50) -> list[dict]:
    db = get_client()
    res = db.table("draft_responses").select("id, sent_at, subject, body, final_intent, status").eq("client_id", client_id).eq("status", "sent").gte("sent_at", f"{date_from}T00:00:00Z").lte("sent_at", f"{date_to}T23:59:59Z").order("sent_at", desc=True).limit(limit).execute()
    return res.data

def q_pending_older_than(client_id: str, hours: int, limit: int = 20) -> list[dict]:
    db = get_client()
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    res = db.table("draft_responses").select("*").eq("client_id", client_id).eq("status", "pending").lte("created_at", cutoff).order("created_at", desc=True).limit(limit).execute()
    return res.data

def q_intent_stats(client_id: str, date_from: str, date_to: str) -> list[dict]:
    db = get_client()
    res = db.table("email_classifications").select("intent").eq("client_id", client_id).gte("created_at", f"{date_from}T00:00:00Z").lte("created_at", f"{date_to}T23:59:59Z").execute()
    counts = {}
    for r in res.data:
        i = r.get("intent", "altro")
        counts[i] = counts.get(i, 0) + 1
    return [{"intent": k, "count": v} for k, v in counts.items()]

def q_daily_volume(client_id: str, date_from: str, date_to: str) -> list[dict]:
    # Stessa cosa, mock in python raggruppando (RPC sarebbe meglio)
    emails = q_emails_in_range(client_id, date_from, date_to, limit=1000)
    drafts = q_drafts_sent_in_range(client_id, date_from, date_to, limit=1000)
    
    days = {}
    for e in emails:
        d = e.get("received_at", "")[:10]
        if d:
            if d not in days: days[d] = {"received": 0, "sent": 0}
            days[d]["received"] += 1
            
    for dr in drafts:
        d = dr.get("sent_at", "")[:10]
        if d:
            if d not in days: days[d] = {"received": 0, "sent": 0}
            days[d]["sent"] += 1
            
    res = [{"day": k, "received": v["received"], "sent": v["sent"]} for k, v in days.items()]
    return sorted(res, key=lambda x: x["day"])

def add_sender_to_blacklist(client_id: str, sender_email: str) -> bool:
    """Aggiunge un mittente alle custom_spam_keywords del tenant (così le prossime andranno in spam_ignored)."""
    db = get_client()
    res = db.table("clients").select("custom_spam_keywords").eq("id", client_id).execute()
    if not res.data:
        return False
        
    current_keywords = res.data[0].get("custom_spam_keywords", [])
    if sender_email not in current_keywords:
        current_keywords.append(sender_email)
        db.table("clients").update({"custom_spam_keywords": current_keywords}).eq("id", client_id).execute()
    return True
