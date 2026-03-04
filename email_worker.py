"""
email_worker.py — Worker asincrono IMAP/SMTP per Polpo AI
Sostituisce n8n come trigger e sender.

Funzioni principali:
  1. IMAP Polling loop  → controlla nuove email ogni 60s per ogni cliente attivo
  2. Approved Watcher   → controlla ogni 30s le bozze approvate e le invia via SMTP

Dipendenze:
    pip install aiosmtplib aioimaplib python-dotenv

Configurazione per cliente in Supabase (tabella clients):
    imap_host, imap_port, imap_user, imap_password
    smtp_host, smtp_port, smtp_user, smtp_password
    imap_last_uid  → UID dell'ultima email processata (evita duplicati)
"""

import asyncio
import logging
import os
import email as email_lib
import email.policy
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional

import aioimaplib
import aiosmtplib
from dotenv import load_dotenv

# Import interni Polpo AI
from database import get_client as get_db, mark_email_no_reply
from classifier import classify_message, InboundMessage
from responder import generate_response_draft
import telegram_bot
from anthropic import Anthropic

load_dotenv()

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("polpo.worker")

# ─────────────────────────────────────────────
# Costanti
# ─────────────────────────────────────────────

IMAP_POLL_INTERVAL     = 60   # secondi tra un controllo IMAP e il successivo
APPROVED_POLL_INTERVAL = 30   # secondi tra un controllo bozze approvate e il successivo
IMAP_TIMEOUT           = 30   # timeout connessione IMAP in secondi

anthropic_client = Anthropic()


# ─────────────────────────────────────────────
# Helpers DB — lettura configurazioni email clienti
# ─────────────────────────────────────────────

def get_active_clients_with_email() -> list[dict]:
    """
    Ritorna tutti i clienti attivi che hanno le credenziali IMAP configurate.

    Campi attesi nella tabella 'clients' (da aggiungere con migration):
        imap_host, imap_port, imap_user, imap_password
        smtp_host, smtp_port, smtp_user, smtp_password
        imap_last_uid  (int, default 0) — UID ultima email processata
    """
    db = get_db()
    result = (
        db.table("clients")
        .select("id, name, imap_host, imap_port, imap_user, imap_password, "
                "smtp_host, smtp_port, smtp_user, smtp_password, imap_last_uid")
        .eq("active", True)
        .not_.is_("imap_host", "null")
        .execute()
    )
    return result.data or []


def update_last_uid(client_id: str, uid: int) -> None:
    """Aggiorna l'ultimo UID processato per evitare di riprocessare la stessa email."""
    db = get_db()
    db.table("clients").update({"imap_last_uid": uid}).eq("id", client_id).execute()


def get_approved_drafts() -> list[dict]:
    """
    Recupera tutte le bozze approvate non ancora inviate, con i dati dell'email originale
    e le credenziali SMTP del cliente.
    """
    db = get_db()
    result = (
        db.table("draft_responses")
        .select("id, client_id, subject, body, email_id, "
                "emails(sender_email, sender_name, subject), "
                "clients(smtp_host, smtp_port, smtp_user, smtp_password, name)")
        .eq("status", "approved")
        .execute()
    )
    return result.data or []


def mark_draft_sent(draft_id: str) -> None:
    """Segna la bozza come inviata con timestamp."""
    db = get_db()
    db.table("draft_responses").update({
        "status":  "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", draft_id).execute()


def mark_draft_failed(draft_id: str, error: str) -> None:
    """Segna la bozza come fallita con messaggio di errore."""
    db = get_db()
    db.table("draft_responses").update({
        "status":     "send_failed",
        "send_error": error[:500],
    }).eq("id", draft_id).execute()


# ─────────────────────────────────────────────
# IMAP — lettura nuove email
# ─────────────────────────────────────────────

async def fetch_new_emails_imap(client: dict) -> list[dict]:
    """
    Si connette via IMAP al provider del cliente e recupera le email
    con UID maggiore dell'ultimo processato.

    Ritorna lista di dict: uid, sender_email, sender_name, subject, body
    """
    client_id = client["id"]
    last_uid  = int(client.get("imap_last_uid") or 0)

    imap = aioimaplib.IMAP4_SSL(
        host=client["imap_host"],
        port=int(client.get("imap_port") or 993),
        timeout=IMAP_TIMEOUT,
    )

    try:
        logger.info("worker.imap | [%s] Connessione a %s:%s...", client["name"], client["imap_host"], client.get("imap_port", 993))
        await imap.wait_hello_from_server()
        await imap.login(client["imap_user"], client["imap_password"])
        logger.info("worker.imap | [%s] Login effettuato", client["name"])
        
        await imap.select("INBOX")
        logger.info("worker.imap | [%s] Cartella INBOX selezionata", client["name"])

        # Cerca i messaggi (fallback se UID SEARCH fallisce o appende)
        logger.info("worker.imap | [%s] Ricerca messaggi...", client["name"])
        
        # Prendiamo gli ultimi 50 messaggi per evitare di scaricare tutto il mailbox
        # (Molto più sicuro che un UID SEARCH potenzialmente appeso)
        _, data = await imap.search("ALL")
        seq_list_all = [s for s in data[0].decode().split() if s.strip()]
        seq_list = seq_list_all[-50:] # Ultime 50 mail
        
        logger.info("worker.imap | [%s] Analisi ultime %d email della cartella", client["name"], len(seq_list))

        emails_found = []

        for seq_str in seq_list:
            # Recuperiamo UID e corpo
            _, msg_data = await imap.fetch(seq_str, "(UID RFC822)")
            if not msg_data:
                continue
            
            uid = None
            raw_bytes = None
            
            for i, part in enumerate(msg_data):
                # Il corpo RFC822 può essere bytes o bytearray (come visto nei log di debug)
                if isinstance(part, (bytes, bytearray)):
                    p_bytes = bytes(part)
                    
                    # Se il segmento contiene i metadati (UID, RFC822, etc), estraiamo l'UID
                    if b"UID " in p_bytes:
                        import re
                        m = re.search(r"UID (\d+)", p_bytes.decode(errors="ignore"))
                        if m: uid = int(m.group(1))
                    
                    # Il corpo RFC822 è il segmento di bytes che NON è la riga di comando IMAP.
                    # Identifichiamo il corpo come il segmento più grande (> 100 bytes).
                    if len(p_bytes) > 100:
                        if not raw_bytes or len(p_bytes) > len(raw_bytes):
                            raw_bytes = p_bytes
                    elif not raw_bytes and len(p_bytes) > 30 and b"UID" not in p_bytes:
                        # Fallback per email estremamente corte, evitando i piccoli meta-frammenti
                        raw_bytes = p_bytes
                elif isinstance(part, tuple):
                    # Spesso aioimaplib ritorna tuple (header, body_bytes)
                    for sub in part:
                        if isinstance(sub, (bytes, bytearray)) and len(sub) > 100:
                            raw_bytes = bytes(sub)
                            break

            if uid:
                logger.info("worker.imap | [%s] Seq %s -> UID %d", client["name"], seq_str, uid)

            if not uid or uid <= last_uid:
                continue
            
            logger.info("worker.imap | [%s] Trovata NUOVA email! (UID: %d > %d)", client["name"], uid, last_uid)
            if not raw_bytes:
                logger.warning("worker.imap | [%s] Nessun corpo RFC822 trovato per UID: %d", client["name"], uid)
                continue
            
            logger.info("worker.imap | [%s] Parsing email UID: %d", client["name"], uid)

            msg = email_lib.message_from_bytes(raw_bytes, policy=email_lib.policy.default)

            # Mittente
            from_raw               = msg.get("From", "")
            sender_name, sender_email = email_lib.utils.parseaddr(from_raw)
            sender_email           = sender_email.lower().strip()
            sender_name            = sender_name.strip() or sender_email
            message_id             = msg.get("Message-ID", "")

            subject = msg.get("Subject", "(nessun oggetto)").strip()

            # Body — preferisce plain text
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    ct = part.get_content_type()
                    cd = str(part.get("Content-Disposition", ""))
                    if ct == "text/plain" and "attachment" not in cd:
                        body = part.get_content()
                        break
                if not body:
                    for part in msg.walk():
                        if part.get_content_type() == "text/html":
                            body = part.get_content()
                            break
            else:
                body = msg.get_content()

            body = (body or "").strip()

            # Salta email vuote o bounce di sistema
            if not body or sender_email.startswith("mailer-daemon@") or sender_email.startswith("postmaster@"):
                continue

            emails_found.append({
                "uid":          uid,
                "sender_email": sender_email,
                "sender_name":  sender_name,
                "subject":      subject,
                "body":         body,
                "message_id":   message_id,
            })

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

def process_email(client_id: str, email_data: dict) -> None:
    """
    Replica esattamente il flusso che prima faceva n8n:
      1. Classifica l'email (cascata 3 livelli)
      2. Genera la bozza con Claude Sonnet
      3. Notifica Telegram se priorità >= 2

    È sincrona — classifier e responder sono blocking.
    Viene chiamata da run_in_executor per non bloccare il loop async.
    """
    client_name = email_data.get("_client_name", client_id)
    logger.info("worker.process | [%s] Elaboro: <%s> — %s",
                client_name, email_data["sender_email"], email_data["subject"])

    msg = InboundMessage(
        sender_email=email_data["sender_email"],
        sender_name=email_data["sender_name"],
        subject=email_data["subject"],
        body=email_data["body"],
        attachments=[],
    )

    # Classificazione a cascata (DB → Regole → LLM Haiku)
    result = classify_message(
        msg=msg,
        client_id=client_id,
        llm_client=anthropic_client,
        use_real_db=True,
        save_to_db=True,
    )

    if not result.db_ids:
        logger.error("worker.process | [%s] Salvataggio DB fallito", client_name)
        return

    email_id = result.db_ids["email_id"]
    logger.info("worker.process | [%s] Classificata → intent=%s priority=%d by=%s",
                client_name, result.intent, result.priority, result.classified_by)

    # Priorità 0: email automatiche o cortesia → segna no_reply, non generare bozza
    if result.priority == 0:
        logger.info("worker.process | [%s] Priorità 0 (%s) — nessuna risposta generata",
                    client_name, result.intent)
        if result.db_ids:
            mark_email_no_reply(result.db_ids["email_id"], result.summary)
        return

    # Spam: non generare bozza
    if result.intent == "spam":
        logger.info("worker.process | [%s] Spam ignorato", client_name)
        return

    # Genera bozza con Claude Sonnet
    draft = generate_response_draft(email_id, anthropic_client)
    if not draft:
        logger.error("worker.process | [%s] Generazione bozza fallita per email %s",
                     client_name, email_id)
        return

    logger.info("worker.process | [%s] Bozza pronta → draft_id=%s", client_name, draft.draft_id)

    # Notifica Telegram per email importanti (Priority 2 e 3)
    if result.priority >= 2 and draft.draft_id:
        try:
            telegram_bot.notify_draft(draft.draft_id)
        except Exception as e:
            logger.warning("worker.process | [%s] Telegram fallito: %s", client_name, e)


# ─────────────────────────────────────────────
# SMTP — invio bozze approvate
# ─────────────────────────────────────────────

async def send_email_smtp(draft: dict) -> None:
    """
    Invia una bozza approvata tramite SMTP del cliente proprietario.
    Aggiorna lo status a 'sent' o 'send_failed'.
    """
    draft_id    = draft["id"]
    client_smtp = draft.get("clients") or {}
    email_orig  = draft.get("emails") or {}

    to_address = email_orig.get("sender_email", "")
    to_name    = email_orig.get("sender_name", "")
    subject    = draft.get("subject", "")
    body       = draft.get("body", "")
    from_name  = client_smtp.get("name", "")
    from_email = client_smtp.get("smtp_user", "")

    if not to_address or not from_email:
        logger.error("worker.smtp | draft %s — dati mancanti (to=%s from=%s)",
                     draft_id, to_address, from_email)
        mark_draft_failed(draft_id, "Dati mittente/destinatario mancanti")
        return

    # Costruisci messaggio MIME
    mime_msg             = MIMEMultipart("alternative")
    mime_msg["Subject"]  = subject
    mime_msg["From"]     = f"{from_name} <{from_email}>" if from_name else from_email
    mime_msg["To"]       = f"{to_name} <{to_address}>" if to_name else to_address

    # Thread header per risposta nella stessa conversazione
    original_message_id = email_orig.get("message_id", "")
    if original_message_id:
        mime_msg["In-Reply-To"] = original_message_id
        mime_msg["References"]  = original_message_id

    mime_msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        await aiosmtplib.send(
            mime_msg,
            hostname=client_smtp["smtp_host"],
            port=int(client_smtp.get("smtp_port") or 587),
            username=client_smtp["smtp_user"],
            password=client_smtp["smtp_password"],
            start_tls=True,
            timeout=30,
        )
        mark_draft_sent(draft_id)
        logger.info("worker.smtp | ✓ Inviata → draft=%s a <%s>", draft_id, to_address)

    except Exception as e:
        err = str(e)
        logger.error("worker.smtp | ✗ Fallita → draft=%s: %s", draft_id, err)
        mark_draft_failed(draft_id, err)


# ─────────────────────────────────────────────
# LOOP 1 — IMAP Polling
# ─────────────────────────────────────────────

async def imap_polling_loop() -> None:
    """
    Ogni IMAP_POLL_INTERVAL secondi controlla le nuove email
    per tutti i clienti attivi con IMAP configurato.
    """
    logger.info("worker | ▶ IMAP polling loop avviato (ogni %ds)", IMAP_POLL_INTERVAL)
    loop = asyncio.get_event_loop()

    while True:
        try:
            clients = get_active_clients_with_email()
            logger.debug("worker.imap | Controllo %d clienti", len(clients))

            for client in clients:
                client_id   = client["id"]
                client_name = client.get("name", client_id)

                try:
                    new_emails = await fetch_new_emails_imap(client)
                    if not new_emails:
                        continue

                    logger.info("worker.imap | [%s] %d nuove email", client_name, len(new_emails))

                    max_uid = int(client.get("imap_last_uid") or 0)

                    for email_data in new_emails:
                        email_data["_client_name"] = client_name
                        # Parte sync eseguita in thread pool per non bloccare l'event loop
                        await loop.run_in_executor(None, process_email, client_id, email_data)
                        max_uid = max(max_uid, email_data["uid"])

                    # Aggiorna UID solo dopo aver processato tutto il batch
                    if max_uid > int(client.get("imap_last_uid") or 0):
                        update_last_uid(client_id, max_uid)

                except Exception as e:
                    logger.error("worker.imap | [%s] Errore ciclo: %s", client_name, e)

        except Exception as e:
            logger.error("worker.imap | Errore generale: %s", e)

        await asyncio.sleep(IMAP_POLL_INTERVAL)


# ─────────────────────────────────────────────
# LOOP 2 — Approved Watcher
# ─────────────────────────────────────────────

async def approved_watcher_loop() -> None:
    """
    Ogni APPROVED_POLL_INTERVAL secondi cerca le bozze approvate
    e le invia via SMTP in parallelo.
    """
    logger.info("worker | ▶ Approved watcher avviato (ogni %ds)", APPROVED_POLL_INTERVAL)

    while True:
        try:
            approved = get_approved_drafts()
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
# Entry point standalone
# ─────────────────────────────────────────────

async def start_worker() -> None:
    """
    Avvia entrambi i loop in parallelo.
    Può essere chiamato da main.py con asyncio.create_task()
    oppure direttamente con: python email_worker.py
    """
    logger.info("=" * 55)
    logger.info("  Polpo AI — Email Worker")
    logger.info("  IMAP polling ogni %ds", IMAP_POLL_INTERVAL)
    logger.info("  Approved watcher ogni %ds", APPROVED_POLL_INTERVAL)
    logger.info("=" * 55)

    await asyncio.gather(
        imap_polling_loop(),
        approved_watcher_loop(),
    )


if __name__ == "__main__":
    asyncio.run(start_worker())
