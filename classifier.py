"""
[AI REFERENCE] Per una visione d'insieme dell'architettura e del flusso logico, 
leggere il file: PROJECT_SUMMARY.md
"""

"""
Email Classifier - Cascade Pipeline (v3 Multi-Tenant)

Livello 1: Lookup DB (mittente noto per questo cliente)
Livello 2: Regole deterministiche (keyword spam + intent patterns)
Livello 3: LLM Haiku (fallback)

La configurazione (persona, intenti, keyword spam) viene letta da ClientConfig
quindi il bot si adatta automaticamente al settore del cliente.
"""

import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional
from anthropic import Anthropic

from database import persist_classified_email, get_contact_by_email
from client_config import ClientConfig, get_client_config

logger = logging.getLogger("polpo.classifier")

# ─────────────────────────────────────────────
# Costanti globali (non dipendono dal cliente)
# ─────────────────────────────────────────────

BASE_SPAM_KEYWORDS = [
    "unsubscribe", "offerta esclusiva", "hai vinto", "clicca qui",
    "guadagna da casa", "verifica il tuo account", "noreply@",
]

from dotenv import load_dotenv

load_dotenv()

# Pattern intent di base — vengono usati solo come fallback
# se il cliente non ha intent personalizzati
BASE_INTENT_PATTERNS = {
    "preventivo":    r"\b(preventivo|stima|quanto costa|costo|quotazione|offerta)\b",
    "appuntamento":  r"\b(appuntamento|prenotare|disponibilit|quando posso|orario)\b",
    "reclamo":       r"\b(reclamo|lamentela|problema|non funziona|deluso|insoddisfatto)\b",
    "pagamento":     r"\b(pagamento|fattura|bonifico|ricevuta|saldo)\b",
    "informazione":  r"\b(informazioni|info|chiedo|volevo sapere|domanda)\b",
}

PRIORITY_MAP = {
    "reclamo":      3,
    "urgenza":      3,   # intent custom per es. dentisti
    "preventivo":   2,
    "appuntamento": 2,
    "pagamento":    2,
    "informazione": 1,
    "info":         1,
    "spam":         0,   # spam, marketing, phishing → non rispondere
    "altro":        1,
    "cortesia":     0,   # ringraziamenti, conferme, no-reply → non rispondere
}

# Pattern mittenti automatici/noreply — controllati prima di tutto
NOREPLY_PATTERNS = [
    r"no.?reply", r"do.?not.?reply", r"noreply",
    r"mailer.daemon", r"postmaster",
    r"notifications?@", r"alerts?@",
    r"auto.?reply", r"automated?@",
]

# Pattern per messaggi di cortesia non azionabili
CORTESIA_PATTERN = r"\b(grazie\s*mille|grazie|perfetto|ricevuto|ok\s+grazie|ottimo|capito|va\s+bene|thank\s+you|thanks)\b"


# ─────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────

@dataclass
class InboundMessage:
    sender_email: str
    sender_name:  str
    subject:      str
    body:         str
    attachments:  list[str] = field(default_factory=list)

@dataclass
class ClassificationResult:
    contact_type:    str
    intent:          str
    priority:        int            # 0=non rispondere, 1=bassa, 2=media, 3=urgente
    confidence:      float
    classified_by:   str            # "db_lookup"|"rules"|"llm"|"llm_fallback"
    summary:         str
    estimated_value: Optional[float] = None
    db_ids:          Optional[dict] = None


# ─────────────────────────────────────────────
# Livello 1 — DB Lookup
# ─────────────────────────────────────────────

def lookup_contact(client_id: str, email: str, use_real_db: bool = True) -> Optional[dict]:
    """Cerca mittente nel DB del cliente specifico."""
    if use_real_db:
        return get_contact_by_email(client_id, email)

    # Mock offline per test
    mock = {
        "mario.rossi@example.com": {"contact_type": "cliente", "name": "Mario Rossi"},
        "ricambi@fornitore.it":    {"contact_type": "fornitore", "name": "Ricambi SpA"},
        "noreply@promo.com":       {"contact_type": "spam", "name": "Promo"},
    }
    return mock.get(email.lower())


# ─────────────────────────────────────────────
# Livello 2 — Regole Deterministiche
# ─────────────────────────────────────────────

def apply_rules(msg: InboundMessage, config: ClientConfig) -> Optional[ClassificationResult]:
    """
    Applica regole basate su keyword e pattern.
    Usa la configurazione del cliente (spam keywords extra, intent list).
    """
    text = (msg.subject + " " + msg.body).lower()
    sender = msg.sender_email.lower()

    # Priorità 0 — Mittenti automatici/noreply: non rispondere
    if any(re.search(p, sender) for p in NOREPLY_PATTERNS):
        return ClassificationResult(
            contact_type="automatico",
            intent="cortesia",
            priority=0,
            confidence=0.99,
            classified_by="rules",
            summary="Mittente automatico o noreply — nessuna risposta necessaria."
        )

    # Priorità 0 — Messaggi di cortesia non azionabili
    body_short = msg.body.strip()
    if len(body_short) < 120 and re.search(CORTESIA_PATTERN, text, re.IGNORECASE):
        return ClassificationResult(
            contact_type="cliente",
            intent="cortesia",
            priority=0,
            confidence=0.90,
            classified_by="rules",
            summary="Messaggio di cortesia non azionabile — nessuna risposta necessaria."
        )

    # Spam: keywords base + quelle custom del cliente
    all_spam_kw = BASE_SPAM_KEYWORDS + config.custom_spam_keywords
    if any(kw in text for kw in all_spam_kw):
        return ClassificationResult(
            contact_type="spam",
            intent="spam",
            priority=1,
            confidence=0.95,
            classified_by="rules",
            summary="Messaggio spam rilevato da keyword matching."
        )

    # Intent detection: 1. Custom keywords del cliente (Priorità)
    if config.intent_keywords:
        for intent, keywords in config.intent_keywords.items():
            if intent not in config.intent_list:
                continue
            for kw in keywords:
                if kw.lower() in text:
                    return ClassificationResult(
                        contact_type="sconosciuto",
                        intent=intent,
                        priority=config.priority_map.get(intent, PRIORITY_MAP.get(intent, 1)),
                        confidence=0.90,  # Alta confidence per match esatto impostato dal cliente
                        classified_by="rules",
                        summary=f"Intent rilevato via keyword personalizzata '{kw}': {intent}."
                    )

    # Intent detection: 2. Pattern base (Fallback)
    for intent, pattern in BASE_INTENT_PATTERNS.items():
        # Salta intent non presenti nella lista del cliente
        if intent not in config.intent_list:
            continue
        if re.search(pattern, text, re.IGNORECASE):
            return ClassificationResult(
                contact_type="sconosciuto",
                intent=intent,
                priority=config.priority_map.get(intent, PRIORITY_MAP.get(intent, 1)),
                confidence=0.75,
                classified_by="rules",
                summary=f"Intent rilevato via pattern base: {intent}. Mittente non in rubrica."
            )

    return None  # → passa al LLM


# ─────────────────────────────────────────────
# Livello 3 — LLM Haiku (con config cliente)
# ─────────────────────────────────────────────

def _build_llm_prompt(config: ClientConfig) -> str:
    """Costruisce il system prompt dinamicamente dalla config del cliente."""
    return f"""{config.llm_persona}
Analizza il messaggio email e rispondi SOLO con un JSON valido:
{{
  "contact_type": "cliente" | "fornitore" | "spam" | "personale" | "sconosciuto",
  "intent": {config.all_intents_str()} | "spam",
  "priority": 0 | 1 | 2 | 3,
  "confidence": 0.0-1.0,
  "summary": "max 100 caratteri",
  "estimated_value": null oppure float se preventivo con valore stimabile
}}

REGOLE DI PRIORITA':
- 0 (ZERO ASSOLUTO): usa TASSATIVAMENTE per spam, pubblicità non richiesta, phishing, email automatiche, ringraziamenti e SOPRATTUTTO per richieste fuori settore (Out of Scope, es. richieste di servizi web per un'azienda di trasporti). Se priorità è 0 per posta indesiderata/OOS imposta "intent": "spam".
- 1: bassa (info generiche non urgenti pertinenti al settore)
- 2: media (preventivi, appuntamenti, pagamenti reali pertinenti)
- 3: urgente (reclami gravi, urgenze operative in target)

Non aggiungere testo fuori dal JSON."""

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import anthropic

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((anthropic.APIError, anthropic.APIConnectionError, anthropic.RateLimitError)),
    before_sleep=lambda retry_state: logger.warning(f"Retrying LLM classification... Attempt {retry_state.attempt_number}")
)
def classify_with_llm(
    msg: InboundMessage,
    config: ClientConfig,
    client: Anthropic,
) -> ClassificationResult:
    """Classificazione via Haiku — solo quando regole e DB non bastano."""
    user_content = f"""
Da: {msg.sender_name} <{msg.sender_email}>
Oggetto: {msg.subject}
Allegati: {', '.join(msg.attachments) if msg.attachments else 'nessuno'}

---
{msg.body[:1500]}
"""
    from models_config import CLASSIFIER_MODEL
    response = client.messages.create(
        model=CLASSIFIER_MODEL,
        max_tokens=300,
        timeout=30.0,
        system=_build_llm_prompt(config),
        messages=[{"role": "user", "content": user_content}]
    )

    raw = response.content[0].text.strip()

    try:
        # Estrazione robusta del JSON (gestisce markdown blocks ```json ... ```)
        import re
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if json_match:
            json_text = json_match.group(0)
            data = json.loads(json_text)
        else:
            data = json.loads(raw)

        intent = data.get("intent", "altro")
        if intent not in config.intent_list:
            intent = "altro"
        result = ClassificationResult(
            contact_type=data.get("contact_type", "sconosciuto"),
            intent=intent,
            priority=int(data.get("priority", 2)),
            confidence=float(data.get("confidence", 0.5)),
            classified_by="llm",
            summary=data.get("summary", ""),
            estimated_value=data.get("estimated_value"),
        )
        logger.info("llm_classify | intent=%s conf=%.2f", result.intent, result.confidence)
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("llm_classify | JSON parsing fallito: %s | raw=%s", e, raw[:100])
        return ClassificationResult(
            contact_type="sconosciuto",
            intent="altro",
            priority=2,
            confidence=0.3,
            classified_by="llm_fallback",
            summary="Classificazione LLM fallita, richiede revisione manuale."
        )
    except Exception as e:
        logger.error("llm_classify | Errore inatteso: %s", e)
        return ClassificationResult(
            contact_type="sconosciuto",
            intent="altro",
            priority=2,
            confidence=0.0,
            classified_by="llm_fallback",
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
    llm_confidence_threshold: float = 0.70,
    save_to_db: bool = True,
    use_real_db: bool = True,
) -> ClassificationResult:
    """
    Pipeline di classificazione a cascata per un cliente specifico.

    1. Carica config cliente (persona, intenti, spam keywords)
    2. Lookup DB contatti del cliente
    3. Regole deterministiche personalizzate
    4. LLM Haiku con persona del cliente
    5. Salva tutto su Supabase
    """

    # ── Config cliente ────────────────────────
    if not config:
        config = get_client_config(client_id) if use_real_db else _mock_config(client_id)
    if not config:
        raise ValueError(f"Cliente {client_id} non trovato o non attivo.")

    # ── Livello 1: DB Lookup ──────────────────
    contact = lookup_contact(client_id, msg.sender_email, use_real_db)
    if contact:
        contact_type = contact.get("contact_type", "sconosciuto")
        rule_result = apply_rules(msg, config)
        intent = rule_result.intent if rule_result else "altro"
        logger.info("classify | client=%s email=%s level=db_lookup intent=%s",
                    client_id[:8], msg.sender_email, intent)
        result = ClassificationResult(
            contact_type=contact_type,
            intent=intent,
            priority=_compute_priority(contact_type, intent, config),
            confidence=0.95,
            classified_by="db_lookup",
            summary=f"{contact.get('name', msg.sender_name)} ({contact_type}): {intent}"
        )

    else:
        # ── Livello 2: Regole ────────────────
        rule_result = apply_rules(msg, config)
        if rule_result and rule_result.confidence >= llm_confidence_threshold:
            logger.info("classify | client=%s email=%s level=rules intent=%s",
                        client_id[:8], msg.sender_email, rule_result.intent)
            result = rule_result

        # ── Livello 3: LLM Haiku ─────────────────
        elif llm_client:
            logger.info("classify | client=%s email=%s level=llm_haiku",
                        client_id[:8], msg.sender_email)
            result = classify_with_llm(msg, config, llm_client)

        else:
            logger.warning("classify | client=%s email=%s level=none — nessun classificatore disponibile",
                           client_id[:8], msg.sender_email)
            result = ClassificationResult(
                contact_type="sconosciuto",
                intent="altro",
                priority=2,
                confidence=0.0,
                classified_by="none",
                summary="Classificazione non riuscita. Richiede revisione manuale."
            )

    # ── Livello 4: Salvataggio DB ──────────────────
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
            )
            result.db_ids = db_ids
            logger.info("classify | salvato su DB — email_id=%s intent=%s priority=%d",
                        db_ids["email_id"][:8], result.intent, result.priority)
        except Exception as e:
            logger.error("classify | Errore salvataggio DB: %s", e)

    return result


def _compute_priority(contact_type: str, intent: str, config: "ClientConfig" = None) -> int:
    """Priorità da combinazione contatto + intent.
    Usa prima il priority_map del cliente (se disponibile), poi il default globale."""
    client_map = config.priority_map if config else {}
    if intent in ("reclamo", "urgenza"):
        return client_map.get(intent, PRIORITY_MAP.get(intent, 3))
    if contact_type == "cliente" and intent == "preventivo":
        return client_map.get(intent, PRIORITY_MAP.get(intent, 2))
    if contact_type == "fornitore":
        return client_map.get(intent, PRIORITY_MAP.get(intent, 1))
    return client_map.get(intent, PRIORITY_MAP.get(intent, 1))


def _mock_config(client_id: str) -> ClientConfig:
    """Config mock per test offline."""
    from client_config import ClientConfig
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

    # Prende il client_id del cliente demo dal DB reale
    CLIENT_ID = get_client_id_by_name("Officina Demo")
    if not CLIENT_ID:
        print("❌ Cliente 'Officina Demo' non trovato. Hai eseguito schema.sql?")
        exit(1)

    print(f"✅ Client ID trovato: {CLIENT_ID[:8]}…")

    msgs = [
        InboundMessage(
            sender_email="mario.rossi@example.com",
            sender_name="Mario Rossi",
            subject="Preventivo riparazione paraurti",
            body="Buongiorno, vorrei un preventivo per la sostituzione del paraurti della mia Fiat Panda."
        ),
        InboundMessage(
            sender_email="luca.bianchi@gmail.com",
            sender_name="Luca Bianchi",
            subject="Problema con la mia auto",
            body="Salve, la settimana scorsa ho fatto riparare la mia auto da voi e il problema persiste. Sono molto deluso.",
            attachments=["foto_danno.jpg"]
        ),
    ]

    for i, msg in enumerate(msgs, 1):
        result = classify_message(
            msg,
            client_id=CLIENT_ID,
            llm_client=anthropic_client,
            use_real_db=True,      # ← Supabase reale
            save_to_db=True,       # ← salva i risultati
        )
        print(f"\n{'='*50}")
        print(f"TEST {i}: {msg.subject}")
        print(f"  Tipo contatto : {result.contact_type}")
        print(f"  Intent        : {result.intent}")
        print(f"  Priorità      : {result.priority}")
        print(f"  Confidence    : {result.confidence:.0%}")
        print(f"  Classificato  : {result.classified_by}")
        print(f"  Sintesi       : {result.summary}")
        if result.db_ids:
            print(f"  ✅ Salvato su DB — email_id: {result.db_ids['email_id'][:8]}…")
