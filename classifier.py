"""
[AI REFERENCE] Per una visione d'insieme dell'architettura e del flusso logico,
leggere il file: PROJECT_SUMMARY.md
"""

"""
Email Classifier — Pipeline LLM-First (v6 Multi-Tenant)

Architettura:

  Livello 1 — Filtro triviale (zero token, zero LLM)
    Casi banali che non richiedono ragionamento:
    - Mittenti automatici / noreply
    - Messaggi di cortesia brevi senza contenuto azionabile

  Livello 2 — DB Lookup
    Se il mittente è in rubrica, il contact_type è già noto.
    Viene passato come hint all'LLM ma non bypassa la classificazione.

  Livello 3 — Haiku con contesto completo
    Classifica SEMPRE con LLM. Niente regole keyword.
    Contesto passato:
    - Corpo email pulito
    - Thread completo (quoted_text) — sempre incluso se presente
    - Nomi allegati (il testo estratto lo legge Sonnet nel responder)
    - Business context del cliente (settore, servizi, out-of-scope)
    - Lingua rilevata dal worker
    - contact_type hint se mittente noto

"""

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from pydantic import BaseModel, field_validator, ValidationError
from anthropic import Anthropic
import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from dotenv import load_dotenv

from database import persist_classified_email, get_contact_by_email
from client_config import ClientConfig, get_client_config
from attachment_reader import get_quoted_text_from_db
from constants import ClassifiedBy

load_dotenv()
logger = logging.getLogger("polpo.classifier")


# ─────────────────────────────────────────────
# Schema Pydantic per validazione output LLM
# ─────────────────────────────────────────────

class _ClassifyPayload(BaseModel):
    intent: str = "altro"
    priority: int = 2
    confidence: float = 0.5
    summary: str = ""
    estimated_value: Optional[float] = None

    @field_validator("priority")
    @classmethod
    def clamp_priority(cls, v: int) -> int:
        return max(0, min(3, v))

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


# ─────────────────────────────────────────────
# Livello 1 — Filtro triviale (zero token)
# ─────────────────────────────────────────────

NOREPLY_PATTERNS = [
    r"no.?reply", r"do.?not.?reply", r"noreply",
    r"mailer.daemon", r"postmaster",
    r"notifications?@", r"alerts?@",
    r"auto.?reply", r"automated?@",
]

# Solo parole di cortesia pura — usate con soglia caratteri molto bassa
CORTESIA_PATTERN = r"\b(grazie\s*mille|grazie|perfetto|ricevuto|ok\s+grazie|ottimo|capito|va\s+bene|thank\s+you|thanks)\b"

# Soglia massima caratteri per filtro cortesia
# Sotto questa soglia + parola cortesia + nessun "?" = sicuramente non azionabile
CORTESIA_MAX_CHARS = 35


def _is_trivial(
    sender_email: str,
    subject: str,
    body: str,
    has_thread: bool = False,
) -> Optional["ClassificationResult"]:
    """
    Filtro conservativo — filtra solo i casi in cui siamo al 100% sicuri.

    Bounce/noreply: sempre filtrati, anche con thread.
    Cortesia: filtrata SOLO se:
      - corpo sotto i 35 caratteri
      - contiene parola di cortesia
      - nessun punto interrogativo (domanda = azionabile)
      - NON c'è un thread (con thread passa sempre a Haiku)
    """
    sender = sender_email.lower()

    # Bounce e mittenti automatici — sempre filtrati
    if any(re.search(p, sender) for p in NOREPLY_PATTERNS):
        return ClassificationResult(
            contact_type="sconosciuto", intent="cortesia", priority=0,
            confidence=0.99, classified_by=ClassifiedBy.FILTER,
            summary="Mittente automatico o noreply — nessuna risposta necessaria.",
        )

    # Con thread → passa sempre a Haiku, qualunque cosa dica il corpo
    if has_thread:
        return None

    # Cortesia pura: corpo cortissimo, nessuna domanda, nessun thread
    body_clean = body.strip()
    text       = (subject + " " + body_clean).lower()
    if (len(body_clean) <= CORTESIA_MAX_CHARS
            and re.search(CORTESIA_PATTERN, text, re.IGNORECASE)
            and "?" not in body_clean):
        return ClassificationResult(
            contact_type="sconosciuto", intent="cortesia", priority=0,
            confidence=0.95, classified_by=ClassifiedBy.FILTER,
            summary="Messaggio di cortesia non azionabile — nessuna risposta necessaria.",
        )

    return None


# ─────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────

@dataclass
class InboundMessage:
    sender_email:      str
    sender_name:       str
    subject:           str
    body:              str
    attachments:       list[str] = field(default_factory=list)
    detected_language: str = "unknown"
    in_reply_to:       str = ""    # header RFC822 per threading corretto

@dataclass
class ClassificationResult:
    contact_type:    str
    intent:          str            # intent dell'email CORRENTE
    priority:        int            # 0=no reply, 1=bassa, 2=media, 3=urgente
    confidence:      float
    classified_by:   str            # ClassifiedBy.FILTER | LLM | LLM_FALLBACK | NONE
    summary:         str            # descrive l'intent corrente, non il thread
    estimated_value: Optional[float] = None
    thread_topic:    str = ""
    db_ids:          Optional[dict] = None


# ─────────────────────────────────────────────
# Livello 2 — DB Lookup (hint per LLM)
# ─────────────────────────────────────────────

def lookup_contact(client_id: str, email: str, use_real_db: bool = True) -> Optional[dict]:
    if use_real_db:
        return get_contact_by_email(client_id, email)
    mock = {
        "mario.rossi@example.com": {"contact_type": "cliente",   "name": "Mario Rossi"},
        "ricambi@fornitore.it":    {"contact_type": "fornitore", "name": "Ricambi SpA"},
        "noreply@promo.com":       {"contact_type": "spam",      "name": "Promo"},
    }
    return mock.get(email.lower())


# ─────────────────────────────────────────────
# Livello 3 — Haiku LLM-First
# ─────────────────────────────────────────────

def _build_system_prompt(
    config: ClientConfig,
    existing_contact_types: list = None,
    detected_language: str = "unknown",
) -> str:

    language_note = ""
    if detected_language and detected_language not in ("unknown", "it"):
        language_note = f"\nNOTA LINGUA: L'email è scritta in '{detected_language}'."

    business_context = config.format_business_context()
    business_block   = f"\n\n{business_context}" if business_context else ""

    return f"""{config.llm_persona}{business_block}

Analizza l'email e rispondi SOLO con JSON valido:
{{
  "intent": {config.all_intents_str()} | "spam",
  "priority": 0 | 1 | 2 | 3,
  "confidence": 0.0-1.0,
  "summary": "max 100 caratteri — descrivi cosa chiede il mittente IN QUESTA EMAIL",
  "estimated_value": null oppure float se preventivo con valore stimabile
}}


PRIORITÀ:
- 0: spam, pubblicità, phishing, automatiche, cortesia pura, OUT-OF-SCOPE. Se 0 → intent="spam"
- 1: informazioni generiche non urgenti
- 2: preventivi, appuntamenti, pagamenti
- 3: reclami gravi, urgenze operative
{language_note}
Non aggiungere testo fuori dal JSON."""


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError)),
    before_sleep=lambda rs: logger.warning("Retry classificazione LLM... tentativo %d", rs.attempt_number)
)
def classify_with_llm(
    msg: InboundMessage,
    config: ClientConfig,
    client: Anthropic,
    quoted_text: str = "",
    contact_type_hint: Optional[str] = None,
) -> ClassificationResult:
    """
    Classificazione completa con Haiku.
    Passa sempre il thread completo se presente — niente ambiguità di contesto.
    """
    from database import get_existing_contact_types
    from models_config import CLASSIFIER_MODEL

    system_prompt = _build_system_prompt(config, None, msg.detected_language)

    att_names     = ", ".join(msg.attachments) if msg.attachments else "nessuno"
    thread_block  = (
        f"\n\n--- STORICO THREAD ---\n{quoted_text}\n--- FINE THREAD ---"
        if quoted_text else ""
    )
    contact_block = (
        f"\nMittente in rubrica come: {contact_type_hint}"
        if contact_type_hint else ""
    )

    user_content = f"""Da: {msg.sender_name} <{msg.sender_email}>
Oggetto: {msg.subject}
Allegati: {att_names}{contact_block}

--- EMAIL CORRENTE ---
{msg.body[:2000]}{thread_block}
"""

    response = client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=400,
        timeout=30.0,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}]
    )

    raw = response.content[0].text.strip()

    try:
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        data       = json.loads(json_match.group(0) if json_match else raw)

        payload = _ClassifyPayload.model_validate(data)

        intent = payload.intent
        if intent not in config.intent_list and intent != "spam":
            intent = "altro"

        result = ClassificationResult(
            contact_type="sconosciuto",
            intent=intent,
            priority=payload.priority,
            confidence=payload.confidence,
            classified_by=ClassifiedBy.LLM,
            summary=payload.summary,
            estimated_value=payload.estimated_value,
        )
        logger.info("llm_classify | intent=%s conf=%.2f", result.intent, result.confidence)
        return result

    except (json.JSONDecodeError, ValueError, ValidationError) as e:
        logger.warning("llm_classify | parsing fallito: %s | raw=%s", e, raw[:100])
        return ClassificationResult(
            contact_type="sconosciuto", intent="altro", priority=2,
            confidence=0.3, classified_by=ClassifiedBy.LLM_FALLBACK,
            summary="Classificazione LLM fallita, richiede revisione manuale."
            )
    except Exception as e:
        logger.error("llm_classify | Errore inatteso: %s", e)
        return ClassificationResult(
            contact_type="sconosciuto", intent="altro", priority=2,
            confidence=0.0, classified_by=ClassifiedBy.LLM_FALLBACK,
            summary="Errore LLM, richiede revisione manuale."
            )


# ─────────────────────────────────────────────
# Pipeline Principale
# ─────────────────────────────────────────────

def classify_message(
    msg: InboundMessage,
    client_id: str,
    llm_client: Anthropic = None,
    config: Optional[ClientConfig] = None,
    save_to_db: bool = True,
    use_real_db: bool = True,
    quoted_text: str = "",      # passato dal worker dopo lo strip del thread
) -> ClassificationResult:
    """
    Pipeline classificazione LLM-First.

    1. Filtro triviale (bounce, cortesia) — zero token
    2. DB Lookup — recupera contact_type se mittente noto (hint per LLM)
    3. Haiku con contesto completo (corpo + thread + allegati + business context)
    4. Salva su Supabase
    """

    if not config:
        config = get_client_config(client_id) if use_real_db else _mock_config(client_id)
    if not config:
        raise ValueError(f"Cliente {client_id} non trovato o non attivo.")

    # Livello 1 — filtro triviale
    trivial = _is_trivial(msg.sender_email, msg.subject, msg.body, has_thread=bool(quoted_text))
    if trivial:
        logger.info("classify | client=%s email=%s level=filter intent=%s",
                    client_id[:8], msg.sender_email, trivial.intent)
        result = trivial

    else:
        # Livello 2 — DB lookup (hint)
        contact           = lookup_contact(client_id, msg.sender_email, use_real_db)
        contact_type_hint = contact.get("contact_type") if contact else None

        # Livello 3 — Haiku
        if llm_client:
            logger.info("classify | client=%s email=%s level=llm has_thread=%s",
                        client_id[:8], msg.sender_email, bool(quoted_text))

            result = classify_with_llm(
                msg=msg,
                config=config,
                client=llm_client,
                quoted_text=quoted_text,
                contact_type_hint=contact_type_hint,
            )

            # Il contact_type dal DB ha sempre precedenza su quello inferito dall'LLM
            if contact_type_hint:
                result.contact_type = contact_type_hint

        else:
            logger.warning("classify | client=%s email=%s — nessun LLM disponibile",
                           client_id[:8], msg.sender_email)
            result = ClassificationResult(
                contact_type="sconosciuto", intent="altro", priority=2,
                confidence=0.0, classified_by=ClassifiedBy.NONE,
                summary="Classificazione non riuscita. Richiede revisione manuale."
            )

    # Salvataggio DB
    if save_to_db and use_real_db:
        try:
            db_ids = persist_classified_email(
                client_id=client_id,
                sender_email=msg.sender_email,
                sender_name=msg.sender_name,
                subject=msg.subject,
                body=msg.body,
                attachments=msg.attachments,
                contact_type=result.contact_type,
                intent=result.intent,
                priority=result.priority,
                confidence=result.confidence,
                classified_by=result.classified_by,
                summary=result.summary,
                estimated_value=result.estimated_value,
                in_reply_to=msg.in_reply_to,
            )
            result.db_ids = db_ids
            logger.info("classify | DB — email_id=%s intent=%s priority=%d",
                        db_ids["email_id"][:8], result.intent, result.priority)
        except Exception as e:
            logger.error("classify | Errore salvataggio DB: %s", e)

    return result


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _mock_config(client_id: str) -> ClientConfig:
    return ClientConfig(
        client_id=client_id,
        name="Officina Test",
        sector="automotive",
        language="Italiano",
        llm_persona="Sei il classificatore email di un'officina italiana.",
        llm_tone="professionale e cordiale",
        signature="Cordiali saluti,\nOfficina Test",
        intent_list=["preventivo","appuntamento","informazione","reclamo","pagamento","spam","altro"],
        priority_map={},
        intent_keywords={},
        custom_spam_keywords=[],
        intent_instructions={},
        contacts={},
        orari={},
        faq=[],
    )


# ─────────────────────────────────────────────
# Test rapido
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from anthropic import Anthropic
    from client_config import get_client_id_by_name

    anthropic_client = Anthropic()
    CLIENT_ID        = get_client_id_by_name("Officina Demo")
    if not CLIENT_ID:
        print("❌ Cliente 'Officina Demo' non trovato.")
        exit(1)

    print(f"✅ Client ID trovato: {CLIENT_ID[:8]}…")

    msgs = [
        # Thread in corso: oggetto con Re:, body breve ambiguo
        InboundMessage(
            sender_email="mario.rossi@example.com",
            sender_name="Mario Rossi",
            subject="Re: Preventivo riparazione paraurti",
            body="Perfetto, confermo. A che ora posso venire?",
        ),
        # Nuova email multi-intent
        InboundMessage(
            sender_email="anna.verdi@gmail.com",
            sender_name="Anna Verdi",
            subject="Preventivo e sede",
            body="Buongiorno, vorrei un preventivo per la sostituzione del paraurti. Inoltre mi può indicare la vostra sede e gli orari?",
        ),
        # Reclamo diretto
        InboundMessage(
            sender_email="luca.bianchi@gmail.com",
            sender_name="Luca Bianchi",
            subject="Problema irrisolto",
            body="Salve, ho fatto riparare la mia auto da voi la settimana scorsa e il problema persiste. Sono molto deluso.",
        ),
    ]

    quoted_texts = [
        "Il giorno 3 marzo, Officina Demo ha scritto:\n> Le inviamo il preventivo: sostituzione paraurti €350 + manodopera €80.",
        "",
        "",
    ]

    for i, (msg, qt) in enumerate(zip(msgs, quoted_texts), 1):
        result = classify_message(
            msg, client_id=CLIENT_ID,
            llm_client=anthropic_client,
            use_real_db=True, save_to_db=False,
            quoted_text=qt,
        )
        print(f"\n{'='*55}")
        print(f"TEST {i}: {msg.subject}")
        print(f"  Intent       : {result.intent}")
        print(f"  Priorità     : {result.priority}")
        print(f"  Confidence   : {result.confidence:.0%}")
        print(f"  By           : {result.classified_by}")
        print(f"  Summary      : {result.summary}")
