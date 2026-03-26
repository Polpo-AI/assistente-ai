"""CRUD contatti e lookup client per chat_id Telegram."""

import logging
from typing import Optional
from .connection import get_client

logger = logging.getLogger("polpo.database")


def get_client_id_by_telegram_chat_id(telegram_chat_id: str) -> Optional[str]:
    db = get_client()
    result = db.table("clients").select("id").eq("telegram_chat_id", telegram_chat_id).execute()
    return result.data[0]["id"] if result.data else None


def upsert_contact(client_id: str, email: str, name: str, contact_type: str) -> dict:
    """Inserisce o aggiorna un contatto. NON sovrascrive contact_type impostato manualmente."""
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
