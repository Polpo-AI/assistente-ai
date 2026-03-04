"""
[AI REFERENCE] Per una visione d'insieme dell'architettura e del flusso logico, 
leggere il file: PROJECT_SUMMARY.md
"""

"""
main.py — API FastAPI per Polpo AI Email Bot

Espone le rotte per processare le email dalla pipeline Python o dashboard.

Rotte:
  POST /classify          ← Invia email in arrivo per classificazione
  POST /draft/{email_id}  ← Chiede generazione bozza per una email
  GET  /pending           ← Dashboard legge email in attesa
  POST /approve/{draft_id}← Dashboard approva una bozza
  GET  /health            ← Verifica che il server sia up

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
# Sicurezza — API Key semplice per chiamate esterne
# In produzione usa OAuth o JWT
# ─────────────────────────────────────────────

API_SECRET = os.environ.get("POLPO_API_SECRET", "changeme")

def verify_api_key(x_api_key: str = Header(...)) -> None:
    """Dipendenza FastAPI — protezione semplice per chiamate API.
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
    """Verifica che il server sia up."""
    return {"status": "ok", "service": "Polpo AI Email Bot"}


@app.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(verify_api_key)])
def classify(email: IncomingEmail):
    """
    Riceve una email, la classifica e la salva su Supabase.
    Restituisce il risultato della classificazione.
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

    return ClassifyResponse(
        email_id=email_id,
        contact_type=result.contact_type,
        intent=result.intent,
        priority=result.priority,
        confidence=result.confidence,
        classified_by=result.classified_by,
        summary=result.summary,
        estimated_value=result.estimated_value,
    )


@app.post("/draft/{email_id}", response_model=DraftResponse, dependencies=[Depends(verify_api_key)])
def create_draft(email_id: str):
    """
    Genera la bozza di risposta per una email già classificata.
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
    Approva una bozza dalla dashboard o da Telegram.
    Cambia lo status da 'pending' ad 'approved'.
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
