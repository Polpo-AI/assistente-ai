"""
[AI REFERENCE] Per una visione d'insieme dell'architettura e del flusso logico, 
leggere il file: PROJECT_SUMMARY.md
"""

"""
responder.py — Modulo Responder (v3 Multi-Tenant)

Genera bozze di risposta personalizzate per settore.
Legge istruzioni, tono e firma dalla ClientConfig.

Flusso:
  1. Carica contesto email + config cliente dal DB
  2. Se intent incerto → riclassifica con Sonnet
  3. Genera bozza con Sonnet (tono e istruzioni del cliente)
  4. Salva bozza in draft_responses
  5. Restituisce DraftResponse per approvazione umana
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from anthropic import Anthropic
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from dotenv import load_dotenv
from database import get_client, get_conversation_history, save_draft, get_draft_by_id, update_draft_body
from client_config import ClientConfig, get_client_config

load_dotenv()

logger = logging.getLogger("polpo.responder")

# ─────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────

from models_config import RECLASSIFY_MODEL, RESPONDER_MODEL

RECLASSIFY_THRESHOLD = 0.7  # Se la confidence è più bassa, usa Sonnet per riclassificare

# ─────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────

@dataclass
class EmailContext:
    email_id:             str
    client_id:            str
    contact_name:         str
    contact_type:         str
    intent:               str
    priority:             int
    confidence:           float
    classified_by:        str
    email_body:           str
    email_subject:        str
    attachments:          list[str] = field(default_factory=list)
    attachments_text:     dict = field(default_factory=dict)
    estimated_value:      Optional[float] = None
    conversation_history: list[dict] = field(default_factory=list)

@dataclass
class DraftResponse:
    email_id:          str
    draft_id:          Optional[str]   # ID nel DB dopo il salvataggio
    contact_name:      str
    intent:            str
    priority:          int
    subject:           str
    body:              str
    suggested_actions: list[str]
    reclassified:      bool
    final_intent:      str
    warning:           Optional[str] = None


# ─────────────────────────────────────────────
# Step 1 — Carica contesto dal DB
# ─────────────────────────────────────────────

def load_email_context(email_id: str) -> Optional[EmailContext]:
    """Legge email + classificazione dalla view v_emails_classified."""
    db = get_client()

    result = (
        db.table("v_emails_classified")
        .select("*")
        .eq("id", email_id)
        .execute()
    )

    if not result.data:
        return None

    row = result.data[0]

    history = []
    if row.get("conversation_id"):
        history = get_conversation_history(row["conversation_id"], limit=3)

    # Leggi testo allegati estratti
    from attachment_reader import get_attachments_text, format_attachments_for_prompt
    attachments_text = get_attachments_text(email_id)

    return EmailContext(
        email_id=email_id,
        client_id=row["client_id"],
        contact_name=row.get("sender_name", "Cliente"),
        contact_type=row.get("contact_type_classified") or row.get("contact_type_db", "sconosciuto"),
        intent=row.get("intent", "altro"),
        priority=row.get("priority", 2),
        confidence=row.get("confidence", 0.0),
        classified_by=row.get("classified_by", "none"),
        email_body=row.get("body", ""),
        email_subject=row.get("subject", ""),
        attachments=row.get("attachments") or [],
        attachments_text=attachments_text,
        estimated_value=row.get("estimated_value"),
        conversation_history=history,
    )


# ─────────────────────────────────────────────
# Step 2 — Riclassifica con Sonnet
# ─────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError)),
    before_sleep=lambda retry_state: logger.warning(f"Retrying LLM reclassification... Attempt {retry_state.attempt_number}")
)
def reclassify_with_sonnet(
    ctx: EmailContext,
    config: ClientConfig,
    client: Anthropic,
) -> tuple[str, float, str]:
    """
    Usa Sonnet per riclassificare email con intent incerto.
    Usa la persona e la lista intent del cliente.
    Restituisce (intent, confidence, reasoning).
    """
    history_text = ""
    if ctx.conversation_history:
        history_text = "\n\nSTORICO CONVERSAZIONE:\n"
        for prev in reversed(ctx.conversation_history):
            history_text += f"- [{prev.get('received_at','')[:10]}] {prev.get('subject','')}: {prev.get('body','')[:200]}...\n"

    system = f"""{config.llm_persona}
Analizza l'email e determina l'intent con precisione.
Rispondi SOLO con JSON:
{{
  "intent": {config.all_intents_str()},
  "confidence": 0.0-1.0,
  "reasoning": "max 80 caratteri"
}}
Non aggiungere testo fuori dal JSON."""

    user_content = f"""
Da: {ctx.contact_name} ({ctx.contact_type})
Oggetto: {ctx.email_subject}
Allegati: {', '.join(ctx.attachments) if ctx.attachments else 'nessuno'}
{history_text}
---
{ctx.email_body[:2000]}
"""

    response = client.messages.create(
        model=RECLASSIFY_MODEL,
        max_tokens=300,
        timeout=30.0,
        system=system,
        messages=[{"role": "user", "content": user_content}]
    )

    try:
        raw_text = response.content[0].text.strip()
        logger.info("reclassify | raw response: %s", raw_text)
        
        # Estrazione robusta del JSON (gestisce markdown blocks ```json ... ```)
        import re
        json_match = re.search(r"\{.*?\}", raw_text, re.DOTALL)
        if json_match:
            json_text = json_match.group(0)
            data = json.loads(json_text)
        else:
            data = json.loads(raw_text)

        intent = data.get("intent", "altro")
        if intent not in config.intent_list:
            intent = "altro"
        result = intent, float(data.get("confidence", 0.5)), data.get("reasoning", "")
        logger.info("reclassify | intent=%s conf=%.2f", result[0], result[1])
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("reclassify | JSON parsing fallito: %s", e)
        return "altro", 0.3, "Parsing JSON fallito"
    except Exception as e:
        logger.error("reclassify | Errore inatteso: %s", e)
        return "altro", 0.0, "Errore inatteso"


# ─────────────────────────────────────────────
# Step 3 — Genera bozza con Sonnet
# ─────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError)),
    before_sleep=lambda retry_state: logger.warning(f"Retrying LLM draft generation... Attempt {retry_state.attempt_number}")
)
def generate_draft(
    ctx: EmailContext,
    config: ClientConfig,
    final_intent: str,
    client: Anthropic,
) -> tuple[str, str, list[str]]:
    from attachment_reader import format_attachments_for_prompt
    """
    Genera la bozza con Sonnet usando tono, firma e istruzioni del cliente.
    Restituisce (subject, body, suggested_actions).
    """
    instructions = config.get_intent_instruction(final_intent)

    history_text = ""
    if ctx.conversation_history:
        history_text = "\nSTORICO THREAD:\n"
        for prev in reversed(ctx.conversation_history):
            history_text += f"[{prev.get('received_at','')[:10]}] {prev.get('body','')[:300]}\n---\n"

    system = f"""{config.llm_persona}
Sei il segretario virtuale di {config.name}, azienda che opera nel settore: {config.sector}.
Scrivi bozze di email che verranno revisionate da un umano prima dell'invio.

REGOLA FONDAMENTALE (OUT-OF-SCOPE): 
Se la richiesta dell'utente è COMPLETAMENTE estranea al settore aziendale ({config.sector}) - ad esempio richieste per servizi web, marketing, o fornitura di beni non attinenti - DEVI informare cortesemente il mittente che l'azienda si occupa esclusivamente del proprio settore e non offre i servizi o prodotti richiesti. NON generare MAI un finto preventivo o una risposta per un servizio che l'azienda non offre.

Stile: {config.llm_tone}
Apertura: "Gentile [Nome],"
Chiusura: "{config.signature}"
Usa [PLACEHOLDER] per dati che l'operatore deve completare.
Non inventare prezzi, date o disponibilità reali.
Lunghezza: max 150 parole salvo necessità.

Rispondi SOLO con un oggetto JSON valido. Non aggiungere commenti o testo extra.
JSON Schema:
{{
  "subject": "oggetto risposta",
  "body": "corpo completo bozza",
  "suggested_actions": ["azione 1", "azione 2"],
  "attach_document": null
}}

Campo 'attach_document': se l'intent è 'preventivo' E hai informazioni sufficienti (voci, descrizioni, prezzi), popola questo campo con un oggetto 'preventivo'.
IMPORTANTE: Se il cliente fornisce dei prezzi nell'email, UTILIZZALI ma AUMENTALI del 10% ciascuno (arrotonda per eccesso). Se non ci sono prezzi, usa [PLACEHOLDER].

Schema attach_document:
{{
  "type": "preventivo",
  "format": "pdf",
  "data": {{
    "oggetto": "Preventivo — [descrizione]",
    "destinatario": {{"nome": "[Nome]", "email": "[email]", "riferimento": "[rif]"}},
    "introduzione": "[introduzione formale]",
    "voci": [
      {{"descrizione": "[voce]", "quantita": 1, "prezzo_unitario": 0.0}}
    ],
    "iva_percentuale": 22,
    "validita_giorni": 30,
    "note": "[note]",
    "condizioni": "[pagamento]"
  }}
}}
Se non hai dati per un preventivo, lascia 'attach_document' a null.
ASSICURATI che il JSON sia perfettamente formattato (usa doppie virgolette, evita virgole finali)."""

    user_content = f"""
EMAIL DA GESTIRE:
- Mittente: {ctx.contact_name} ({ctx.contact_type})
- Oggetto: {ctx.email_subject}
- Intent: {final_intent}
- Priorità: {ctx.priority} (1=bassa, 2=media, 3=urgente)
- Allegati: {', '.join(ctx.attachments) if ctx.attachments else 'nessuno'}
{history_text}
TESTO EMAIL:
{ctx.email_body[:2000]}
{format_attachments_for_prompt(ctx.attachments_text) if ctx.attachments_text else ''}
ISTRUZIONI PER QUESTO INTENT ({final_intent}):
{instructions}
"""

    response = client.messages.create(
        model=RESPONDER_MODEL,
        max_tokens=2000,
        timeout=60.0,
        system=system,
        messages=[{"role": "user", "content": user_content}]
    )

    raw_text = response.content[0].text.strip()
    # Pulizia avanzata del JSON
    import re
    
    # 1. Rimuove blocchi markdown ```json ... ```
    clean_json = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw_text, flags=re.DOTALL).strip()
    
    # 2. Se ancora inizia con ```, rimuovi solo l'inizio
    clean_json = re.sub(r"^```(?:json)?", "", clean_json).strip()
    clean_json = re.sub(r"```$", "", clean_json).strip()

    # 3. Tenta di trovare il primo { e l'ultimo } se c'è testo spurio fuori
    start_idx = clean_json.find('{')
    end_idx = clean_json.rfind('}')
    if start_idx != -1 and end_idx != -1:
        clean_json = clean_json[start_idx:end_idx+1]

    # 4. Rimuove trailing commas prima di chiusure di oggetti/liste (errore comune delle IA)
    clean_json = re.sub(r",\s*([\]}])", r"\1", clean_json)

    try:
        data = json.loads(clean_json)
        return (
            data.get("subject", f"Re: {ctx.email_subject}"),
            data.get("body", ""),
            data.get("suggested_actions", []),
            data.get("attach_document"),  # None oppure dict con type/format/data
        )
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("responder | Errore Parsing JSON: %s", e)
        logger.error("responder | RAW TEXT (first 3000 chars): %s", raw_text[:3000])
        logger.error("responder | CLEANED TEXT: %s", clean_json)
        return (
            f"Re: {ctx.email_subject}",
            "Errore generazione bozza. Rispondere manualmente.",
            ["Rispondere manualmente a questa email"],
            None,
        )


# ─────────────────────────────────────────────
# Pipeline Principale
# ─────────────────────────────────────────────

def generate_response_draft(
    email_id: str,
    client: Anthropic,
    reclassify_threshold: float = RECLASSIFY_THRESHOLD,
) -> Optional[DraftResponse]:
    """
    Pipeline completa Responder:
    1. Carica contesto + config cliente
    2. Riclassifica con Sonnet se confidence bassa
    3. Genera bozza personalizzata
    4. Salva in draft_responses
    5. Restituisce DraftResponse per approvazione
    """

    # ── Step 1: Contesto + Config ─────────────
    ctx = load_email_context(email_id)
    if not ctx:
        print(f"⚠️  Email {email_id} non trovata.")
        return None

    config = get_client_config(ctx.client_id)
    if not config:
        print(f"⚠️  Config cliente {ctx.client_id} non trovata.")
        return None

    # ── Step 2: Riclassifica se necessario ────
    reclassified = False
    final_intent = ctx.intent
    warning = None

    needs_reclassification = (
        ctx.intent == "altro"
        or ctx.classified_by == "llm_fallback"
        or ctx.confidence < reclassify_threshold
    )

    if needs_reclassification:
        logger.info("responder | email_id=%s 🔄 reclassifica Sonnet (intent=%s conf=%.0f%%)",
                    email_id[:8], ctx.intent, ctx.confidence * 100)
        new_intent, new_conf, reasoning = reclassify_with_sonnet(ctx, config, client)
        reclassified = True
        final_intent = new_intent

        if new_conf < reclassify_threshold:
            warning = (
                f"Intent incerto anche dopo Sonnet ({new_intent}, {new_conf:.0%}). "
                f"Verificare prima dell'invio. Nota: {reasoning}"
            )
            logger.warning("responder | email_id=%s intent ancora incerto: %s",
                           email_id[:8], warning)

    # ── Step 3: Bozza ────────────────────
    if final_intent == "spam":
        # Spam: non salvare bozza nel DB (evita che compaia in dashboard)
        return DraftResponse(
            email_id=email_id,
            draft_id=None,
            contact_name=ctx.contact_name,
            intent=ctx.intent,
            priority=ctx.priority,
            subject="[SPAM] Nessuna risposta necessaria",
            body="",
            suggested_actions=["Archiviare l'email"],
            reclassified=reclassified,
            final_intent="spam",
            warning=None,
        )

    subject, body, actions, attach_doc = generate_draft(ctx, config, final_intent, client)
    if final_intent == "reclamo":
        actions.insert(0, "⚠️ Reclamo: revisionare con attenzione prima dell'invio")

    # ── Step 4: Genera documento allegato se richiesto ──
    attachment_bytes: bytes | None = None
    attachment_filename: str | None = None

    if attach_doc and isinstance(attach_doc, dict):
        try:
            from document_generator import generate_document
            client_info = {
                "name":      config.name,
                "sector":    config.sector,
                "signature": config.signature,
            }
            doc_type   = attach_doc.get("type", "comunicazione")
            doc_format = attach_doc.get("format", "pdf")
            doc_data   = attach_doc.get("data", {})

            attachment_bytes, attachment_filename = generate_document(
                format=doc_format, doc_type=doc_type,
                data=doc_data, client_info=client_info,
            )
            logger.info("responder | email_id=%s documento generato: %s (%d bytes)",
                        email_id[:8], attachment_filename, len(attachment_bytes))
        except Exception as e:
            logger.error("responder | email_id=%s errore generazione documento: %s", email_id[:8], e)
            attachment_bytes = None
            attachment_filename = None

    # ── Step 5: Salva bozza nel DB ────────────────────
    draft_status = "pending"
    approved_by = None
    if ctx.priority == 1:
        draft_status = "approved"
        approved_by = "system_auto"

    draft_id = None
    try:
        from database import get_client as _get_db
        import base64
        draft_payload: dict = {
            "email_id":          email_id,
            "client_id":         ctx.client_id,
            "subject":           subject,
            "body":              body,
            "suggested_actions": actions,
            "final_intent":      final_intent,
            "reclassified":      reclassified,
            "warning":           warning,
            "status":            draft_status,
            "approved_by":       approved_by,
        }
        if attachment_filename:
            draft_payload["attachment_filename"] = attachment_filename
        if attachment_bytes:
            # Supabase non supporta bytea via REST — salviamo base64 nel campo text
            # Al momento dell'invio lo decodifichiamo
            draft_payload["attachment_data"] = base64.b64encode(attachment_bytes).decode()

        draft_record = save_draft(
            email_id=email_id,
            client_id=ctx.client_id,
            subject=subject,
            body=body,
            suggested_actions=actions,
            final_intent=final_intent,
            reclassified=reclassified,
            warning=warning,
            status=draft_status,
            approved_by=approved_by,
        )
        draft_id = draft_record["id"]

        # Salva attachment separatamente se presente
        if attachment_filename and attachment_bytes:
            _get_db().table("draft_responses").update({
                "attachment_filename": attachment_filename,
                "attachment_data":     base64.b64encode(attachment_bytes).decode(),
            }).eq("id", draft_id).execute()

        logger.info("responder | email_id=%s draft_id=%s salvato — intent=%s%s",
                    email_id[:8], draft_id[:8], final_intent,
                    f" + {attachment_filename}" if attachment_filename else "")
    except Exception as e:
        logger.error("responder | email_id=%s errore salvataggio bozza: %s", email_id[:8], e)

    return DraftResponse(
        email_id=email_id,
        draft_id=draft_id,
        contact_name=ctx.contact_name,
        intent=ctx.intent,
        priority=ctx.priority,
        subject=subject,
        body=body,
        suggested_actions=actions,
        reclassified=reclassified,
        final_intent=final_intent,
        warning=warning,
    )


# ─────────────────────────────────────────────
# Modifica bozza via Telegram (stateless)
# ─────────────────────────────────────────────

def refine_draft(
    draft_id: str,
    instruction: str,
    client: Anthropic,
) -> Optional[tuple[str, str, str]]:
    """
    Modifica una bozza esistente tramite istruzione in linguaggio naturale.
    Usato dal bot Telegram dopo click su "Modifica".

    Stateless: recupera bozza e config dal DB, chiama Sonnet, aggiorna il record.
    Restituisce (nuovo_subject, nuovo_body, feedback_ia) o None in caso di errore.
    """
    draft = get_draft_by_id(draft_id)
    if not draft:
        logger.warning("refine_draft | draft_id=%s non trovato", draft_id[:8])
        return None

    client_data = draft.get("clients", {})
    tone     = client_data.get("llm_tone", "professionale e cordiale")
    signature = client_data.get("signature", "Cordiali saluti")
    name     = client_data.get("name", "azienda")

    system = (
        f"Sei il segretario virtuale di {name}. "
        f"Stile: {tone}. Firma: {signature}.\n"
        "Ti viene fornita una bozza di email e un'istruzione di modifica. "
        "Applica la modifica e rispondi SOLO con JSON:\n"
        '{\n'
        '  "subject": "oggetto",\n'
        '  "body": "corpo completo",\n'
        '  "feedback": "breve frase di conferma di ciò che hai fatto (es: Ho aggiunto lo sconto richiesto)"\n'
        '}\n'
        "Non aggiungere testo fuori dal JSON."
    )

    user_content = (
        f"BOZZA ATTUALE:\nOggetto: {draft.get('subject', '')}\n\n{draft.get('body', '')}\n\n"
        f"ISTRUZIONE DI MODIFICA: {instruction}"
    )

    try:
        response = client.messages.create(
            model=RESPONDER_MODEL,
            max_tokens=1000,
            timeout=60.0,
            system=system,
            messages=[{"role": "user", "content": user_content}]
        )
        import re as _re
        raw = response.content[0].text.strip()
        
        # Estrazione robusta JSON
        json_match = _re.search(r"\{.*?\}", raw, _re.DOTALL)
        if json_match:
            data = json.loads(json_match.group(0))
        else:
            data = json.loads(raw)
            
        new_subject = data.get("subject", draft.get("subject", ""))
        new_body    = data.get("body", draft.get("body", ""))
        feedback    = data.get("feedback", "Bozza aggiornata con successo.")

        update_draft_body(draft_id, new_subject, new_body)
        logger.info("refine_draft | draft_id=%s bozza aggiornata. Feedback: %s", draft_id[:8], feedback)

        # Restituisce anche la draft aggiornata per evitare una seconda query nel bot
        updated_draft = {**draft, "subject": new_subject, "body": new_body}
        return new_subject, new_body, feedback, updated_draft
    except Exception as e:
        logger.error("refine_draft | draft_id=%s errore: %s", draft_id[:8], e)
        return None


# ─────────────────────────────────────────────
# Test rapido
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from anthropic import Anthropic

    anthropic_client = Anthropic()

    # Incolla qui l'email_id completo che hai visto nel test precedente
    TEST_EMAIL_ID = "6f12f150-8a30-4445-9693-80baa4a2cd73"

    draft = generate_response_draft(TEST_EMAIL_ID, anthropic_client)

    if draft:
        print(f"\n{'='*60}")
        print(f"BOZZA — {draft.contact_name}")
        print(f"  Intent originale : {draft.intent}")
        print(f"  Intent finale    : {draft.final_intent}")
        print(f"  Priorità         : {draft.priority}")
        print(f"  Riclassificato   : {'Sì (Sonnet)' if draft.reclassified else 'No'}")
        print(f"  Draft ID         : {draft.draft_id}")
        if draft.warning:
            print(f"\n  ⚠️  {draft.warning}")
        print(f"\n  Oggetto : {draft.subject}")
        print(f"\n  Bozza:\n{draft.body}")
        print(f"\n  Azioni:")
        for a in draft.suggested_actions:
            print(f"    • {a}")
