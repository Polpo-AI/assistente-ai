"""
database.py — Layer Supabase per Polpo AI Email Bot (v3 Multi-Tenant)

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

import threading

_supabase: Optional[Client] = None
_supabase_lock = threading.Lock()

def get_client() -> Client:
    global _supabase
    if _supabase is None:
        with _supabase_lock:
            if _supabase is None:  # double-checked locking
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


def get_existing_contact_types(client_id: str) -> list[str]:
    """Restituisce i valori distinti di contact_type già usati per questo cliente."""
    db = get_client()
    result = (
        db.table("contacts")
        .select("contact_type")
        .eq("client_id", client_id)
        .execute()
    )
    types = list({r["contact_type"] for r in result.data if r.get("contact_type")})
    return sorted(types)


# ─────────────────────────────────────────────
# CONVERSATIONS
# ─────────────────────────────────────────────

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

    Strategia di raggruppamento (in ordine di priorità):

    1. RFC822 in_reply_to → cerca l'email con quel message_id e usa
       la sua conversation_id. Questo è il criterio corretto per i thread
       email — garantisce che "Re: Preventivo" di marzo non si mescoli
       con "Re: Preventivo" di gennaio.

    2. Nessun in_reply_to → nuova email indipendente → nuova conversazione.
       Non usiamo più il subject per raggruppare: troppo ambiguo.
       Il subject viene salvato solo come etichetta leggibile.
    """
    db = get_client()

    # Criterio 1: thread RFC822
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
            # Aggiorna contatori
            conv = db.table("conversations").select("*").eq("id", conv_id).execute()
            if conv.data:
                c = conv.data[0]
                db.table("conversations").update({
                    "email_count":   c["email_count"] + 1,
                    "last_email_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", conv_id).execute()
                c["email_count"] += 1
                return c

    # Criterio 2: nuova conversazione
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
    Include le email inviate dal bot (direction=outbound) per dare a Sonnet
    il contesto completo dello scambio — non solo le domande ma anche le risposte date.
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
    # Restituisce in ordine cronologico (dal più vecchio) per il prompt
    return list(reversed(result.data))


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



def update_draft_status(draft_id: str, status: str) -> dict:
    """Aggiorna lo stato di una bozza."""
    db = get_client()
    result = db.table("draft_responses").update({
        "status": status,
    }).eq("id", draft_id).execute()
    return result.data[0] if result.data else {}

def mark_email_no_reply(email_id: str, summary: str = "") -> dict:
    """
    Crea una voce in draft_responses con stato 'no_reply_needed' per email Priority 0.
    In questo modo tracciamo che l'email è stata gestita anche senza bozza.
    """
    db = get_client()
    
    # Recuperiamo il client_id dall'email
    email_res = db.table("emails").select("client_id, subject").eq("id", email_id).execute()
    if not email_res.data:
        return {}
    
    email_data = email_res.data[0]
    
    # Verifica se esiste già
    existing = db.table("draft_responses").select("id").eq("email_id", email_id).execute()
    
    data = {
        "email_id":    email_id,
        "client_id":   email_data["client_id"],
        "subject":     email_data["subject"],
        "body":        "[Nessuna risposta necessaria - Sistema Polpo AI]",
        "status":      "sent",
        "final_intent": "cortesia",
        "warning":     summary or "Email automatica o di cortesia."
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


def save_outbound_email(
    client_id: str,
    draft_id: str,
    sent_message_id: str,
    in_reply_to: str,
    references_ids: list,
    sender_email: str,
    sender_name: str,
    recipient_email: str,
    subject: str,
    body: str,
) -> None:
    """
    Crea un record nella tabella emails per ogni email inviata (direction='outbound').
    Permette la ricostruzione completa del thread.
    """
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
    """Recupera una bozza completa per ID (usata dai callback Telegram)."""
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
    in_reply_to:     str = "",
) -> dict:
    """
    Pipeline salvataggio completa:
    1. Upsert contatto
    2. Trova o crea conversazione (via RFC822 in_reply_to se disponibile)
    3. Salva email
    4. Salva classificazione
    """
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


def get_approved_drafts() -> list[dict]:
    """Cerca bozze che sono state approvate dall'utente e devono essere inviate."""
    db = get_client()
    # Recuperiamo info sulla bozza, sull'email originale e sulle credenziali SMTP del cliente
    result = (
        db.table("draft_responses")
        .select("id, client_id, subject, body, email_id, telegram_message_id, "
                "emails(sender_email, sender_name, subject, message_id), "
                "clients(smtp_host, smtp_port, smtp_user, smtp_password, name, telegram_chat_id)")
        .eq("status", "approved")
        .execute()
    )
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
    # Non selezioniamo all attachments o l'intero body per risparmiare RAM e tempo di trasferimento
    res = db.table("emails").select("id, received_at, sender_name, sender_email, subject").eq("client_id", client_id).gte("received_at", f"{date_from}T00:00:00Z").lte("received_at", f"{date_to}T23:59:59Z").order("received_at", desc=True).limit(limit).execute()
    return res.data

def q_top_senders(client_id: str, date_from: str, date_to: str, limit: int = 5) -> list[dict]:
    """Recupera i mittenti principali tramite procedura RPC sul DB."""
    db = get_client()
    res = db.rpc("rpc_top_senders", {
        "p_client_id": client_id,
        "p_date_from": f"{date_from}T00:00:00Z",
        "p_date_to": f"{date_to}T23:59:59Z",
        "p_limit": limit
    }).execute()
    return res.data

def q_unanswered_emails(client_id: str, limit: int = 20) -> list[dict]:
    db = get_client()
    res = db.table("v_unanswered_emails").select("*").eq("client_id", client_id).order("received_at", desc=True).limit(limit).execute()
    return res.data

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
    # email_classifications non ha client_id — filtriamo via join con emails
    db = get_client()
    res = db.table("email_classifications") \
        .select("intent, emails!inner(client_id, received_at)") \
        .eq("emails.client_id", client_id) \
        .gte("emails.received_at", f"{date_from}T00:00:00Z") \
        .lte("emails.received_at", f"{date_to}T23:59:59Z") \
        .execute()
    counts = {}
    for r in res.data:
        i = r.get("intent", "altro")
        counts[i] = counts.get(i, 0) + 1
    return [{"intent": k, "count": v} for k, v in counts.items()]

def q_daily_volume(client_id: str, date_from: str, date_to: str) -> list[dict]:
    """Recupera il volume giornaliero tramite procedura RPC sul DB."""
    db = get_client()
    res = db.rpc("rpc_daily_volume", {
        "p_client_id": client_id,
        "p_date_from": f"{date_from}T00:00:00Z",
        "p_date_to": f"{date_to}T23:59:59Z"
    }).execute()
    return res.data

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


# ─────────────────────────────────────────────
# PENDING EDITS — stato modifica bozza Telegram
# Spostato su DB per supportare multi-worker FastAPI
# ─────────────────────────────────────────────

def set_pending_edit(chat_id: str, draft_id: str, client_id: Optional[str] = None) -> None:
    """
    Registra che un operatore sta modificando una bozza.
    client_id opzionale: se non passato viene ricavato dal DB (query extra).
    Passarlo direttamente dal bot quando già disponibile per evitare la query.
    """
    db = get_client()
    if not client_id:
        client_id = get_client_id_by_telegram_chat_id(chat_id)
    if not client_id:
        logger.warning("set_pending_edit | client_id non trovato per chat_id=%s", chat_id)
        return
    db.table("chat_history").insert({
        "client_id": client_id,
        "chat_id":   str(chat_id),
        "role":      "assistant",  # 'system' non permesso dal constraint live — _type nel content identifica il record
        "content":   {"_type": "pending_edit", "draft_id": draft_id},
    }).execute()


def get_pending_edit(chat_id: str) -> Optional[str]:
    """Restituisce il draft_id in attesa di modifica per questo chat, o None."""
    db = get_client()
    result = (
        db.table("chat_history")
        .select("id, content")
        .eq("chat_id", str(chat_id))
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    for row in result.data or []:
        content = row.get("content", {})
        if isinstance(content, dict) and content.get("_type") == "pending_edit":
            return content.get("draft_id")
    return None


def clear_pending_edit(chat_id: str) -> None:
    """Rimuove il pending edit per questo chat dopo che è stato processato."""
    db = get_client()
    result = (
        db.table("chat_history")
        .select("id, content")
        .eq("chat_id", str(chat_id))
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )
    for row in result.data or []:
        content = row.get("content", {})
        if isinstance(content, dict) and content.get("_type") == "pending_edit":
            db.table("chat_history").delete().eq("id", row["id"]).execute()
            return

def save_chat_message(client_id: str, chat_id: str, role: str, content: any) -> bool:
    """Salva un messaggio della chat assistente (Telegram) su DB."""
    try:
        db = get_client()
        db.table("chat_history").insert({
            "client_id": client_id,
            "chat_id": str(chat_id),
            "role": role,
            "content": content
        }).execute()
        return True
    except Exception as e:
        logger.error(f"Errore save_chat_message: {e}")
        raise e

def get_chat_history(chat_id: str, limit: int = 10) -> list:
    """Recupera gli ultimi N messaggi della chat per il conteggio context."""
    try:
        db = get_client()
        res = db.table("chat_history") \
                .select("role, content") \
                .eq("chat_id", str(chat_id)) \
                .order("created_at", desc=True) \
                .limit(limit) \
                .execute()
        
        # Invertiamo per avere ordine cronologico corretto per Claude
        history = res.data[::-1]
        return history
    except Exception as e:
        logger.error(f"Errore get_chat_history: {e}")
        return []
