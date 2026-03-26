"""
attachment_reader.py — Estrazione testo da allegati email (v2)

Pipeline per ogni allegato:
  1. Pre-screening Haiku (nome + mime + size) → decide se estrarre, skippare o rimandare
     - Allegati inutili (loghi, banner, immagini decorative) → SKIP senza LLM
     - Allegati utili piccoli (<1MB) → estrai silenziosamente
     - Allegati utili medi (1-4MB) → estrai + notifica su Telegram "allegato grande letto"
     - Allegati utili grandi (>4MB) → NON estrarre, chiedi conferma su Telegram

  2. Estrazione per tipo:
     - PDF (anche scansionati) → Claude Sonnet via API nativa
     - DOCX                    → python-docx
     - XLSX                    → openpyxl

  3. Salvataggio nel DB (emails.attachments_text jsonb)

Dipendenze:
    pip install python-docx openpyxl
"""

import base64
import logging
import json
import os
from typing import Optional
from anthropic import Anthropic
from db.connection import get_client as get_db

logger = logging.getLogger("polpo.attachment_reader")

# ─────────────────────────────────────────────
# Soglie dimensione
# ─────────────────────────────────────────────

SIZE_SILENT_MB   = 1      # < 1MB  → estrai silenziosamente
SIZE_NOTIFY_MB   = 4      # 1-4MB  → estrai + notifica Telegram
# > 4MB → chiedi conferma su Telegram prima di estrarre

SIZE_SILENT  = SIZE_SILENT_MB * 1024 * 1024
SIZE_NOTIFY  = SIZE_NOTIFY_MB * 1024 * 1024

# Estensioni decorative — skippate senza LLM (loghi, banner, font, ecc.)
# Le foto inviate dai clienti (jpg, png) NON sono qui — vengono processate da Sonnet
SKIP_EXTENSIONS = {
    ".svg", ".ico",
    ".mp3", ".mp4", ".wav", ".avi",
    ".ttf", ".otf", ".woff", ".woff2",
    ".css", ".js",
}

# Tipi MIME supportati per l'estrazione
SUPPORTED_TYPES = {
    # Documenti
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/plain",
    # Immagini — processate con visione Sonnet
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Estensioni immagine supportate (foto clienti, documenti scansionati)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


# ─────────────────────────────────────────────
# Pre-screening Haiku
# ─────────────────────────────────────────────

# Risultati possibili dello screening
SCREEN_EXTRACT  = "extract"    # estrai subito
SCREEN_SKIP     = "skip"       # non estrarre, inutile
SCREEN_ASK      = "ask"        # chiedi conferma su Telegram (troppo grande)
SCREEN_NOTIFY   = "notify"     # estrai ma notifica su Telegram (grande ma nei limiti)


def _quick_skip(filename: str, mime_type: str) -> bool:
    """
    Controllo rapido senza LLM — skippa allegati palesemente inutili.
    Le immagini NON vengono skippate qui: Haiku decide in base al contesto
    se è una foto del cliente (utile) o un logo/banner (decorativo).
    """
    mime = mime_type.lower().split(";")[0].strip()
    ext  = os.path.splitext(filename.lower())[1]

    # Skippa audio/video/font/css sempre
    skip_prefixes = ("audio/", "video/", "font/", "text/css", "text/javascript")
    if any(mime.startswith(p) for p in skip_prefixes):
        return True
    if ext in SKIP_EXTENSIONS:
        return True
    return False


def screen_attachment(
    filename: str,
    mime_type: str,
    size_bytes: int,
    anthropic_client: Anthropic,
    email_subject: str = "",
    email_body_preview: str = "",
) -> tuple[str, str]:
    """
    Pre-screening di un allegato con Haiku.
    Restituisce (decision, reason):
      decision: SCREEN_EXTRACT | SCREEN_SKIP | SCREEN_ASK | SCREEN_NOTIFY
      reason:   stringa leggibile per log/Telegram

    Prima controlla con regex veloci, poi se necessario chiede a Haiku.
    """
    from models_config import CLASSIFIER_MODEL

    # 1. Controllo rapido senza LLM
    if _quick_skip(filename, mime_type):
        return SCREEN_SKIP, f"Tipo non processabile: {mime_type}"

    mime = mime_type.lower().split(";")[0].strip()
    if mime not in SUPPORTED_TYPES and not any(
        filename.lower().endswith(ext) for ext in (".pdf", ".docx", ".xlsx", ".txt")
    ):
        return SCREEN_SKIP, f"Formato non supportato: {filename}"

    # 2. Screening Haiku — capisce dal contesto se l'allegato è utile
    size_kb = size_bytes / 1024
    try:
        response = anthropic_client.messages.create(
            model=CLASSIFIER_MODEL,
            max_tokens=100,
            system=(
                "Sei un filtro per allegati email aziendali. "
                "Rispondi SOLO con JSON: {\"useful\": true/false, \"reason\": \"max 60 caratteri\"}"
            ),
            messages=[{"role": "user", "content": (
                f"Allegato: '{filename}' ({mime_type}, {size_kb:.0f} KB)\n"
                f"Oggetto email: {email_subject or '—'}\n"
                f"Corpo email (preview): {email_body_preview[:300] or '—'}\n\n"
                "Questo allegato contiene informazioni utili per rispondere all'email "
                "(es: preventivo, contratto, fattura, documento tecnico, richiesta dettagliata)? "
                "Oppure è decorativo/pubblicitario (logo, banner, firma grafica, newsletter)?"
            )}]
        )
        raw  = response.content[0].text.strip()
        import re
        m    = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0) if m else raw)
        useful = data.get("useful", True)
        reason = data.get("reason", "")
    except Exception as e:
        logger.warning("attachment_reader | Haiku screening fallito per '%s': %s — assumo utile", filename, e)
        useful = True
        reason = "screening fallito, estratto per sicurezza"

    if not useful:
        return SCREEN_SKIP, reason

    # 3. Decisione in base alla dimensione
    if size_bytes <= SIZE_SILENT:
        return SCREEN_EXTRACT, reason
    elif size_bytes <= SIZE_NOTIFY:
        return SCREEN_NOTIFY, f"{filename} ({size_bytes/1024/1024:.1f} MB) — {reason}"
    else:
        return SCREEN_ASK, f"{filename} ({size_bytes/1024/1024:.1f} MB) — {reason}"


# ─────────────────────────────────────────────
# Estrazione per tipo
# ─────────────────────────────────────────────

def extract_pdf(data: bytes, anthropic_client: Anthropic) -> str:
    """
    Estrae testo da un PDF usando Claude Sonnet con visione nativa.
    Funziona anche su PDF scansionati (immagini).
    """
    from models_config import RESPONDER_MODEL
    b64 = base64.standard_b64encode(data).decode("utf-8")
    try:
        response = anthropic_client.messages.create(
            model=RESPONDER_MODEL,
            max_tokens=4000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type":       "base64",
                            "media_type": "application/pdf",
                            "data":       b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Estrai tutto il testo da questo documento in modo fedele. "
                            "Mantieni la struttura logica (tabelle, elenchi, sezioni). "
                            "Non aggiungere commenti o interpretazioni. "
                            "Restituisci solo il testo estratto."
                        ),
                    },
                ],
            }]
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.error("attachment_reader | Errore estrazione PDF: %s", e)
        return ""


def extract_docx(data: bytes) -> str:
    """Estrae testo da un file .docx usando python-docx."""
    try:
        import io
        from docx import Document
        doc        = Document(io.BytesIO(data))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    paragraphs.append(row_text)
        return "\n".join(paragraphs)
    except Exception as e:
        logger.error("attachment_reader | Errore estrazione DOCX: %s", e)
        return ""


def extract_xlsx(data: bytes) -> str:
    """Estrae testo da un file .xlsx usando openpyxl."""
    try:
        import io
        import openpyxl
        wb    = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            lines.append(f"[Foglio: {sheet_name}]")
            for row in ws.iter_rows(values_only=True):
                row_text = " | ".join(str(cell) for cell in row if cell is not None)
                if row_text.strip():
                    lines.append(row_text)
        return "\n".join(lines)
    except Exception as e:
        logger.error("attachment_reader | Errore estrazione XLSX: %s", e)
        return ""


def extract_image(data: bytes, mime_type: str, anthropic_client: Anthropic) -> str:
    """
    Descrive il contenuto di un'immagine usando Claude Sonnet con visione nativa.
    Ottimizzato per immagini inviate dai clienti: danni, documenti, parti di ricambio, ecc.
    """
    from models_config import RESPONDER_MODEL

    mime_map = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".gif": "image/gif",
        ".webp": "image/webp",
    }
    # Normalizza il mime type
    mime = mime_type.lower().split(";")[0].strip()
    if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        mime = "image/jpeg"  # fallback

    b64 = base64.standard_b64encode(data).decode("utf-8")
    try:
        response = anthropic_client.messages.create(
            model=RESPONDER_MODEL,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": mime,
                            "data":       b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "Descrivi cosa mostra questa immagine in modo dettagliato e utile "
                            "per rispondere a un'email di un cliente. "
                            "Se mostri un danno o problema: descrivi tipo, gravità e zona interessata. "
                            "Se è un documento: trascrivi il testo leggibile. "
                            "Se è un prodotto o ricambio: identifica cosa è e le caratteristiche visibili. "
                            "Se è decorativa (logo, banner, grafica generica): rispondi solo con '[immagine decorativa]'. "
                            "Sii conciso e fattuale."
                        ),
                    },
                ],
            }]
        )
        result = response.content[0].text.strip()
        # Se Sonnet identifica immagine decorativa, la trattiamo come skip
        if result.lower() == "[immagine decorativa]":
            return ""
        return result
    except Exception as e:
        logger.error("attachment_reader | Errore estrazione immagine: %s", e)
        return ""


def extract_attachment_text(
    filename:         str,
    mime_type:        str,
    data:             bytes,
    anthropic_client: Anthropic,
) -> Optional[str]:
    """
    Estrae testo da un allegato in base al tipo MIME.
    Presuppone che lo screening sia già stato fatto — qui si estrae e basta.
    """
    mime_type = mime_type.lower().split(";")[0].strip()

    if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
        logger.info("attachment_reader | Estrazione PDF: %s (%d bytes)", filename, len(data))
        return extract_pdf(data, anthropic_client)

    if (mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or filename.lower().endswith(".docx")):
        logger.info("attachment_reader | Estrazione DOCX: %s (%d bytes)", filename, len(data))
        return extract_docx(data)

    if (mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            or filename.lower().endswith(".xlsx")):
        logger.info("attachment_reader | Estrazione XLSX: %s (%d bytes)", filename, len(data))
        return extract_xlsx(data)

    if mime_type == "text/plain" or filename.lower().endswith(".txt"):
        return data.decode("utf-8", errors="replace")

    # Immagini — visione Sonnet
    ext = os.path.splitext(filename.lower())[1]
    if mime_type in IMAGE_MIME_TYPES or ext in IMAGE_EXTENSIONS:
        logger.info("attachment_reader | Estrazione immagine: %s (%d bytes)", filename, len(data))
        return extract_image(data, mime_type, anthropic_client)

    logger.debug("attachment_reader | Tipo non supportato: %s (%s)", filename, mime_type)
    return None


# ─────────────────────────────────────────────
# Dispatcher principale — usato dal worker
# ─────────────────────────────────────────────

def process_email_attachments(
    email_id:         str,
    raw_attachments:  list[dict],
    anthropic_client: Anthropic,
    email_subject:    str = "",
    email_body:       str = "",
) -> dict:
    """
    Processa tutti gli allegati di un'email con pipeline completa:
      1. Pre-screening Haiku per ogni allegato
      2. Estrazione testo per quelli approvati
      3. Salvataggio nel DB
      4. Ritorna anche metadati per notifiche Telegram

    raw_attachments: lista di dict con chiavi:
        - filename:  str
        - mime_type: str
        - data:      bytes

    Ritorna dict con:
        - extracted:  { filename: testo }  — allegati estratti
        - skipped:    [ filename ]          — allegati skippati (inutili)
        - to_ask:     [ {filename, reason} ] — allegati troppo grandi, aspettano conferma TG
        - notified:   [ {filename, reason} ] — estratti ma da notificare su TG
    """
    if not raw_attachments:
        return {"extracted": {}, "skipped": [], "to_ask": [], "notified": []}

    extracted = {}
    skipped   = []
    to_ask    = []
    notified  = []

    for att in raw_attachments:
        filename  = att.get("filename", "allegato")
        mime_type = att.get("mime_type", "")
        data      = att.get("data", b"")

        if not data:
            continue

        size_bytes = len(data)

        # Screening
        decision, reason = screen_attachment(
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            anthropic_client=anthropic_client,
            email_subject=email_subject,
            email_body_preview=email_body[:300],
        )

        if decision == SCREEN_SKIP:
            logger.info("attachment_reader | SKIP '%s': %s", filename, reason)
            skipped.append(filename)
            continue

        if decision == SCREEN_ASK:
            logger.info("attachment_reader | ASK '%s' (%d MB): %s",
                        filename, size_bytes // 1024 // 1024, reason)
            to_ask.append({
                "filename":   filename,
                "mime_type":  mime_type,
                "size_bytes": size_bytes,
                "reason":     reason,
                "email_id":   email_id,
            })
            # Salva i bytes nel DB come pending per estrazione futura
            _save_pending_attachment(email_id, filename, mime_type, data)
            continue

        # SCREEN_EXTRACT o SCREEN_NOTIFY → estrai
        text = extract_attachment_text(filename, mime_type, data, anthropic_client)
        if text:
            extracted[filename] = text
            logger.info("attachment_reader | ✓ '%s' (%d chars)", filename, len(text))
            if decision == SCREEN_NOTIFY:
                notified.append({"filename": filename, "reason": reason})
        else:
            logger.warning("attachment_reader | Estrazione vuota per '%s'", filename)
            skipped.append(filename)

    if extracted:
        save_attachments_text(email_id, extracted)

    return {
        "extracted": extracted,
        "skipped":   skipped,
        "to_ask":    to_ask,
        "notified":  notified,
    }


def extract_pending_attachment(
    email_id:         str,
    filename:         str,
    anthropic_client: Anthropic,
) -> Optional[str]:
    """
    Estrae un allegato 'pending' (>4MB) dopo conferma su Telegram.
    Legge i bytes dal DB, estrae il testo, aggiorna attachments_text.
    """
    try:
        result = get_db().table("email_attachments_pending").select("*").eq(
            "email_id", email_id
        ).eq("filename", filename).execute()

        if not result.data:
            logger.warning("attachment_reader | Pending attachment non trovato: %s / %s", email_id, filename)
            return None

        row       = result.data[0]
        data      = base64.b64decode(row["data_b64"])
        mime_type = row["mime_type"]

        text = extract_attachment_text(filename, mime_type, data, anthropic_client)
        if text:
            # Aggiorna attachments_text nel DB
            existing = get_attachments_text(email_id)
            existing[filename] = text
            save_attachments_text(email_id, existing)
            # Rimuovi dal pending
            get_db().table("email_attachments_pending").delete().eq(
                "email_id", email_id
            ).eq("filename", filename).execute()
            logger.info("attachment_reader | ✓ Estratto pending '%s' (%d chars)", filename, len(text))
        return text

    except Exception as e:
        logger.error("attachment_reader | Errore estrazione pending '%s': %s", filename, e)
        return None


# ─────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────

def _save_pending_attachment(
    email_id:  str,
    filename:  str,
    mime_type: str,
    data:      bytes,
) -> None:
    """
    Salva un allegato grande in attesa di conferma Telegram.
    I bytes vengono salvati come base64 nella tabella email_attachments_pending.
    """
    try:
        get_db().table("email_attachments_pending").upsert({
            "email_id":  email_id,
            "filename":  filename,
            "mime_type": mime_type,
            "data_b64":  base64.b64encode(data).decode("utf-8"),
            "size_bytes": len(data),
        }).execute()
    except Exception as e:
        logger.error("attachment_reader | Errore salvataggio pending '%s': %s", filename, e)


def save_attachments_text(email_id: str, attachments_text: dict) -> None:
    """Salva il testo estratto degli allegati nel DB."""
    try:
        get_db().table("emails").update({
            "attachments_text": json.dumps(attachments_text, ensure_ascii=False)
        }).eq("id", email_id).execute()
    except Exception as e:
        logger.error("attachment_reader | Errore salvataggio DB: %s", e)


def get_attachments_text(email_id: str) -> dict:
    """Recupera il testo estratto degli allegati dal DB."""
    try:
        result = get_db().table("emails").select("attachments_text").eq("id", email_id).execute()
        if result.data and result.data[0].get("attachments_text"):
            val = result.data[0]["attachments_text"]
            if isinstance(val, dict):
                return val
            return json.loads(val)
    except Exception as e:
        logger.error("attachment_reader | Errore lettura DB: %s", e)
    return {}


def get_quoted_text_from_db(email_id: str) -> str:
    """Recupera il quoted text salvato nel DB per una email."""
    try:
        result = get_db().table("emails").select("quoted_text").eq("id", email_id).execute()
        if result.data:
            return result.data[0].get("quoted_text", "") or ""
    except Exception as e:
        logger.error("attachment_reader | Errore lettura quoted_text: %s", e)
    return ""


def format_attachments_for_prompt(
    attachments_text:  dict,
    max_chars_per_file: int = 3000,
) -> str:
    """Formatta il testo degli allegati per inserirlo in un prompt LLM."""
    if not attachments_text:
        return ""
    lines = ["--- ALLEGATI ---"]
    for filename, text in attachments_text.items():
        lines.append(f"\n[Allegato: {filename}]")
        if len(text) > max_chars_per_file:
            lines.append(text[:max_chars_per_file] + f"\n[...troncato, {len(text)} chars totali]")
        else:
            lines.append(text)
    lines.append("--- FINE ALLEGATI ---")
    return "\n".join(lines)
