"""Chat history e pending edits per il bot Telegram."""

import logging
from typing import Optional
from .connection import get_client
from .contacts import get_client_id_by_telegram_chat_id

logger = logging.getLogger("polpo.database")


def set_pending_edit(chat_id: str, draft_id: str, client_id: Optional[str] = None) -> None:
    """
    Registra che un operatore sta modificando una bozza.
    client_id opzionale: se non passato viene ricavato dal DB.
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
        "role":      "assistant",
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


def save_chat_message(client_id: str, chat_id: str, role: str, content: object) -> bool:
    """Salva un messaggio della chat assistente (Telegram) su DB."""
    try:
        db = get_client()
        db.table("chat_history").insert({
            "client_id": client_id,
            "chat_id":   str(chat_id),
            "role":      role,
            "content":   content,
        }).execute()
        return True
    except Exception as e:
        logger.error("save_chat_message | Errore: %s", e)
        raise


def get_chat_history(chat_id: str, limit: int = 10) -> list:
    """Recupera gli ultimi N messaggi della chat in ordine cronologico."""
    try:
        db = get_client()
        res = (
            db.table("chat_history")
            .select("role, content")
            .eq("chat_id", str(chat_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data[::-1]
    except Exception as e:
        logger.error("get_chat_history | Errore: %s", e)
        return []
