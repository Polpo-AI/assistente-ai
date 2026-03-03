import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

logger = logging.getLogger("polpo.notifications")

def notify_missing_feature(client_id: str, chat_id: str, message: str, conversation_history: list):
    """
    Invia un'email all'admin segnalando una funzione richiesta ma non disponibile.
    """
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    admin_email = "admin@polpo-ai.com"

    if not smtp_user or not smtp_pass:
        logger.warning("Notifica fallita: SMTP_USER o SMTP_PASS non configurati.")
        return

    # Formattazione history
    history_str = ""
    if conversation_history:
        for msg in conversation_history:
            role = "Operatore" if msg.get("role") == "user" else "Bot"
            # Supporto per tool_use e tool_result (blocchi lista)
            content = msg.get("content")
            if isinstance(content, list):
                # Estraiamo il testo dai blocchi se presente
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[Tool Use: {block.get('name')}]")
                        elif block.get("type") == "tool_result":
                            text_parts.append("[Tool Result]")
                content_text = " ".join(text_parts)
            else:
                content_text = content or ""
            
            history_str += f"- [{role}]: {content_text}\n"
    else:
        history_str = "Nessun contesto precedente."

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    body = f"""
[Polpo Bot] Funzione richiesta non disponibile

CLIENT INFO:
- Client ID: {client_id}
- Telegram Chat ID: {chat_id}
- Timestamp: {timestamp}

MESSAGGIO OPERATORE:
"{message}"

CRONOLOGIA CONVERSAZIONE:
{history_str}
    """

    msg = MIMEMultipart()
    msg['From'] = smtp_user
    msg['To'] = admin_email
    msg['Subject'] = "[Polpo Bot] Funzione richiesta non disponibile"
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
        logger.info(f"Notifica funzione mancante inviata per chat_id {chat_id}")
    except Exception as e:
        logger.error(f"Errore invio email notifica: {e}")
