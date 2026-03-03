"""
main.py — API FastAPI per Polpo AI Email Bot

Espone le rotte che n8n chiama per processare le email.

Rotte:
  POST /classify          ← n8n invia email in arrivo
  POST /draft/{email_id}  ← n8n chiede bozza per una email
  GET  /pending           ← dashboard legge email in attesa
  POST /approve/{draft_id}← dashboard approva una bozza
  GET  /health            ← n8n verifica che il server sia up

Dipendenze:
    pip install fastapi uvicorn anthropic supabase python-dotenv

Avvio:
    uvicorn main:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from pydantic import BaseModel
from typing import Optional
from anthropic import Anthropic
import os
import logging
from dotenv import load_dotenv

from classifier import classify_message, InboundMessage
from responder import generate_response_draft
from database import get_pending_emails, approve_draft, ignore_draft
import telegram_bot

load_dotenv()

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("polpo.api")

app = FastAPI(
    title="Polpo AI - Email Bot API",
    version="1.0.0",
)

anthropic_client = Anthropic()

# ─────────────────────────────────────────────
# Sicurezza — API Key semplice per n8n
# In produzione usa OAuth o JWT
# ─────────────────────────────────────────────

API_SECRET = os.environ.get("POLPO_API_SECRET", "changeme")

def verify_api_key(x_api_key: str = Header(...)) -> None:
    """Dipendenza FastAPI — protezione semplice per n8n.
    Usare come: Depends(verify_api_key) nelle rotte.
    In produzione sostituire con OAuth o JWT.
    """
    if x_api_key != API_SECRET:
        raise HTTPException(status_code=401, detail="API key non valida")


# ─────────────────────────────────────────────
# Modelli Pydantic — struttura richieste/risposte
# ─────────────────────────────────────────────

class IncomingEmail(BaseModel):
    client_id:    str
    sender_email: str
    sender_name:  str
    subject:      str
    body:         str
    attachments:  list[str] = []

class ApproveRequest(BaseModel):
    approved_by: str  # nome/email di chi approva

class ClassifyResponse(BaseModel):
    email_id:       str
    contact_type:   str
    intent:         str
    priority:       int
    confidence:     float
    classified_by:  str
    summary:        str
    estimated_value: Optional[float] = None
    # Indica al workflow n8n cosa fare dopo
    next_action:    str  # "send_auto" | "notify_whatsapp" | "alert_urgent"

class DraftResponse(BaseModel):
    email_id:          str
    draft_id:          Optional[str]
    subject:           str
    body:              str
    suggested_actions: list[str]
    final_intent:      str
    reclassified:      bool
    warning:           Optional[str] = None


# ─────────────────────────────────────────────
# ROTTE
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    """n8n chiama questa rotta per verificare che il server sia up."""
    return {"status": "ok", "service": "Polpo AI Email Bot"}


@app.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(verify_api_key)])
def classify(email: IncomingEmail):
    """
    Riceve una email da n8n, la classifica e la salva su Supabase.
    Restituisce il risultato + next_action per guidare il workflow n8n.

    next_action:
      "send_auto"       → Priority BASSA  → n8n chiama /draft e invia
      "notify_whatsapp" → Priority MEDIA  → n8n manda WhatsApp con bottoni
      "alert_urgent"    → Priority URGENTE→ n8n manda alert WhatsApp al titolare
    """
    msg = InboundMessage(
        sender_email=email.sender_email,
        sender_name=email.sender_name,
        subject=email.subject,
        body=email.body,
        attachments=email.attachments,
    )

    try:
        result = classify_message(
            msg=msg,
            client_id=email.client_id,
            llm_client=anthropic_client,
            use_real_db=True,
            save_to_db=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore classificazione: {str(e)}")

    if not result.db_ids:
        raise HTTPException(status_code=500, detail="Errore salvataggio DB")

    # Mappa priorità → azione n8n
    next_action_map = {
        1: "send_auto",
        2: "notify_whatsapp",
        3: "alert_urgent",
    }

    email_id = result.db_ids["email_id"]
    draft_id  = result.db_ids.get("draft_id")  # potrebbe non esistere ancora (generata dopo)

    # Notifica Telegram per email di priorità media o urgente
    if result.priority >= 2 and draft_id:
        try:
            telegram_bot.notify_draft(draft_id)
        except Exception as e:
            logger.warning("classify | notifica Telegram fallita: %s", e)

    return ClassifyResponse(
        email_id=email_id,
        contact_type=result.contact_type,
        intent=result.intent,
        priority=result.priority,
        confidence=result.confidence,
        classified_by=result.classified_by,
        summary=result.summary,
        estimated_value=result.estimated_value,
        next_action=next_action_map.get(result.priority, "notify_whatsapp"),
    )


@app.post("/draft/{email_id}", response_model=DraftResponse, dependencies=[Depends(verify_api_key)])
def create_draft(email_id: str):
    """
    Genera la bozza di risposta per una email già classificata.
    n8n chiama questa rotta dopo /classify.
    """
    try:
        draft = generate_response_draft(email_id, anthropic_client)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione bozza: {str(e)}")

    if not draft:
        raise HTTPException(status_code=404, detail="Email non trovata")

    return DraftResponse(
        email_id=draft.email_id,
        draft_id=draft.draft_id,
        subject=draft.subject,
        body=draft.body,
        suggested_actions=draft.suggested_actions,
        final_intent=draft.final_intent,
        reclassified=draft.reclassified,
        warning=draft.warning,
    )


@app.get("/pending", dependencies=[Depends(verify_api_key)])
def pending(
    client_id: str,
    priority: Optional[int] = None,
    limit: int = 20,
):
    """
    Restituisce email in attesa di gestione.
    Usata dalla dashboard Retool per mostrare la lista.
    """
    try:
        emails = get_pending_emails(client_id, priority=priority, limit=limit)
        return {"emails": emails, "count": len(emails)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/approve/{draft_id}", dependencies=[Depends(verify_api_key)])
def approve(draft_id: str, body: ApproveRequest):
    """
    Approva una bozza dalla dashboard o da WhatsApp.
    Cambia lo status da 'pending' ad 'approved'.
    n8n intercetta questo cambio e procede con l'invio.
    """
    try:
        approve_draft(draft_id, body.approved_by)
        return {"status": "approved", "draft_id": draft_id, "approved_by": body.approved_by}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ignore/{draft_id}", dependencies=[Depends(verify_api_key)])
def ignore(draft_id: str):
    """
    Ignora definitivamente una bozza.
    Disponibile sia da dashboard che da Telegram.
    """
    try:
        ignore_draft(draft_id)
        return {"status": "ignored", "draft_id": draft_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Webhook Telegram — riceve update e li delega a telegram_bot.
    NON protetto da API key: l'autenticità è garantita da Telegram
    (il token è nel path dell'URL di registrazione del webhook).
    """
    data = await request.json()
    try:
        telegram_bot.handle_update(data)
    except Exception as e:
        logger.error("telegram_webhook | errore: %s", e)
    return {"ok": True}


# ─────────────────────────────────────────────
# Avvio diretto
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
