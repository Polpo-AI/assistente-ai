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
        .select("id, client_id, subject, body, email_id, telegram_message_id, "
                "emails(sender_email, sender_name, subject), "
                "clients(smtp_host, smtp_port, smtp_user, smtp_password, name, telegram_chat_id)")
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
    last_uid_global = int(client.get("imap_last_uid") or 0)

    imap = aioimaplib.IMAP4_SSL(
        host=client["imap_host"],
        port=int(client.get("imap_port") or 993),
        timeout=IMAP_TIMEOUT,
    )

    try:
        logger.info("worker.imap | [%s] Connessione a %s:%s...", client["name"], client["imap_host"], client.get("imap_port", 993))
        await imap.wait_hello_from_server()
        # Gmail app passwords are stored with spaces (e.g. "xxxx xxxx xxxx xxxx") — strip them
        imap_password = (client["imap_password"] or "").replace(" ", "")
        await imap.login(client["imap_user"], imap_password)
        logger.info("worker.imap | [%s] Login effettuato", client["name"])
        
        # Lista cartelle da controllare
        # Solo INBOX per ora — la cartella Spam di Gmail richiede gestione separata
        folders = ["INBOX"]
        
        emails_found = []

        for folder in folders:
            # Per INBOX usa imap_last_uid dal DB; per Spam parte sempre da 0
            last_uid = last_uid_global if folder == "INBOX" else 0

            logger.info("worker.imap | [%s] Seleziono %s (last_uid seen: %d)...", client["name"], folder, last_uid)
            select_res = await imap.select(folder)
            if select_res[0] != 'OK':
                continue

            # Recupera tutti i numeri di sequenza e scansiona dalla fine
            # fermandosi appena troviamo UID <= last_uid (già processati)
            import re as _re
            _, data = await imap.search("ALL")
            seq_list = [s for s in data[0].decode().split() if s.strip()]

            if not seq_list:
                logger.info("worker.imap | [%s] Nessuna email in %s", client["name"], folder)
                continue

            # Scorri dalla più recente, fermati al primo già processato
            new_seqs = []
            for seq_str in reversed(seq_list):
                _, hdr_data = await imap.fetch(seq_str, "(UID)")
                uid = None
                for part in hdr_data:
                    if isinstance(part, (bytes, bytearray)):
                        m = _re.search(rb"UID (\d+)", bytes(part))
                        if m:
                            uid = int(m.group(1))
                            break
                if uid is None or uid <= last_uid:
                    break
                new_seqs.append((seq_str, uid))

            new_seqs = list(reversed(new_seqs))  # ordina dal più vecchio
            if not new_seqs:
                logger.info("worker.imap | [%s] Nessuna nuova email in %s (last_uid=%d)", client["name"], folder, last_uid)
                continue

            logger.info("worker.imap | [%s] %d nuove email in %s", client["name"], len(new_seqs), folder)

            for seq_str, uid in new_seqs:
                # Scarica il corpo completo
                _, msg_data = await imap.fetch(seq_str, "(RFC822)")
                if not msg_data:
                    logger.warning("worker.imap | [%s] Nessun dato per UID %d", client["name"], uid)
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

                logger.info("worker.imap | [%s] Trovata NUOVA email (UID: %d)", client["name"], uid)
                if not raw_bytes:
                    logger.warning("worker.imap | [%s] Nessun corpo RFC822 per UID %d", client["name"], uid)
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
                if len(body) > 30000:
                    logger.warning("worker.imap | [%s] Email UID %d troncata da %d a 30000 caratteri", client["name"], uid, len(body))
                    body = body[:30000] + "\n\n[...Testo troncato: troppo lungo...]"

                # Salta email vuote o bounce di sistema, ma traccia l'UID
                if not body or sender_email.startswith("mailer-daemon@") or sender_email.startswith("postmaster@"):
                    logger.info("worker.imap | [%s] Skip UID %d (mailer-daemon/postmaster/vuota)", client["name"], uid)
                    emails_found.append({
                        "uid":          uid,
                        "sender_email": sender_email,
                        "sender_name":  sender_name,
                        "subject":      subject,
                        "body":         "",
                        "message_id":   message_id,
                        "folder":       folder,
                        "_skip":        True,
                    })
                    continue

                emails_found.append({
                    "uid":          uid,
                    "sender_email": sender_email,
                    "sender_name":  sender_name,
                    "subject":      subject,
                    "body":         body,
                    "message_id":   message_id,
                    "folder":       folder
                })

        await imap.logout()
        # Ordina per UID totale (opzionale)
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

async def process_email(client_id: str, email_data: dict) -> None:
    """
    Replica il flusso di elaborazione:
      1. Classifica l'email (Sync -> via to_thread)
      2. Genera la bozza (Sync -> via to_thread)
      3. Notifica Telegram (Async -> await)
    """
    client_name = email_data.get("_client_name", client_id)
    logger.info("worker.process | [%s] Elaboro email da %s: %s", 
                client_name, email_data.get("sender_email"), email_data.get("subject"))

    anthropic_client = Anthropic()

    # 1. Classificazione (Blocking -> Thread)
    try:
        from classifier import InboundMessage, classify_message
        msg = InboundMessage(
            sender_email=email_data["sender_email"],
            sender_name=email_data["sender_name"],
            subject=email_data["subject"],
            body=email_data["body"]
        )
        
        result = await asyncio.to_thread(
            classify_message, 
            msg=msg, 
            client_id=client_id, 
            llm_client=anthropic_client, 
            use_real_db=True, 
            save_to_db=True
        )
    except Exception as e:
        logger.error("worker.process | [%s] Errore classificazione per email da %s: %s", 
                     client_name, email_data["sender_email"], e)
        return

    if not result.db_ids:
        logger.error("worker.process | [%s] Salvataggio DB fallito dopo classificazione", client_name)
        return

    email_id = result.db_ids["email_id"]
    logger.info("worker.process | [%s] Classificata → intent=%s priority=%d by=%s (email_id: %s)",
                client_name, result.intent, result.priority, result.classified_by, email_id)

    # Priorità 0: email automatiche o cortesia → segna no_reply, non generare bozza
    if result.priority == 0:
        logger.info("worker.process | [%s] Priorità 0 (%s) — nessuna risposta generata",
                    client_name, result.intent)
        mark_email_no_reply(email_id, result.summary)
        return

    # Spam: non generare bozza
    if result.intent == "spam":
        logger.info("worker.process | [%s] Spam ignorato", client_name)
        return

    # Genera bozza con Claude Sonnet (Blocking -> Thread)
    draft = await asyncio.to_thread(generate_response_draft, email_id, anthropic_client)
    if not draft:
        logger.error("worker.process | [%s] Generazione bozza fallita per email %s",
                     client_name, email_id)
        return

    logger.info("worker.process | [%s] Bozza pronta → draft_id=%s", client_name, draft.draft_id)

    # Notifica Telegram per email importanti (Priority 2 e 3)
    if result.priority >= 2 and draft.draft_id:
        try:
            await telegram_bot.notify_draft(draft.draft_id)
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
            password=(client_smtp["smtp_password"] or "").replace(" ", ""),
            start_tls=True,
            timeout=30,
        )
        mark_draft_sent(draft_id)
        logger.info("worker.smtp | ✓ Inviata → draft=%s a <%s>", draft_id, to_address)

        # Aggiorna la card Telegram con stato finale "📨 Inviata!" (solo se la card esiste)
        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            await telegram_bot.update_card_sent(tg_chat_id, tg_msg_id, draft)

    except Exception as e:
        err = str(e)
        logger.error("worker.smtp | ✗ Fallita → draft=%s: %s", draft_id, err)
        mark_draft_failed(draft_id, err)

        # Aggiorna la card Telegram segnalando il fallimento
        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            await telegram_bot.update_card_failed(tg_chat_id, tg_msg_id, draft, err)


# ─────────────────────────────────────────────
# LOOP 1 — IMAP Polling
# ─────────────────────────────────────────────

async def imap_polling_loop() -> None:
    """
    Ogni IMAP_POLL_INTERVAL secondi controlla le nuove email
    per tutti i clienti attivi con IMAP configurato.
    """
    logger.info("worker | ▶ IMAP polling loop avviato (ogni %ds)", IMAP_POLL_INTERVAL)
    # loop = asyncio.get_event_loop() # No longer needed as process_email is async

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
                        # Email marcate _skip (mailer-daemon, postmaster, vuote):
                        # non processare ma aggiorna max_uid per non riprocessarle
                        if email_data.get("_skip"):
                            max_uid = max(max_uid, email_data["uid"])
                            continue
                        try:
                            await process_email(client_id, email_data)
                            max_uid = max(max_uid, email_data["uid"])
                        except Exception as inner_e:
                            logger.error("worker.imap | [%s] Errore processamento email UID %s: %s",
                                         client_name, email_data.get("uid"), inner_e)

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
    import fcntl
    import sys
    
    lock_file = open("/tmp/polpo_email_worker.lock", "w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.error("❌ Un'istanza di email_worker.py è già in esecuzione! Exit.")
        print("Worker is already running. Exiting.")
        sys.exit(1)
        
    try:
        asyncio.run(start_worker())
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
