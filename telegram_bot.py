"""
[AI REFERENCE] Per una visione d'insieme dell'architettura e del flusso logico, 
leggere il file: PROJECT_SUMMARY.md
"""

"""
telegram_bot.py — Bot Telegram per approvazione bozze Polpo AI

Flusso:
  1. notify_draft(draft_id) — Inviato da main.py dopo /classify (priority >= 2)
  2. Webhook POST /telegram/webhook — Riceve update Telegram, delega qui
  3. handle_update(data) — Router per callback e testi liberi

Bottoni 2x2:
  [ ✅ Invia ]         [ ✏️ Modifica    ]
  [ ⏸ Lascia per dopo] [ 🗑 Ignora      ]

Variabili d'ambiente richieste:
  TELEGRAM_BOT_TOKEN=...
"""

import os
import logging
from typing import Optional

import json
import httpx
from anthropic import Anthropic
from anthropic.types import MessageParam
from dotenv import load_dotenv

from database import (
    get_draft_by_id,
    approve_draft,
    ignore_draft,
    save_telegram_message_id,
    update_draft_body,
    get_client_id_by_telegram_chat_id,
)
from responder import refine_draft
import query_tools
from notifications import notify_missing_feature
from models_config import TELEGRAM_ASSISTANT_MODEL

load_dotenv()

logger = logging.getLogger("polpo.telegram")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Mappa draft_id → utente in attesa di istruzione modifica
# { chat_id: draft_id }  (in memoria, senza Redis per ora)
_pending_edits: dict[str, str] = {}

# Storico conversazioni: { chat_id: [MessageParam] } max 5 messaggi per chat_id
_conversations: dict[str, list[MessageParam]] = {}



# ─────────────────────────────────────────────
# Helpers HTTP
# ─────────────────────────────────────────────

def _tg_post(method: str, payload: dict) -> dict:
    """Chiama l'API Telegram in modo sincrono."""
    try:
        r = httpx.post(f"{TELEGRAM_API}/{method}", json=payload, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("telegram | %s fallita: %s", method, e)
        return {}


def _build_buttons(draft_id: str) -> dict:
    """Griglia 2x2 con i 4 bottoni inline."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Invia",           "callback_data": f"invia:{draft_id}"},
                {"text": "✏️ Modifica",        "callback_data": f"modifica:{draft_id}"},
            ],
            [
                {"text": "⏸ Lascia per dopo", "callback_data": f"snooze:{draft_id}"},
                {"text": "🗑 Ignora",          "callback_data": f"ignora:{draft_id}"},
            ],
        ]
    }


def _format_message(draft: dict) -> str:
    """Formatta il messaggio Telegram con email + bozza."""
    email = draft.get("emails") or {}
    return (
        f"📧 *Da:* {email.get('sender_name', '')} <{email.get('sender_email', '')}>\n"
        f"*Oggetto:* {email.get('subject', '')}\n\n"
        f"*Email originale:*\n{email.get('body', '')[:800]}\n\n"
        f"————————————————\n"
        f"*✍️ Bozza risposta:*\n{draft.get('body', '')}"
    )


# ─────────────────────────────────────────────
# Notifica push quando arriva una nuova bozza
# ─────────────────────────────────────────────

def notify_draft(draft_id: str) -> None:
    """
    Invia il messaggio Telegram al tenant corretto.
    Chiamato da main.py dopo la classificazione (priority >= 2).
    """
    draft = get_draft_by_id(draft_id)
    if not draft:
        logger.warning("notify_draft | draft_id=%s non trovato", draft_id[:8])
        return

    client_data = draft.get("clients") or {}
    chat_id = client_data.get("telegram_chat_id")
    if not chat_id:
        logger.info("notify_draft | draft_id=%s — nessun chat_id Telegram per questo cliente", draft_id[:8])
        return

    text = _format_message(draft)
    resp = _tg_post("sendMessage", {
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "Markdown",
        "reply_markup": _build_buttons(draft_id),
    })

    message_id = resp.get("result", {}).get("message_id")
    if message_id:
        save_telegram_message_id(draft_id, message_id)
        logger.info("notify_draft | draft_id=%s inviato su chat_id=%s msg_id=%s",
                    draft_id[:8], chat_id, message_id)


# ─────────────────────────────────────────────
# Gestione update in arrivo dal webhook
# ─────────────────────────────────────────────

def handle_update(data: dict) -> None:
    """
    Router principale per gli update Telegram.
    Chiamato da POST /telegram/webhook in main.py.
    """
    if "callback_query" in data:
        _handle_callback(data["callback_query"])
    elif "message" in data:
        _handle_message(data["message"])


def _handle_callback(cq: dict) -> None:
    """Gestisce i click sui bottoni inline."""
    callback_id = cq.get("id")
    raw_data    = cq.get("data", "")
    chat_id     = cq.get("message", {}).get("chat", {}).get("id")
    message_id  = cq.get("message", {}).get("message_id")

    # Risponde subito a Telegram per togliere il loader
    _tg_post("answerCallbackQuery", {"callback_query_id": callback_id})

    if ":" not in raw_data:
        return

    action, draft_id = raw_data.split(":", 1)
    draft = get_draft_by_id(draft_id)

    if not draft:
        _edit_message(chat_id, message_id, "⚠️ Bozza non trovata.")
        return

    # Gestione conflitti: bozza già gestita da altro canale
    if draft.get("status") != "pending":
        _edit_message(chat_id, message_id,
                      "⚠️ Questa email è già stata gestita.")
        logger.info("callback | draft_id=%s già in stato '%s' — conflitto ignorato",
                    draft_id[:8], draft.get("status"))
        return

    if action == "invia":
        approve_draft(draft_id, approved_by="telegram")
        _edit_message(chat_id, message_id, "✅ Bozza inviata.")
        logger.info("callback | draft_id=%s approvata da Telegram", draft_id[:8])

    elif action == "ignora":
        ignore_draft(draft_id)
        _edit_message(chat_id, message_id, "🗑 Email ignorata.")
        logger.info("callback | draft_id=%s ignorata da Telegram", draft_id[:8])

    elif action == "snooze":
        # Rimane pending — solo rimuove i bottoni dal messaggio Telegram
        _edit_message(chat_id, message_id,
                      "⏸ Lasciata per dopo — gestisci dalla dashboard.")
        logger.info("callback | draft_id=%s snooze, bottoni rimossi", draft_id[:8])

    elif action == "modifica":
        # Segna che questo chat è in attesa di istruzione per questa bozza
        _pending_edits[str(chat_id)] = draft_id
        _tg_post("sendMessage", {
            "chat_id": chat_id,
            "text":    "✏️ Scrivi la modifica da fare:",
        })
        logger.info("callback | draft_id=%s in attesa istruzione modifica", draft_id[:8])


def _handle_message(msg: dict) -> None:
    """
    Gestisce testo libero inviato dall'operatore.
    Se è in attesa di modifica bozza, elabora la modifica.
    Altrimenti, avvia il loop conversazionale con data retrieval.
    """
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text    = msg.get("text", "").strip()

    if not text or not chat_id:
        return

    # 1. È una modifica bozza in corso?
    if chat_id in _pending_edits:
        _process_draft_edit(chat_id, text)
        return

    # 2. Assistente Conversazionale
    _process_conversational_query(chat_id, text)


def _process_draft_edit(chat_id: str, instruction: str) -> None:
    """Modifica bozza e rimanda con bottoni."""
    draft_id = _pending_edits.pop(chat_id)
    logger.info("modifica | draft_id=%s istruzione ricevuta: %s", draft_id[:8], instruction[:50])

    anthropic_client = Anthropic()
    result = refine_draft(draft_id, instruction, anthropic_client)

    if not result:
        _tg_post("sendMessage", {"chat_id": chat_id, "text": "⚠️ Errore nella modifica. Riprova."})
        return

    draft = get_draft_by_id(draft_id)
    if draft:
        _tg_post("sendMessage", {
            "chat_id": chat_id,
            "text": _format_message(draft),
            "parse_mode": "Markdown",
            "reply_markup": _build_buttons(draft_id),
        })
        logger.info("modifica | draft_id=%s rimandato aggiornato", draft_id[:8])


def _process_conversational_query(chat_id: str, text: str) -> None:
    """
    Loop function calling con Claude: riceve domanda, invoca query (se necessario),
    risponde, e salva nello storico (max 5 coppie).
    """
    client_id = get_client_id_by_telegram_chat_id(chat_id)
    if not client_id:
        _tg_post("sendMessage", {"chat_id": chat_id, "text": "⚠️ Non sei associato a nessun tenant Polpo AI."})
        return

    hist = _conversations.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text})

    anthropic_client = Anthropic()
    logger.info("conversazione | chat_id=%s domanda: %s", chat_id, text[:50])

    # Manda indicatore "sto scrivendo..."
    _tg_post("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    try:
        # Loop: Claude può chiamare tool => eseguiamo => reinviamo => risposta finale
        response = anthropic_client.messages.create(
            model=TELEGRAM_ASSISTANT_MODEL,
            max_tokens=1000,
            timeout=40.0,
            system=(
                "Sei l'assistente analitico di Polpo AI Mail Intelligence. "
                "Aiuti l'operatore a interrogare il database delle email ricorrendo ai tool. "
                "Se la domanda è ambigua, chiedi chiarimenti. "
                "Se elenchi email da gestire, concludi sempre chiedendo 'Vuoi gestirne qualcuna adesso?'."
                "\n\nIMPORTANTE: Se la richiesta dell'operatore non rientra in nessuna funzione disponibile "
                "e non puoi risolverla, usa il tool 'report_unsupported_feature' come ultimo fallback. "
                "In questo caso specifico, DOPO aver usato il tool, rispondi ALL'UTENTE ESATTAMENTE con: "
                "'Questa funzione non è ancora disponibile. Ho già notificato il developer per aggiungerla al più presto. Posso aiutarti con qualcos'altro?'"
            ),
            messages=hist,
            tools=query_tools.TOOLS,
        )

        # Se Claude usa un tool
        while response.stop_reason == "tool_use":
            tool_use = next(b for b in response.content if b.type == "tool_use")
            logger.info("conversazione | chat_id=%s usa tool: %s", chat_id, tool_use.name)

            if tool_use.name == "send_draft_card":
                # Intercettiamo in locale: inviamo la card e notifichiamo Claude del successo
                draft_id = tool_use.input.get("draft_id")
                draft = get_draft_by_id(draft_id)
                if draft:
                    _tg_post("sendMessage", {
                        "chat_id": chat_id,
                        "text": _format_message(draft),
                        "parse_mode": "Markdown",
                        "reply_markup": _build_buttons(draft_id),
                    })
                    tool_result = f"Card per {draft_id[:8]} inviata all'operatore con bottoni Invia/Modifica/ecc."
                else:
                    tool_result = f"Errore: draft_id {draft_id} non trovato."
            
            elif tool_use.name == "report_unsupported_feature":
                # Invio email notifica admin con history
                user_req = tool_use.input.get("user_message", "")
                notify_missing_feature(client_id, chat_id, user_req, hist[:-1]) # Escludiamo l'ultimo tool call block
                tool_result = "Il developer è stato notificato correttamente via email."

            else:
                # Esecuzione standard query a DB (o summarize Sonnet)
                tool_result = query_tools.dispatch(tool_use.name, tool_use.input, client_id, anthropic_client)

            # Aggiungiamo il passaggio al context
            hist.append({"role": "assistant", "content": response.content})
            hist.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": tool_result}]
            })

            # Reinvocazione per fargli leggere il dato ed elaborare risposta
            response = anthropic_client.messages.create(
                model=TELEGRAM_ASSISTANT_MODEL,
                max_tokens=1000,
                timeout=40.0,
                messages=hist,
                tools=query_tools.TOOLS,
            )

        final_text = next((block.text for block in response.content if getattr(block, 'text', None)), "Nessuna risposta.")
        hist.append({"role": "assistant", "content": final_text})

        # Manteniamo solo ultime 5 coppie per limitare i token
        if len(hist) > 10:
            _conversations[chat_id] = hist[-10:]

        _tg_post("sendMessage", {"chat_id": chat_id, "text": final_text})

    except Exception as e:
        logger.error("conversazione | chat_id=%s errore Claude: %s", chat_id, e)
        hist.pop()  # Rimuovi l'ultima domanda fallita
        _tg_post("sendMessage", {"chat_id": chat_id, "text": "Scusa, c'è stato un problema nel recuperare i dati."})


# ─────────────────────────────────────────────
# Helper: edit messaggio esistente
# ─────────────────────────────────────────────

def _edit_message(chat_id, message_id: int, text: str) -> None:
    """Edita un messaggio esistente rimuovendo i bottoni."""
    _tg_post("editMessageText", {
        "chat_id":    chat_id,
        "message_id": message_id,
        "text":       text,
    })
