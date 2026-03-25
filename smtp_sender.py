import asyncio
import logging
import os
import uuid
import socket
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import email.mime.base as _mimebase
import email.encoders as _encoders
import base64
from typing import Optional

from database import (
    mark_draft_sent,
    mark_draft_failed,
    save_outbound_email
)

logger = logging.getLogger("polpo.smtp")

SMTP_MAX_CONCURRENT = int(os.getenv("SMTP_MAX_CONCURRENT", "5"))
_smtp_semaphore: Optional[asyncio.Semaphore] = None

def _get_smtp_semaphore():
    global _smtp_semaphore
    if _smtp_semaphore is None:
        _smtp_semaphore = asyncio.Semaphore(SMTP_MAX_CONCURRENT)
    return _smtp_semaphore

def generate_message_id(from_email: str) -> str:
    """Genera un Message-ID RFC822 valido e RFC compliant."""
    import email.utils
    domain = from_email.split("@")[-1] if "@" in from_email else socket.gethostname()
    return email.utils.make_msgid(domain=domain)

async def _send_email_smtp_inner(draft: dict, attachments: list[dict] = None) -> None:
    """
    Logica core di invio SMTP.
    attachments: lista opzionale di {filename: str, data: bytes, mime_type: str}
    """
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
        logger.error("smtp | draft %s — dati mancanti", draft_id)
        await asyncio.to_thread(mark_draft_failed, draft_id, "Dati mittente/destinatario mancanti")
        return

    sent_message_id       = generate_message_id(from_email)
    original_message_id   = email_orig.get("message_id", "")

    # Se ci sono allegati, switch a 'mixed'
    if attachments:
        mime_msg = MIMEMultipart("mixed")
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(body, "plain", "utf-8"))
        mime_msg.attach(alt_part)
    else:
        mime_msg = MIMEMultipart("alternative")
        mime_msg.attach(MIMEText(body, "plain", "utf-8"))

    import email.utils
    mime_msg["Subject"]    = subject
    mime_msg["From"]       = email.utils.formataddr((from_name, from_email))
    mime_msg["To"]         = email.utils.formataddr((to_name, to_address))
    mime_msg["Message-ID"] = sent_message_id


    if original_message_id:
        mime_msg["In-Reply-To"] = original_message_id
        mime_msg["References"]  = original_message_id

    if attachments:
        for att in attachments:
            part = _mimebase.MIMEBase("application", "octet-stream")
            part.set_payload(att["data"])
            _encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=att["filename"])
            mime_msg.attach(part)

    try:
        await aiosmtplib.send(
            mime_msg,
            hostname=client_smtp["smtp_host"],
            port=int(client_smtp.get("smtp_port") or 587),
            username=client_smtp["smtp_user"],
            password=client_smtp["smtp_password"] or "",
            start_tls=True,
            timeout=30,
        )

        await asyncio.to_thread(mark_draft_sent, draft_id, sent_message_id)
        logger.info("smtp | ✓ Inviata → draft=%s a <%s>", draft_id, to_address)

        await asyncio.to_thread(
            save_outbound_email,
            client_id=client_id,
            draft_id=draft_id,
            sent_message_id=sent_message_id,
            in_reply_to=original_message_id,
            references_ids=[original_message_id] if original_message_id else [],
            sender_email=from_email,
            sender_name=from_name,
            recipient_email=to_address,
            subject=subject,
            body=body,
        )

        # Aggiorna card Telegram (deferred import per evitare dipendenza circolare)
        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            try:
                import telegram_bot as _tgbot
                await _tgbot.update_card_sent(tg_chat_id, tg_msg_id, draft)
            except Exception as tg_err:
                logger.warning("smtp | Telegram card update fallito: %s", tg_err)

        return True

    except Exception as e:
        err = str(e)
        logger.error("smtp | ✗ Fallita → draft=%s: %s", draft_id, err)
        await asyncio.to_thread(mark_draft_failed, draft_id, err)

        # Aggiorna card Telegram con errore
        tg_msg_id  = draft.get("telegram_message_id")
        tg_chat_id = client_smtp.get("telegram_chat_id")
        if tg_msg_id and tg_chat_id:
            try:
                import telegram_bot as _tgbot
                await _tgbot.update_card_failed(tg_chat_id, tg_msg_id, draft, err)
            except Exception as tg_err:
                logger.warning("smtp | Telegram error card update fallito: %s", tg_err)
        raise e

async def send_email_smtp(draft: dict) -> None:
    """Invia una bozza approvata via SMTP con gestione semaforo e allegati dal DB."""
    attachments: list[dict] | None = None
    att_filename = draft.get("attachment_filename")
    att_data_b64 = draft.get("attachment_data")
    if att_filename and att_data_b64:
        try:
            att_bytes = base64.b64decode(att_data_b64)
            attachments = [{"filename": att_filename, "data": att_bytes}]
        except Exception as e:
            logger.warning("smtp | Errore decode allegato: %s", e)

    async with _get_smtp_semaphore():
        await _send_email_smtp_inner(draft, attachments=attachments)
