"""
query_tools.py — Tool definitions per Claude function calling (Assistente Conversazionale)

Ogni tool corrisponde a una funzione che Claude può chiamare in linguaggio naturale.
Le query vengono eseguite sempre filtrate per client_id del tenant.

Modelli:
  - Haiku   → query dirette, veloci ed economiche
  - Sonnet  → summarize_emails (richiede ragionamento complesso)
"""

import logging
from typing import Optional
from anthropic import Anthropic

import database as db
from responder import generate_response_draft

logger = logging.getLogger("polpo.query_tools")

# ─────────────────────────────────────────────
# Definizioni Tool per l'API Anthropic
# ─────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_contact_emails",
        "description": "Recupera le email ricevute da un mittente specifico.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_email": {"type": "string", "description": "Indirizzo email del mittente"},
                "limit":         {"type": "integer", "description": "Numero massimo di email (default: 10)"},
            },
            "required": ["contact_email"],
        },
    },
    {
        "name": "get_last_email_from",
        "description": "Recupera l'ultima email ricevuta da un contatto specifico.",
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_email": {"type": "string", "description": "Indirizzo email del mittente"},
            },
            "required": ["contact_email"],
        },
    },
    {
        "name": "get_emails_in_range",
        "description": "Recupera le email ricevute in un arco di tempo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_top_senders",
        "description": "Chi ha scritto di più in un periodo, con conteggio email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
                "limit":     {"type": "integer", "description": "Numero di mittenti (default: 5)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_unanswered_emails",
        "description": "Email ricevute senza una bozza approvata (non ancora risposte).",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Numero massimo (default: 20)"},
            },
        },
    },
    {
        "name": "get_drafts_by_status",
        "description": "Recupera bozze filtrate per stato: pending, approved, ignored, sent.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "approved", "ignored", "sent"],
                    "description": "Stato della bozza",
                },
                "limit": {"type": "integer", "description": "Numero massimo (default: 20)"},
            },
            "required": ["status"],
        },
    },
    {
        "name": "get_drafts_sent_in_range",
        "description": "Bozze inviate (stat=sent) in un intervallo di date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_pending_older_than",
        "description": "Bozze in stato pending da più di N ore (da gestire da più tempo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Ore minime di attesa (es. 24)"},
            },
            "required": ["hours"],
        },
    },
    {
        "name": "get_intent_stats",
        "description": "Distribuzione degli intent classificati in un periodo (quanti preventivi, reclami, ecc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_daily_volume",
        "description": "Volumi giornalieri di email ricevute e bozze inviate in un periodo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "summarize_emails",
        "description": (
            "Legge le email di un periodo e restituisce un briefing riassuntivo in linguaggio naturale. "
            "Usa questo tool quando l'operatore vuole una panoramica, non dati grezzi."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data inizio (YYYY-MM-DD)"},
                "date_to":   {"type": "string", "description": "Data fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "send_draft_card",
        "description": (
            "Invia all'operatore la card interattiva con i bottoni (Invia, Modifica, ecc.) "
            "per una specifica bozza. Usalo QUANDO L'OPERATORE TI CHIEDE DI GESTIRE UN'EMAIL "
            "tra quelle che gli hai appena elencato (es. 'Sì, dammi la prima')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "L'ID completo della bozza da gestire"},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "change_draft_status",
        "description": "Approva (invia) o ignora direttamente una bozza pendente.",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string", "description": "L'ID della bozza."},
                "new_status": {"type": "string", "enum": ["approved", "ignored"], "description": "Stato da impostare."},
            },
            "required": ["draft_id", "new_status"],
        },
    },
    {
        "name": "add_sender_to_blacklist",
        "description": "Aggiunge l'indirizzo email di un mittente alle keyword di spam del cliente, così che in futuro le sue email vengano ignorate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender_email": {"type": "string", "description": "L'indirizzo email da bloccare."},
            },
            "required": ["sender_email"],
        },
    },
    {
        "name": "generate_draft_on_demand",
        "description": "Forza la generazione di una nuova bozza di risposta per un'email specifica (es. molto vecchia o precedentemente ignorata).",
        "input_schema": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description": "L'ID completo dell'email originale (email_id, non draft_id)."},
            },
            "required": ["email_id"],
        },
    },
    {
        "name": "report_unsupported_feature",
        "description": "Segnala che l'operatore ha chiesto una funzione non ancora implementata. Usalo solo come ULTIMO FALLBACK.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_message": {"type": "string", "description": "L'esatta richiesta dell'operatore che non può essere soddisfatta."},
            },
            "required": ["user_message"],
        },
    },
]


# ─────────────────────────────────────────────
# Dispatcher: esegue la funzione richiesta da Claude
# ─────────────────────────────────────────────

def dispatch(tool_name: str, tool_input: dict, client_id: str, anthropic_client: Anthropic) -> str:
    """
    Esegue il tool richiesto da Claude e restituisce il risultato come stringa.
    Ogni funzione è obbligatoriamente filtrata per client_id del tenant.
    """
    try:
        if tool_name == "get_contact_emails":
            rows = db.q_contact_emails(
                client_id,
                tool_input["contact_email"],
                tool_input.get("limit", 10),
            )
            return _fmt_emails(rows)

        elif tool_name == "get_last_email_from":
            rows = db.q_contact_emails(client_id, tool_input["contact_email"], limit=1)
            return _fmt_emails(rows) if rows else "Nessuna email trovata."

        elif tool_name == "get_emails_in_range":
            rows = db.q_emails_in_range(client_id, tool_input["date_from"], tool_input["date_to"])
            return _fmt_emails(rows)

        elif tool_name == "get_top_senders":
            rows = db.q_top_senders(
                client_id,
                tool_input["date_from"],
                tool_input["date_to"],
                tool_input.get("limit", 5),
            )
            if not rows:
                return "Nessun dato trovato."
            lines = [f"{i+1}. {r['sender_email']} — {r['count']} email" for i, r in enumerate(rows)]
            return "\n".join(lines)

        elif tool_name == "get_unanswered_emails":
            rows = db.q_unanswered_emails(client_id, tool_input.get("limit", 20))
            return _fmt_emails(rows)

        elif tool_name == "get_drafts_by_status":
            rows = db.q_drafts_by_status(
                client_id,
                tool_input["status"],
                tool_input.get("limit", 20),
            )
            return _fmt_drafts(rows)

        elif tool_name == "get_drafts_sent_in_range":
            rows = db.q_drafts_sent_in_range(client_id, tool_input["date_from"], tool_input["date_to"])
            return _fmt_drafts(rows)

        elif tool_name == "get_pending_older_than":
            rows = db.q_pending_older_than(client_id, tool_input["hours"])
            return _fmt_drafts(rows)

        elif tool_name == "get_intent_stats":
            rows = db.q_intent_stats(client_id, tool_input["date_from"], tool_input["date_to"])
            if not rows:
                return "Nessun dato nel periodo."
            lines = [f"• {r['intent']}: {r['count']}" for r in rows]
            return "\n".join(lines)

        elif tool_name == "get_daily_volume":
            rows = db.q_daily_volume(client_id, tool_input["date_from"], tool_input["date_to"])
            if not rows:
                return "Nessun dato nel periodo."
            lines = [f"{r['day']}: {r['received']} ricevute, {r['sent']} inviate" for r in rows]
            return "\n".join(lines)

        elif tool_name == "summarize_emails":
            # Usa Sonnet per la sintesi narrativa
            return _summarize_with_sonnet(
                client_id,
                tool_input["date_from"],
                tool_input["date_to"],
                anthropic_client,
            )

        elif tool_name == "send_draft_card":
            # Questo tool in realtà "invia" il messaggio Telegram, ma dal dispatcher restituiamo
            # solo una notifica fittizia a Claude. Il frontend su `telegram_bot.py` lo intercetta.
            return f"Card interattiva per {tool_input['draft_id']} inviata con successo all'operatore."

        elif tool_name == "change_draft_status":
            draft_id = tool_input["draft_id"]
            new_status = tool_input["new_status"]
            if new_status == "approved":
                db.approve_draft(draft_id, "telegram_bot")
                return f"Bozza {draft_id[:8]} approvata e messa in coda d'invio."
            elif new_status == "ignored":
                db.ignore_draft(draft_id)
                return f"Bozza {draft_id[:8]} ignorata."
            else:
                return f"Stato {new_status} non supportato"

        elif tool_name == "add_sender_to_blacklist":
            success = db.add_sender_to_blacklist(client_id, tool_input["sender_email"])
            if success:
                return f"Mittente {tool_input['sender_email']} aggiunto alla blacklist. Le future email verranno ignorate in automatico."
            else:
                return "Errore nell'aggiunta del mittente alla blacklist."

        elif tool_name == "generate_draft_on_demand":
            email_id = tool_input["email_id"]
            # Richiamiamo il flusso originario per la generazione:
            draft = generate_response_draft(email_id, anthropic_client)
            if draft:
                return f"Bozza generata con successo. L'ID della nuova bozza è {draft.draft_id}."
            else:
                return "Generazione della bozza fallita o email già con bozza pending in corso."

        elif tool_name == "report_unsupported_feature":
            # Questo tool verrà intercettato da telegram_bot.py per passare la history
            return "SUCCESS: Notifica inviata al developer."

        else:
            return f"Tool '{tool_name}' non riconosciuto."

    except Exception as e:
        logger.error("dispatch | tool=%s errore: %s", tool_name, e)
        return f"Errore nell'esecuzione di {tool_name}: {e}"


# ─────────────────────────────────────────────
# Summarize con Sonnet
# ─────────────────────────────────────────────

def _summarize_with_sonnet(client_id: str, date_from: str, date_to: str, client: Anthropic) -> str:
    """Usa Sonnet per sintetizzare le email del periodo in un briefing."""
    rows = db.q_emails_in_range(client_id, date_from, date_to, limit=50)
    if not rows:
        return "Nessuna email nel periodo selezionato."

    email_texts = "\n\n---\n\n".join(
        f"Da: {r.get('sender_email','')}\nOggetto: {r.get('subject','')}\n{r.get('body','')[:500]}"
        for r in rows[:30]
    )

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        timeout=60.0,
        system=(
            "Sei un assistente aziendale. Analizza le email e produci un briefing conciso in italiano: "
            "temi principali, richieste frequenti, urgenze, opportunità commerciali. "
            "Formato: punti elenco, max 200 parole."
        ),
        messages=[{"role": "user", "content": f"Email dal {date_from} al {date_to}:\n\n{email_texts}"}],
    )
    return response.content[0].text.strip()


# ─────────────────────────────────────────────
# Formatters
# ─────────────────────────────────────────────

def _fmt_emails(rows: list) -> str:
    if not rows:
        return "Nessuna email trovata."
    lines = []
    for r in rows:
        ts = (r.get("received_at") or "")[:10]
        lines.append(
            f"📧 [{ts}] {r.get('sender_name','')} <{r.get('sender_email','')}>\n"
            f"   Oggetto: {r.get('subject','')}\n"
            f"   ID: {r.get('id','')[:8]}…"
        )
    return "\n\n".join(lines)


def _fmt_drafts(rows: list) -> str:
    if not rows:
        return "Nessuna bozza trovata."
    lines = []
    for r in rows:
        ts = (r.get("created_at") or "")[:10]
        lines.append(
            f"📝 [{ts}] {r.get('final_intent','?')} — {r.get('status','?')}\n"
            f"   Oggetto: {r.get('subject','')}\n"
            f"   ID: {r.get('id','')[:8]}…"
        )
    return "\n\n".join(lines)
