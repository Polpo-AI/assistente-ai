"""
email_worker.py — Worker asincrono IMAP/SMTP per Polpo AI
Sostituisce n8n come trigger e sender.

Funzioni principali:
  1. IMAP Polling loop  → controlla nuove email ogni 60s per ogni cliente attivo
  2. Approved Watcher   → controlla ogni 30s le bozze approvate e le invia via SMTP

Novità v3:
  - Strip quoted text anti-matriosca: salva solo il primo livello, non l'intera catena
  - Threading: salva message_id, in_reply_to, references per ricostruzione thread via query
  - Outbound tracking: al momento dell'invio SMTP crea un record emails con direction='outbound'
  - Message-ID generato da noi prima dell'invio (controllo totale)
  - Estrazione allegati (PDF via Sonnet, DOCX/XLSX via librerie)
  - Rilevamento lingua (langdetect)
  - Bounce detection estesa

Dipendenze:
    pip install aiosmtplib aioimaplib python-dotenv langdetect python-docx openpyxl
"""

import asyncio
import logging
import os
import re
import email as email_lib
import email.policy
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional
import uuid

import aioimaplib
import aiosmtplib
from dotenv import load_dotenv

from database import get_client as get_db, mark_email_no_reply, mark_draft_generation_failed, get_approved_drafts
from classifier import classify_message, InboundMessage
from responder import generate_response_draft
import telegram_bot
from anthropic import Anthropic
from attachment_reader import process_email_attachments

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("polpo.worker")

IMAP_POLL_INTERVAL     = 60
APPROVED_POLL_INTERVAL = 30
IMAP_TIMEOUT           = 30
SMTP_MAX_CONCURRENT    = 3  # max connessioni SMTP simultanee

# Circuit breaker IMAP — evita timeout ripetuti su server down
IMAP_CB_MAX_FAILS   = 3          # fallimenti prima di skipare
IMAP_CB_BACKOFF_SEC = 600        # 10 minuti di pausa prima di riprovare
_imap_circuit: dict[str, dict] = {}  # {client_id: {"fails": N, "retry_at": datetime}}

def _cb_is_open(client_id: str) -> bool:
    """Restituisce True se il circuit breaker è aperto (client skippato)."""
    cb = _imap_circuit.get(client_id)
    if not cb:
        return False
    if cb["fails"] < IMAP_CB_MAX_FAILS:
        return False
    if datetime.now(timezone.utc) >= cb["retry_at"]:
        # Backoff scaduto: ripristina un tentativo
        cb["fails"] = IMAP_CB_MAX_FAILS - 1
        logger.info("worker.cb | [%s] Circuit breaker rimesso in prova", client_id)
        return False
    return True

def _cb_record_fail(client_id: str, client_name: str) -> None:
    cb = _imap_circuit.setdefault(client_id, {"fails": 0, "retry_at": None})
    cb["fails"] += 1
    if cb["fails"] >= IMAP_CB_MAX_FAILS:
        from datetime import timedelta
        cb["retry_at"] = datetime.now(timezone.utc) + timedelta(seconds=IMAP_CB_BACKOFF_SEC)
        logger.warning(
            "worker.cb | [%s] Circuit breaker APERTO dopo %d fallimenti — skip per %ds",
            client_name, cb["fails"], IMAP_CB_BACKOFF_SEC
        )

def _cb_record_success(client_id: str) -> None:
    _imap_circuit.pop(client_id, None)

_smtp_semaphore: asyncio.Semaphore | None = None

def _get_smtp_semaphore() -> asyncio.Semaphore:
    global _smtp_semaphore
    if _smtp_semaphore is None:
        _smtp_semaphore = asyncio.Semaphore(SMTP_MAX_CONCURRENT)
    return _smtp_semaphore

anthropic_client = Anthropic()


# ─────────────────────────────────────────────
# Bounce detection
# ─────────────────────────────────────────────

BOUNCE_SENDER_PREFIXES = (
    "mailer-daemon@", "postmaster@", "noreply@", "no-reply@",
    "do-not-reply@", "donotreply@", "mail-daemon@", "daemon@",
    "auto-reply@", "auto_reply@", "bounce@", "bounces@",
    "delivery@", "maildelivery@",
)

BOUNCE_SUBJECT_KEYWORDS = (
    "delivery status notification", "undelivered mail",
    "mail delivery failed", "mail delivery failure",
    "returned mail", "failure notice", "non-delivery report",
    "undeliverable", "auto:", "automatic reply",
    "out of office", "fuori ufficio", "risposta automatica",
)

def is_bounce(sender_email: str, subject: str) -> bool:
    sender  = sender_email.lower().strip()
    subject = subject.lower().strip()
    if any(sender.startswith(p) for p in BOUNCE_SENDER_PREFIXES):
        return True
    if any(kw in subject for kw in BOUNCE_SUBJECT_KEYWORDS):
        return True
    return False


# ─────────────────────────────────────────────
# Strip quoted text — anti-matriosca
# ─────────────────────────────────────────────

# Pattern ordinati per specificità: prima i più precisi
_QUOTE_DELIMITERS = [
    # Outlook / Thunderbird: "Da: ... Inviato: ..."
    re.compile(r"\n\s*Da:\s.+?\n\s*Inviato:\s.+?\n",        re.IGNORECASE | re.DOTALL),
    re.compile(r"\n\s*From:\s.+?\n\s*Sent:\s.+?\n",         re.IGNORECASE | re.DOTALL),
    # Gmail italiano: "Il giorno X, Y ha scritto:"
    re.compile(r"\n\s*Il giorno .+? ha scritto:\s*\n",       re.IGNORECASE | re.DOTALL),
    # Gmail inglese: "On X, Y wrote:"
    re.compile(r"\n\s*On .+? wrote:\s*\n",                   re.IGNORECASE | re.DOTALL),
    # Separatori "---Original Message---"
    re.compile(r"\n[-_]{3,}.*?(Original Message|Messaggio originale|Forwarded).*?\n",
               re.IGNORECASE),
    # Linee che iniziano con ">" (tutti i client)
    re.compile(r"(\n>[^\n]*)+",                              re.MULTILINE),
]

def strip_quoted_text(body: str) -> tuple[str, str, bool]:
    """
    Estrae SOLO il primo livello di quoted text.
    Restituisce (corpo_pulito, quoted_primo_livello, nested_detected).

    nested_detected=True significa che nel quoted c'era a sua volta del quoted
    annidato (matriosca) — lo segnaliamo con tag [NESTED] e flag nel DB.
    """
    if not body:
        return "", "", False

    # Trova il punto più in alto in cui inizia il quoted
    earliest_pos = len(body)
    for pattern in _QUOTE_DELIMITERS:
        m = pattern.search(body)
        if m and m.start() < earliest_pos:
            earliest_pos = m.start()

    if earliest_pos == len(body):
        # Nessun quoted trovato
        return body.strip(), "", False

    clean  = body[:earliest_pos].strip()
    quoted = body[earliest_pos:].strip()

    # Controlla se il quoted contiene a sua volta del quoted (matriosca)
    nested = False
    for pattern in _QUOTE_DELIMITERS:
        if pattern.search(quoted):
            nested = True
            break

    if nested:
        # Strip del secondo livello — teniamo solo il messaggio immediatamente precedente
        inner_earliest = len(quoted)
        for pattern in _QUOTE_DELIMITERS:
            m = pattern.search(quoted)
            if m and m.start() < inner_earliest:
                inner_earliest = m.start()

        if inner_earliest < len(quoted):
            quoted = quoted[:inner_earliest].strip() + "\n[NESTED: messaggi precedenti disponibili nel DB]"

    return clean, quoted, nested


# ─────────────────────────────────────────────
# Rilevamento lingua
# ─────────────────────────────────────────────

def detect_email_language(text: str) -> str:
    if not text or len(text.strip()) < 20:
        return "unknown"
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        return "unknown"


# ─────────────────────────────────────────────
# Generazione Message-ID
# ─────────────────────────────────────────────

def generate_message_id(smtp_user: str) -> str:
    """
    Genera un Message-ID univoco nel formato RFC 5322.
    Es: <uuid@dominio>
    """
    domain  = smtp_user.split("@")[-1] if "@" in smtp_user else "polpo.ai"
    msg_id  = f"<{uuid.uuid4().hex}@{domain}>"
    return msg_id


# ─────────────────────────────────────────────
# Helpers DB
# ─────────────────────────────────────────────

def get_active_clients_with_email() -> list[dict]:
    result = (
        get_db().table("clients")
        .select("id, name, imap_host, imap_port, imap_user, imap_password, "
                "smtp_host, smtp_port, smtp_user, smtp_password, imap_last_uid, language")
        .eq("active", True)
        .not_.is_("imap_host", "null")
        .execute()
    )
    return result.data or []


def update_last_uid(client_id: str, uid: int) -> None:
    get_db().table("clients").update({"imap_last_uid": uid}).eq("id", client_id).execute()


from database import get_approved_drafts


def mark_draft_sent(draft_id: str, sent_message_id: str) -> None:
    get_db().table("draft_responses").update({
        "status":          "sent",
        "sent_at":         datetime.now(timezone.utc).isoformat(),
        "sent_message_id": sent_message_id,
    }).eq("id", draft_id).execute()


def mark_draft_failed(draft_id: str, error: str) -> None:
    get_db().table("draft_responses").update({
        "status":     "send_failed",
        "send_error": error[:500],
    }).eq("id", draft_id).execute()


def save_email_enrichments(
    email_id: str,
    message_id: str = "",
    in_reply_to: str = "",
    references_ids: list = None,
    quoted_text: str = "",
    quoted_nested: bool = False,
    detected_language: str = "",
    thread_topic: str = "",
) -> None:
    """Aggiorna le colonne di arricchimento su un record email esistente."""
    try:
        get_db().table("emails").update({
            "message_id":        message_id,
            "in_reply_to":       in_reply_to,
            "references_ids":    references_ids or [],
            "quoted_text":       quoted_text,
            "quoted_nested":     quoted_nested,
            "detected_language": detected_language,
            "thread_topic":      thread_topic,
        }).eq("id", email_id).execute()
    except Exception as e:
        logger.error("worker | Errore salvataggio enrichments email_id=%s: %s", email_id, e)


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
    Permette la ricostruzione completa del thread via query ricorsiva.
    """
    try:
        get_db().table("emails").insert({
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
        logger.info("worker | Salvata email outbound message_id=%s", sent_message_id)
    except Exception as e:
        logger.error("worker | Errore salvataggio outbound email: %s", e)


# ─────────────────────────────────────────────
# IMAP — lettura nuove email
# ─────────────────────────────────────────────

async def fetch_new_emails_imap(client: dict) -> list[dict]:
    """
    Recupera le nuove email IMAP con UID > last_uid.
    Esegue strip anti-matriosca, rilevamento lingua, parsing header thread.
    """
    client_id       = client["id"]
    last_uid_global = int(client.get("imap_last_uid") or 0)

    imap = aioimaplib.IMAP4_SSL(
        host=client["imap_host"],
        port=int(client.get("imap_port") or 993),
        timeout=IMAP_TIMEOUT,
    )

    try:
        await imap.wait_hello_from_server()
        imap_password = (client["imap_password"] or "").replace(" ", "")
        await imap.login(client["imap_user"], imap_password)
        logger.info("worker.imap | [%s] Login effettuato", client["name"])

        # ── Calibrazione silenziosa al primo avvio ──────────────────────
        # Se imap_last_uid è 0, imposta l'UID all'ultima email esistente
        # senza processare nulla. Il bot parte dalle email successive.
        if last_uid_global == 0:
            await imap.select("INBOX")
            _, cal_data = await imap.search("ALL")
            cal_seqs = [s for s in cal_data[0].decode().split() if s.strip()]
            if cal_seqs:
                _, hdr_data = await imap.fetch(cal_seqs[-1], "(UID)")
                cal_uid = 0
                for part in hdr_data:
                    if isinstance(part, (bytes, bytearray)):
                        m = re.search(rb"UID (\d+)", bytes(part))
                        if m:
                            cal_uid = int(m.group(1))
                            break
                if cal_uid > 0:
                    update_last_uid(client_id, cal_uid)
                    logger.info(
                        "worker.imap | [%s] Prima attivazione — calibrazione silenziosa UID=%d. "
                        "Il bot processerà solo le email successive.",
                        client["name"], cal_uid
                    )
            await imap.logout()
            return []
        # ────────────────────────────────────────────────────────────────

        emails_found = []

        for folder in ["INBOX"]:
            last_uid   = last_uid_global if folder == "INBOX" else 0
            select_res = await imap.select(folder)
            if select_res[0] != "OK":
                continue

            _, data   = await imap.search("ALL")
            seq_list  = [s for s in data[0].decode().split() if s.strip()]
            if not seq_list:
                continue

            new_seqs = []
            for seq_str in reversed(seq_list):
                _, hdr_data = await imap.fetch(seq_str, "(UID)")
                uid = None
                for part in hdr_data:
                    if isinstance(part, (bytes, bytearray)):
                        m = re.search(rb"UID (\d+)", bytes(part))
                        if m:
                            uid = int(m.group(1))
                            break
                if uid is None or uid <= last_uid:
                    break
                new_seqs.append((seq_str, uid))

            new_seqs = list(reversed(new_seqs))
            if not new_seqs:
                continue

            logger.info("worker.imap | [%s] %d nuove email in %s",
                        client["name"], len(new_seqs), folder)

            for seq_str, uid in new_seqs:
                _, msg_data = await imap.fetch(seq_str, "(RFC822)")
                if not msg_data:
                    continue

                raw_bytes = None
                for part in msg_data:
                    if isinstance(part, (bytes, bytearray)) and len(part) > 100:
                        raw_bytes = bytes(part)
                        break
                    elif isinstance(part, tuple):
                        for sub in part:
                            if isinstance(sub, (bytes, bytearray)) and len(sub) > 100:
                                raw_bytes = bytes(sub)
                                break
                        if raw_bytes:
                            break

                if not raw_bytes:
                    continue

                msg          = email_lib.message_from_bytes(raw_bytes, policy=email_lib.policy.default)
                from_raw     = msg.get("From", "")
                sender_name, sender_email = email_lib.utils.parseaddr(from_raw)
                sender_email = sender_email.lower().strip()
                sender_name  = sender_name.strip() or sender_email

                # Header threading RFC822
                message_id   = (msg.get("Message-ID", "") or "").strip()
                in_reply_to  = (msg.get("In-Reply-To", "") or "").strip()
                references   = (msg.get("References", "") or "").strip()
                # References è una lista di message_id separati da spazi/newline
                references_ids = [r.strip() for r in references.split() if r.strip()]

                subject = msg.get("Subject", "(nessun oggetto)").strip()

                # Bounce detection
                if is_bounce(sender_email, subject):
                    logger.info("worker.imap | [%s] Skip UID %d (bounce: %s)",
                                client["name"], uid, sender_email)
                    emails_found.append({
                        "uid": uid, "sender_email": sender_email,
                        "sender_name": sender_name, "subject": subject,
                        "body": "", "message_id": message_id,
                        "folder": folder, "_skip": True,
                    })
                    continue

                # Estrazione corpo + allegati
                body            = ""
                raw_attachments = []

                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        cd = str(part.get("Content-Disposition", ""))
                        fn = part.get_filename()

                        if fn or "attachment" in cd:
                            try:
                                att_data = part.get_payload(decode=True)
                                if att_data:
                                    raw_attachments.append({
                                        "filename":  fn or "allegato",
                                        "mime_type": ct,
                                        "data":      att_data,
                                    })
                            except Exception as att_e:
                                logger.warning("worker.imap | Errore allegato '%s': %s", fn, att_e)
                            continue

                        if ct == "text/plain" and not body:
                            body = part.get_content() or ""
                    if not body:
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                body = part.get_content() or ""
                                break
                else:
                    body = msg.get_content() or ""

                body = body.strip()
                if len(body) > 30000:
                    body = body[:30000] + "\n\n[...Testo troncato...]"

                if not body and not raw_attachments:
                    emails_found.append({
                        "uid": uid, "sender_email": sender_email,
                        "sender_name": sender_name, "subject": subject,
                        "body": "", "message_id": message_id,
                        "folder": folder, "_skip": True,
                    })
                    continue

                # Strip anti-matriosca
                clean_body, quoted_text, quoted_nested = strip_quoted_text(body)

                # Lingua
                detected_language = detect_email_language(clean_body or subject)

                emails_found.append({
                    "uid":               uid,
                    "sender_email":      sender_email,
                    "sender_name":       sender_name,
                    "subject":           subject,
                    "body":              clean_body,
                    "quoted_text":       quoted_text,
                    "quoted_nested":     quoted_nested,
                    "raw_attachments":   raw_attachments,
                    "message_id":        message_id,
                    "in_reply_to":       in_reply_to,
                    "references_ids":    references_ids,
                    "folder":            folder,
                    "detected_language": detected_language,
                })

        await imap.logout()
        emails_found.sort(key=lambda x: x["uid"])
        return emails_found

    except Exception as e:
        logger.error("worker.imap | [%s] Errore IMAP: %s", client.get("name", client_id), e)
        return []
    finally:
        try:
            await imap.logout()
        except Exception:
            pass


# ─────────────────────────────────────────────
# Pipeline processamento email
# ─────────────────────────────────────────────

def _update_draft_gen_status(email_id: str, status: str) -> None:
    """Aggiorna draft_generation_status sulla tabella emails."""
    try:
        get_db().table("emails").update({
            "draft_generation_status": status
        }).eq("id", email_id).execute()
    except Exception as e:
        logger.warning("worker | _update_draft_gen_status failed: %s", e)


async def _alert_draft_failed(client_id: str, email_id: str, error: str) -> None:
    """Invia alert Telegram quando la generazione bozza fallisce."""
    try:
        def fetch_db_data():
            db = get_db()
            c = db.table("clients").select("telegram_chat_id, name").eq("id", client_id).execute()
            e = db.table("emails").select("subject, sender_email").eq("id", email_id).execute()
            return c.data, e.data

        client_data, email_data = await asyncio.to_thread(fetch_db_data)
        
        if not client_data:
            return
        chat_id = client_data[0].get("telegram_chat_id")
        name    = client_data[0].get("name", client_id)
        if not chat_id:
            return
            
        subject = email_data[0].get("subject", "—") if email_data else "—"
        sender  = email_data[0].get("sender_email", "—") if email_data else "—"
        msg = (
            f"⚠️ *Generazione bozza fallita* — {name}\n\n"
            f"Da: `{sender}`\n"
            f"Oggetto: {subject}\n"
            f"Errore: `{error[:200]}`\n\n"
            f"Email ID: `{email_id}`\n"
            f"Richiede gestione manuale."
        )
        await telegram_bot.send_alert(chat_id, msg)
    except Exception as e:
        logger.error("worker | _alert_draft_failed: %s", e)


async def process_email(client_id: str, email_data: dict) -> None:
    """
    Flusso completo:
      1. Classificazione Haiku con contesto completo (corpo + thread)
      2. Salva enrichments nel DB (message_id, in_reply_to, quoted_text, lingua, thread_topic)
      3. Processa allegati con Sonnet (estrai testo, salva nel DB)
      4. Genera bozza con Sonnet
      5. Notifica Telegram
    """
    client_name = email_data.get("_client_name", client_id)
    logger.info("worker.process | [%s] Elaboro email da %s: %s",
                client_name, email_data.get("sender_email"), email_data.get("subject"))

    raw_attachments = email_data.get("raw_attachments", [])

    # Corpo per il classifier — se vuoto ma con allegati, segnalalo
    body_for_classifier = email_data["body"]
    if not body_for_classifier.strip() and raw_attachments:
        att_names           = ", ".join(a["filename"] for a in raw_attachments)
        body_for_classifier = f"[Email senza testo — allegati presenti: {att_names}]"

    # Costruisce il messaggio per il classificatore
    msg = InboundMessage(
        sender_email=email_data["sender_email"],
        sender_name=email_data["sender_name"],
        subject=email_data["subject"],
        body=body_for_classifier,
        detected_language=email_data.get("detected_language", "unknown"),
        in_reply_to=email_data.get("in_reply_to", ""),
    )

    # 1. Classificazione con retry automatico su errori transienti (429, 529)
    try:
        from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
        import anthropic as _anthropic_mod

        @retry(
            retry=retry_if_exception_type((_anthropic_mod.RateLimitError, _anthropic_mod.APIStatusError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        )
        def _classify_with_retry():
            return classify_message(
                msg=msg,
                client_id=client_id,
                llm_client=anthropic_client,
                use_real_db=True,
                save_to_db=True,
                quoted_text=email_data.get("quoted_text", ""),
            )

        result = await asyncio.to_thread(_classify_with_retry)
    except Exception as e:
        logger.error("worker.process | [%s] Errore classificazione: %s", client_name, e)
        raise

    if not result.db_ids:
        logger.error("worker.process | [%s] Salvataggio DB fallito", client_name)
        raise RuntimeError("Salvataggio DB fallito")

    email_id = result.db_ids["email_id"]
    logger.info("worker.process | [%s] Classificata → intent=%s thread_topic=%s priority=%d (email_id=%s)",
                client_name, result.intent, result.thread_topic or "—",
                result.priority, email_id)

    # 2. Salva enrichments nel DB
    await asyncio.to_thread(
        save_email_enrichments,
        email_id=email_id,
        message_id=email_data.get("message_id", ""),
        in_reply_to=email_data.get("in_reply_to", ""),
        references_ids=email_data.get("references_ids", []),
        quoted_text=email_data.get("quoted_text", ""),
        quoted_nested=email_data.get("quoted_nested", False),
        detected_language=email_data.get("detected_language", ""),
        thread_topic=result.thread_topic or "",
    )

    # 3. Allegati — screening Haiku + estrazione Sonnet
    if raw_attachments and result.priority > 0 and result.intent != "spam":
        logger.info("worker.process | [%s] Processo %d allegato/i...",
                    client_name, len(raw_attachments))
        att_result = await asyncio.to_thread(
            process_email_attachments,
            email_id,
            raw_attachments,
            anthropic_client,
            email_data.get("subject", ""),
            email_data.get("body", ""),
        )

        # Allegati grandi → chiedi conferma su Telegram
        for pending in att_result.get("to_ask", []):
            try:
                await telegram_bot.ask_extract_attachment(
                    client_id=client_id,
                    email_id=email_id,
                    filename=pending["filename"],
                    size_mb=round(pending["size_bytes"] / 1024 / 1024, 1),
                    reason=pending["reason"],
                )
            except Exception as e:
                logger.warning("worker.process | [%s] ask_extract_attachment fallito: %s", client_name, e)

        # Allegati medi estratti → notifica su Telegram (non blocca)
        for notified in att_result.get("notified", []):
            logger.info("worker.process | [%s] Allegato grande estratto: %s",
                        client_name, notified["filename"])

    # Priorità 0 o spam → nessuna risposta
    if result.priority == 0 or result.intent == "spam":
        logger.info("worker.process | [%s] Priorità 0 / spam (%s) — nessuna risposta",
                    client_name, result.intent)
        await asyncio.to_thread(mark_email_no_reply, email_id, result.summary)
        await asyncio.to_thread(_update_draft_gen_status, email_id, "skipped")
        return

    # 4. Genera bozza con Sonnet
    draft = None
    try:
        draft = await asyncio.to_thread(generate_response_draft, email_id, anthropic_client)
    except Exception as e:
        err = str(e)
        logger.error("worker.process | [%s] Eccezione generazione bozza: %s", client_name, err)
        await asyncio.to_thread(mark_draft_generation_failed, email_id, err)
        await _alert_draft_failed(client_id, email_id, err)
        return

    if not draft:
        err = "generate_response_draft ha restituito None"
        logger.error("worker.process | [%s] %s (email %s)", client_name, err, email_id)
        await asyncio.to_thread(mark_draft_generation_failed, email_id, err)
        await _alert_draft_failed(client_id, email_id, err)
        return

    await asyncio.to_thread(_update_draft_gen_status, email_id, "done")
    logger.info("worker.process | [%s] Bozza pronta → draft_id=%s", client_name, draft.draft_id)

    # 5. Notifica Telegram per email importanti (Priority 2 e 3)
    if result.priority >= 2 and draft.draft_id:
        try:
            # Prepara preview allegati per Telegram
            inbound_photos: list[dict] = []
            inbound_docs: list[dict] = []

            if att_result:
                att_text = att_result.get("extracted", {})

                for att in raw_attachments:
                    fname = att.get("filename", "")
                    data  = att.get("data", b"")
                    mime  = att.get("mime_type", "").lower()
                    ext   = fname.lower().split(".")[-1] if "." in fname else ""
                    if not data:
                        continue
                    if mime.startswith("image/") or ext in ("jpg", "jpeg", "png", "webp", "gif"):
                        inbound_photos.append({
                            "filename":    fname,
                            "data":        data,
                            "description": att_text.get(fname, ""),
                        })
                    elif ext in ("pdf", "docx", "xlsx", "xls", "txt"):
                        inbound_docs.append({
                            "filename": fname,
                            "data":     data,
                            "mime_type": mime,
                        })

            await telegram_bot.notify_draft(
                draft.draft_id,
                inbound_photos=inbound_photos or None,
                inbound_docs=inbound_docs or None,
            )
        except Exception as e:
            logger.warning("worker.process | [%s] Telegram fallito: %s", client_name, e)


# ─────────────────────────────────────────────
# SMTP — invio bozze approvate
# ─────────────────────────────────────────────

async def _send_email_smtp_inner(draft: dict, attachments: list[dict] | None = None) -> None:
    """
    Core invio SMTP — chiamato sempre dentro il semaforo.

    Args:
        draft:       record draft (con clients, emails embedded)
        attachments: lista opzionale di {filename: str, data: bytes, mime_type: str}
                     per allegare file generati (PDF, Excel) alla risposta
    """
    import email.mime.base as _mimebase
    import email.encoders as _encoders

    draft_id    = draft["id"]
    client_id   = draft["client_id"]
    client_smtp = draft.get("clients") or {}
    email_orig  = draft.get("emails") or {}

    to_address   = email_orig.get("sender_email", "")
    to_name      = email_orig.get("sender_name", "")
    subject      = draft.get("subject", "")
    body         = draft.get("body", "")
    from_name    = client_smtp.get("name", "")
    from_email   = client_smtp.get("smtp_user", "")

    if not to_address or not from_email:
        logger.error("worker.smtp | draft %s — dati mancanti", draft_id)
        await asyncio.to_thread(mark_draft_failed, draft_id, "Dati mittente/destinatario mancanti")
        return

    # Message-ID generato da noi → controllo totale, salviamo subito
    sent_message_id       = generate_message_id(from_email)
    original_message_id   = email_orig.get("message_id", "")

    # References: aggiungi message_id originale alla catena
    references_ids = []
    if original_message_id:
        references_ids = [original_message_id]

    # Se ci sono allegati, switch a 'mixed' per supportarli
    if attachments:
        mime_msg = MIMEMultipart("mixed")
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(body, "plain", "utf-8"))
        mime_msg.attach(alt_part)
    else:
        mime_msg = MIMEMultipart("alternative")
        mime_msg.attach(MIMEText(body, "plain", "utf-8"))

    mime_msg["Subject"]    = subject
    mime_msg["From"]       = f"{from_name} <{from_email}>" if from_name else from_email
    mime_msg["To"]         = f"{to_name} <{to_address}>" if to_name else to_address
    mime_msg["Message-ID"] = sent_message_id

    if original_message_id:
        mime_msg["In-Reply-To"] = original_message_id
        mime_msg["References"]  = original_message_id

    # Allega file (PDF/Excel generati)
    if attachments:
        for att in attachments:
            part = _mimebase.MIMEBase("application", "octet-stream")
            part.set_payload(att["data"])
            _encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment",
                            filename=att["filename"])
            mime_msg.attach(part)
            logger.info("worker.smtp | Allegato: %s (%d bytes)", att["filename"], len(att["data"]))

    try:
        await aiosmtplib.send(
            mime_msg,
            hostname=client_smtp["smtp_host"],
            port=int(client_smtp.get("smtp_port") or 587),
            username=client_smtp["smtp_user"],
            password=(client_smtp["smtp_password"] or "").replace(" ", ""),
            start_tls=True,
            timeout=30,
        )

        # Aggiorna draft con sent_message_id
        await asyncio.to_thread(mark_draft_sent, draft_id, sent_message_id)
        logger.info("worker.smtp | ✓ Inviata → draft=%s a <%s> msg_id=%s",
                    draft_id, to_address, sent_message_id)

        # Salva email outbound nel DB per threading completo
        await asyncio.to_thread(
            save_outbound_email,
            client_id=client_id,
            draft_id=draft_id,
            sent_message_id=sent_message_id,
            in_reply_to=original_message_id,
            references_ids=references_ids,
            sender_email=from_email,
            sender_name=from_name,
            recipient_email=to_address,
            subject=subject,
            body=body,
        )

        # Aggiorna card Telegram
        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            await telegram_bot.update_card_sent(tg_chat_id, tg_msg_id, draft)

    except Exception as e:
        err = str(e)
        logger.error("worker.smtp | ✗ Fallita → draft=%s: %s", draft_id, err)
        await asyncio.to_thread(mark_draft_failed, draft_id, err)

        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            await telegram_bot.update_card_failed(tg_chat_id, tg_msg_id, draft, err)


async def send_email_smtp(draft: dict) -> None:
    """
    Invia una bozza approvata via SMTP.
    Limita le connessioni simultanee a SMTP_MAX_CONCURRENT tramite semaforo.
    """
    async with _get_smtp_semaphore():
        await _send_email_smtp_inner(draft)


# ─────────────────────────────────────────────
# LOOP 1 — IMAP Polling
# ─────────────────────────────────────────────

async def _process_client_imap(client: dict, global_max_uid: dict) -> None:
    """Processa le nuove email IMAP per un singolo cliente — eseguito in parallelo."""
    client_id   = client["id"]
    client_name = client.get("name", client_id)

    # Circuit breaker: se il server IMAP è stato down per troppo tempo, saltiamo
    if _cb_is_open(client_id):
        logger.debug("worker.cb | [%s] Circuit breaker aperto — skip questo ciclo", client_name)
        return

    try:
        new_emails = await fetch_new_emails_imap(client)
        _cb_record_success(client_id)  # connessione andata a buon fine

        if not new_emails:
            return

        base_uid  = int(client.get("imap_last_uid") or 0)
        # UID per le email di skip (bounce, vuote) — questi possiamo aggiornare subito
        skip_uids: set[int] = set()
        # UID per le email processate con successo
        done_uids: set[int] = set()

        async def _safe_process(edata: dict) -> None:
            try:
                await process_email(client_id, edata)
                done_uids.add(edata["uid"])
            except Exception as inner_e:
                logger.error(
                    "worker.imap | [%s] Errore UID %s: %s — verrà ritentata al prossimo ciclo",
                    client_name, edata.get("uid"), inner_e
                )
                # Non aggiungiamo a done_uids → UID non avanzato → sarà ripresa

        tasks = []
        for email_data in new_emails:
            email_data["_client_name"] = client_name
            if email_data.get("_skip"):
                skip_uids.add(email_data["uid"])
                continue
            tasks.append(_safe_process(email_data))

        if tasks:
            await asyncio.gather(*tasks)

        # Calcola il UID sicuro: il più grande tra skip e done,
        # ma NON superiore all'UID minimo delle email che hanno fallito.
        all_processed = skip_uids | done_uids
        failed_uids = {
            ed["uid"] for ed in new_emails
            if not ed.get("_skip") and ed["uid"] not in done_uids
        }

        # Avanza l'UID solo fino al primo fallimento (conservativo e sicuro)
        safe_max_uid = base_uid
        for uid in sorted(all_processed):
            if failed_uids and uid > min(failed_uids):
                break  # non avanziamo oltre il primo gap
            safe_max_uid = uid

        if safe_max_uid > base_uid:
            logger.info("worker.imap | [%s] Aggiorno last_uid %d → %d (%d ok, %d fallite)",
                        client_name, base_uid, safe_max_uid, len(done_uids), len(failed_uids))
            await asyncio.to_thread(update_last_uid, client_id, safe_max_uid)
        elif failed_uids:
            logger.warning("worker.imap | [%s] %d email fallite — last_uid rimane a %d per il retry",
                           client_name, len(failed_uids), base_uid)

    except Exception as e:
        logger.error("worker.imap | [%s] Errore ciclo: %s", client_name, e)
        _cb_record_fail(client_id, client_name)


async def imap_polling_loop() -> None:
    logger.info("worker | ▶ IMAP polling loop avviato (ogni %ds)", IMAP_POLL_INTERVAL)

    while True:
        try:
            clients = await asyncio.to_thread(get_active_clients_with_email)
            if clients:
                # Tutti i clienti in parallelo — nessuno aspetta l'altro
                await asyncio.gather(
                    *[_process_client_imap(client, {}) for client in clients],
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error("worker.imap | Errore generale: %s", e)

        await asyncio.sleep(IMAP_POLL_INTERVAL)


# ─────────────────────────────────────────────
# LOOP 2 — Approved Watcher
# ─────────────────────────────────────────────

async def approved_watcher_loop() -> None:
    logger.info("worker | ▶ Approved watcher avviato (ogni %ds)", APPROVED_POLL_INTERVAL)

    while True:
        try:
            approved = await asyncio.to_thread(get_approved_drafts)
            if approved:
                logger.info("worker.approved | %d bozze da inviare", len(approved))
                await asyncio.gather(
                    *[send_email_smtp(draft) for draft in approved],
                    return_exceptions=True,
                )
        except Exception as e:
            logger.error("worker.approved | Errore generale: %s", e)

        await asyncio.sleep(APPROVED_POLL_INTERVAL)


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

async def start_worker() -> None:
    logger.info("=" * 55)
    logger.info("  Polpo AI — Email Worker v3")
    logger.info("  IMAP polling ogni %ds", IMAP_POLL_INTERVAL)
    logger.info("  Approved watcher ogni %ds", APPROVED_POLL_INTERVAL)
    logger.info("=" * 55)
    await asyncio.gather(imap_polling_loop(), approved_watcher_loop())


if __name__ == "__main__":
    import fcntl
    import sys

    lock_path = os.getenv("WORKER_LOCK_FILE", "/tmp/polpo_email_worker.lock")
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.error("❌ Un'istanza di email_worker.py è già in esecuzione! Exit.")
        sys.exit(1)

    try:
        asyncio.run(start_worker())
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
