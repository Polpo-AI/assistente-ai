"""
onboard_client.py — Onboarding nuovo cliente Polpo AI (v3)

Due modalità:
  1. Interattiva (default):  python onboard_client.py
  2. Da file pre-compilato:  python onboard_client.py --file cliente.json

Il file JSON pre-compilato permette di non dover reinserire tutto da zero
ogni volta. Lo script mostra un riepilogo e chiede conferma prima di salvare.

Genera un file di esempio con:
    python onboard_client.py --genera-esempio

Nota: le keyword per intent sono state rimosse (classifier LLM-First v6).
"""

import json
import os
import sys
import argparse
import textwrap
from dotenv import load_dotenv
from database import get_client as get_db

load_dotenv()


# ─────────────────────────────────────────────
# Helpers UI
# ─────────────────────────────────────────────

def titolo(testo):
    print(f"\n{'='*55}")
    print(f"  {testo}")
    print(f"{'='*55}")

def sezione(testo):
    print(f"\n-- {testo.upper()} --")

def print_wrap(testo, indent="    ", width=80):
    if not testo: return
    wrapped = textwrap.fill(testo, width=width, initial_indent=indent, subsequent_indent=indent)
    print(wrapped)

def chiedi(domanda, default=""):
    hint = f" [{default}]" if default else ""
    risposta = input(f"  {domanda}{hint}: ").strip()
    return risposta if risposta else default

def chiedi_multiriga(domanda):
    print(f"  {domanda}")
    print("  (Più righe OK. Riga vuota per terminare.)")
    righe = []
    while True:
        riga = input("  > ").strip()
        if not riga:
            break
        righe.append(riga)
    return " ".join(righe)

def chiedi_lista(domanda, esempio=""):
    print(f"  {domanda}")
    if esempio:
        print(f"  (esempio: {esempio})")
    print("  Inserisci uno per riga. Invio vuoto per finire.")
    items = []
    while True:
        item = input("  -> ").strip()
        if not item:
            break
        items.append(item)
    return items

def chiedi_scelta(domanda, opzioni):
    print(f"\n  {domanda}")
    for i, op in enumerate(opzioni, 1):
        print(f"    {i}. {op}")
    while True:
        scelta = input("  Scelta: ").strip()
        if scelta.isdigit() and 1 <= int(scelta) <= len(opzioni):
            return opzioni[int(scelta) - 1]
        print("  Scelta non valida, riprova.")

def chiedi_priorita(intent):
    print(f"\n  Priorita per '{intent}':")
    print("    0. NON RISPONDERE -> nessuna bozza generata")
    print("    1. BASSA          -> risponde in autonomia")
    print("    2. MEDIA          -> Telegram con bottoni Approva/Modifica")
    print("    3. URGENTE        -> alert immediato")
    while True:
        scelta = input("  Scelta [0/1/2/3]: ").strip()
        if scelta in ("0", "1", "2", "3"):
            return int(scelta)
        print("  Inserisci 0, 1, 2 o 3.")

def conferma(domanda):
    risposta = input(f"\n  {domanda} [s/n]: ").strip().lower()
    return risposta in ("s", "si", "y", "yes")


# ─────────────────────────────────────────────
# Costanti
# ─────────────────────────────────────────────

INTENTI_PER_SETTORE = {
    "automotive":     ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "sanitario":      ["visita", "urgenza", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "legale":         ["consulenza", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "edilizia":       ["preventivo", "sopralluogo", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "commercialista": ["dichiarazione", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "altro":          ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
}

PRIORITA_DEFAULT = {
    "preventivo": 2, "appuntamento": 2, "visita": 2, "sopralluogo": 2,
    "consulenza": 2, "dichiarazione": 2, "urgenza": 3, "reclamo": 3,
    "informazione": 1, "pagamento": 2, "spam": 0, "cortesia": 0, "altro": 2,
}

GIORNI = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]


# ─────────────────────────────────────────────
# LLM helpers
# ─────────────────────────────────────────────

def suggerisci_config_settore(settore, descrizione="", servizi=""):
    """Usa Claude per suggerire intenti e istruzioni per settore."""
    try:
        from anthropic import Anthropic
        from models_config import ONBOARDING_SECTOR_SUGGESTER_MODEL
        client = Anthropic()

        contesto = ""
        if descrizione: contesto += f"\nDescrizione azienda: {descrizione}"
        if servizi:     contesto += f"\nServizi offerti: {servizi}"

        response = client.messages.create(
            model=ONBOARDING_SECTOR_SUGGESTER_MODEL,
            max_tokens=3000,
            system=(
                "Sei un esperto di customer service automation per Polpo AI. "
                "Rispondi SOLO con JSON valido:\n"
                '{"intent_list": ["intent1", ...], "intent_instructions": {"intent1": "istruzione sintetica"}}\n'
                "Nessun testo fuori dal JSON."
            ),
            messages=[{"role": "user", "content": (
                f"Genera configurazione ottimale per azienda nel settore '{settore}'.{contesto}\n"
                "5-7 intenti specifici per questa azienda. "
                "Istruzioni max 2 frasi. "
                "Tieni conto dei servizi per definire out-of-scope."
            )}]
        )
        testo = response.content[0].text.strip()
        s, e = testo.find("{"), testo.rfind("}") + 1
        if s == -1 or e == 0: raise ValueError("Nessun JSON")
        return json.loads(testo[s:e])
    except Exception as ex:
        print(f"  (Suggerimento AI non disponibile: {ex})")
        return None


# ─────────────────────────────────────────────
# Upload logo e template su Supabase Storage
# ─────────────────────────────────────────────

def upload_asset(client_id: str, asset_type: str, file_path: str) -> str:
    """
    Carica logo o template su Supabase Storage.
    asset_type: "logo" | "template"
    Restituisce il path storage o "" in caso di errore.
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    try:
        db        = get_db()
        ext       = os.path.splitext(file_path)[1].lower()
        storage_path = f"clients/{client_id}/{asset_type}{ext}"

        with open(file_path, "rb") as f:
            data = f.read()

        mime_map = {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc":  "application/msword",
        }
        mime = mime_map.get(ext, "application/octet-stream")

        db.storage.from_("client-assets").upload(
            path=storage_path,
            file=data,
            file_options={"content-type": mime, "upsert": "true"},
        )
        print(f"  ✅ {asset_type.capitalize()} caricato: {storage_path}")
        return storage_path

    except Exception as e:
        print(f"  ⚠️  Errore upload {asset_type}: {e}")
        return ""


def chiedi_asset_upload(client_id: str, asset_type: str, label: str) -> str:
    """Chiede il percorso del file e lo carica su Supabase Storage."""
    print(f"\n  {label}")
    print(f"  Inserisci il percorso completo del file (es: /home/user/{asset_type}.png)")
    print(f"  Lascia vuoto per saltare.")
    path = input("  Percorso: ").strip()
    if not path:
        return ""
    if not os.path.exists(path):
        print(f"  ⚠️  File non trovato: {path}")
        return ""
    return upload_asset(client_id, asset_type, path)


# ─────────────────────────────────────────────
# Raccolta dati interattiva
# ─────────────────────────────────────────────

def raccogli_dati() -> dict:
    titolo("Polpo AI — Onboarding Nuovo Cliente (v3)")
    print("\n  Questo script crea la configurazione del cliente su Supabase.")
    print("  Tempo stimato: 10-15 minuti.\n")

    dati = {}

    # ── 1. PROFILO ──────────────────────────────
    sezione("1. Profilo Azienda")
    dati["name"]   = chiedi("Nome azienda")
    dati["sector"] = chiedi("Settore (es: Pasticceria Artigianale, Studio Legale)")

    print("\n  Descrizione azienda (chi siete, da quanto operate, cosa vi distingue):")
    dati["business_description"] = chiedi_multiriga(
        "Descrizione (es: Officina meccanica specializzata in auto d'epoca, fondata nel 1985)"
    )

    print("\n  Servizi offerti — definisce cosa è in-scope e out-of-scope per il bot:")
    dati["services_offered"] = chiedi_multiriga(
        "Servizi (es: tagliando, revisione, carrozzeria, gomme — NO elettrauto)"
    )

    lingue = ["Italiano", "Italiano + Inglese", "Inglese", "Altro"]
    dati["language"] = chiedi_scelta("Lingua delle risposte:", lingue)

    # ── 2. STILE ─────────────────────────────
    sezione("2. Stile Comunicazione")
    tono_opzioni = [
        "professionale e cordiale",
        "formale e preciso",
        "friendly e informale",
        "tecnico e diretto",
    ]
    dati["llm_tone"] = chiedi_scelta("Tono delle risposte:", tono_opzioni)
    firma_nome = chiedi("Nome/ragione sociale per la firma", dati["name"])
    dati["signature"] = f"Cordiali saluti,\n{firma_nome}"

    servizi_str = f" Servizi offerti: {dati['services_offered']}." if dati.get("services_offered") else ""
    desc_str    = f" {dati['business_description']}." if dati.get("business_description") else ""
    persona_auto = (
        f"Sei il classificatore email di {dati['name']}, "
        f"un'azienda nel settore {dati['sector']}.{desc_str}{servizi_str} "
        f"Analizza le email in arrivo e classificale con precisione. "
        f"Considera out-of-scope qualsiasi richiesta di servizi non inclusi nell'elenco."
    )
    print("\n  [ PERSONA IA GENERATA ]")
    print_wrap(persona_auto)
    if conferma("Va bene questa persona o vuoi modificarla?"):
        dati["llm_persona"] = persona_auto
    else:
        dati["llm_persona"] = chiedi("Inserisci persona personalizzata", persona_auto)

    # ── 3. TELEGRAM ──────────────────────────
    sezione("3. Telegram")
    print("\n  Scrivi a @userinfobot su Telegram per trovare il tuo Chat ID.")
    dati["telegram_chat_id"] = chiedi("Chat ID Telegram (opzionale)", "")

    # ── 4. EMAIL ─────────────────────────────
    sezione("4. Connessione Email (IMAP/SMTP)")
    dati["imap_host"]     = chiedi("IMAP Host", "imap.gmail.com")
    dati["imap_port"]     = int(chiedi("IMAP Port", "993"))
    dati["imap_user"]     = chiedi("IMAP User (tua email)")
    dati["imap_password"] = chiedi("IMAP Password (app password)")
    dati["smtp_host"]     = chiedi("SMTP Host", "smtp.gmail.com")
    dati["smtp_port"]     = int(chiedi("SMTP Port", "587"))
    dati["smtp_user"]     = dati["imap_user"]
    dati["smtp_password"] = dati["imap_password"]

    # ── 5. CONTATTI ──────────────────────────
    sezione("5. Contatti Azienda")
    print("\n  Usati al posto dei [PLACEHOLDER] nelle bozze. Lascia vuoto se non applicabile.\n")
    dati["contacts"] = {k: v for k, v in {
        "telefono":             chiedi("Telefono principale", ""),
        "cellulare":            chiedi("Cellulare/WhatsApp", ""),
        "email":                chiedi("Email di risposta", ""),
        "indirizzo":            chiedi("Indirizzo fisico", ""),
        "sito_web":             chiedi("Sito web", ""),
        "prenotazione_online":  chiedi("Link prenotazione online", ""),
    }.items() if v}

    # ── 6. ORARI ─────────────────────────────
    sezione("6. Orari di Lavoro")
    print("\n  Formato: HH:MM - HH:MM (es: 09:00 - 18:00). Invio vuoto = chiuso.\n")
    orari = {}
    for giorno in GIORNI:
        orario = chiedi(f"  {giorno}", "").strip()
        if not orario:
            orari[giorno.lower()] = "chiuso"
        else:
            orari[giorno.lower()] = orario
    if conferma("C'è una pausa pranzo?"):
        orari["pausa_pranzo"] = chiedi("Orario pausa", "13:00 - 15:00")
    dati["orari"] = orari

    # ── 7. INTENTI ───────────────────────────
    sezione("7. Tipi di Email (Intenti)")
    print(f"\n  Genero suggerimenti per '{dati['sector']}'...")
    config_suggerita = suggerisci_config_settore(
        dati["sector"],
        descrizione=dati.get("business_description", ""),
        servizi=dati.get("services_offered", ""),
    )

    intent_instructions = {}
    if config_suggerita:
        print(f"\n  L'IA suggerisce questi intenti:")
        for i in config_suggerita["intent_list"]:
            print(f"    - {i}")
        if conferma("Usi questi suggerimenti?"):
            intenti             = config_suggerita["intent_list"]
            intent_instructions = config_suggerita["intent_instructions"]
        else:
            config_suggerita = None

    if not config_suggerita:
        intenti = chiedi_lista("Intenti:", "preventivo, appuntamento, info, reclamo, pagamento, spam, altro")
        for obbligatorio in ("spam", "cortesia", "altro"):
            if obbligatorio not in intenti:
                intenti.append(obbligatorio)

    dati["intent_list"] = intenti

    # ── 8. PRIORITÀ ──────────────────────────
    sezione("8. Priorità per Ogni Tipo")
    print("\n  0=non rispondere  1=BASSA autonomo  2=MEDIA Telegram  3=URGENTE alert\n")
    priority_map = {}
    for intent in intenti:
        if intent in ("spam", "cortesia"):
            priority_map[intent] = 0
            print(f"  '{intent}' -> NON RISPONDERE (automatico)")
            continue
        if intent == "altro":
            priority_map[intent] = 1
            print(f"  'altro' -> BASSA (automatico)")
            continue
        default_p = PRIORITA_DEFAULT.get(intent, 2)
        label     = {0: "NON RISPONDERE", 1: "BASSA", 2: "MEDIA", 3: "URGENTE"}.get(default_p)
        print(f"\n  [{intent.upper()}] — default: {label}")
        if conferma(f"  Confermi il default per '{intent}'?"):
            priority_map[intent] = default_p
        else:
            priority_map[intent] = chiedi_priorita(intent)
    dati["priority_map"] = priority_map

    # ── 9. ISTRUZIONI RISPOSTA ───────────────
    sezione("9. Istruzioni Risposta per Ogni Tipo")
    if not intent_instructions:
        print("\n  Cosa deve fare la bozza per ogni tipo. Lascia vuoto per il default Polpo AI.\n")
        for intent in intenti:
            if intent in ("spam", "cortesia", "altro"):
                continue
            print(f"\n  [{intent.upper()}]")
            istruzione = chiedi(f"Istruzione per '{intent}' (vuoto = default)", "")
            if istruzione:
                intent_instructions[intent] = istruzione
    else:
        print("\n  Istruzioni suggerite dall'IA:")
        for intent, istr in intent_instructions.items():
            print(f"    [{intent}]: {istr}")
        if not conferma("Vuoi modificarne alcune?"):
            pass
        else:
            for intent in intenti:
                if intent in ("spam", "cortesia", "altro"):
                    continue
                attuale = intent_instructions.get(intent, "")
                print(f"\n  [{intent.upper()}] — attuale: {attuale or '(default)'}")
                nuova = chiedi(f"  Nuova istruzione (invio = mantieni)", "")
                if nuova:
                    intent_instructions[intent] = nuova
    dati["intent_instructions"] = intent_instructions

    # ── 10. FAQ ──────────────────────────────
    sezione("10. FAQ (opzionale)")
    print("\n  Domande frequenti con risposta pronta.")
    print("  Il bot le include nelle bozze quando pertinenti.\n")
    faq = []
    if conferma("Vuoi configurare delle FAQ?"):
        while True:
            domanda = chiedi("  Domanda", "")
            if not domanda:
                break
            risposta = chiedi("  Risposta", "")
            if risposta:
                faq.append({"domanda": domanda, "risposta": risposta})
            if not conferma("  Aggiungi un'altra FAQ?"):
                break
    dati["faq"] = faq

    # Note: keyword per intent rimosse — il classifier è ora LLM-First (v6)
    # Non servono più regole keyword — Haiku classifica dal contesto.
    dati["intent_keywords"]      = {}
    dati["custom_spam_keywords"] = []

    return dati


# ─────────────────────────────────────────────
# Riepilogo
# ─────────────────────────────────────────────

def mostra_riepilogo(dati):
    titolo(f"RIEPILOGO — {dati['name']}")
    print(f"\n  Settore  : {dati['sector']}")
    print(f"  Lingua   : {dati['language']}")
    print(f"  Tono     : {dati['llm_tone']}")
    print(f"  Firma    : {dati['signature'].replace(chr(10), ' / ')}")

    if dati.get("business_description"):
        print(f"\n  Descrizione azienda:")
        print_wrap(dati["business_description"])
    if dati.get("services_offered"):
        print(f"\n  Servizi offerti:")
        print_wrap(dati["services_offered"])
    if dati.get("contacts"):
        print(f"\n  Contatti:")
        for k, v in dati["contacts"].items():
            print(f"    - {k}: {v}")
    if dati.get("orari"):
        print(f"\n  Orari:")
        for g, o in dati["orari"].items():
            print(f"    - {g}: {o}")

    print(f"\n  Intenti e priorità:")
    for intent in dati["intent_list"]:
        p     = dati["priority_map"].get(intent, 1)
        label = {0: "NO REPLY", 1: "BASSA   ", 2: "MEDIA   ", 3: "URGENTE"}.get(p, "BASSA   ")
        istr  = dati["intent_instructions"].get(intent, "(default Polpo AI)")
        print(f"    - {intent:<15} -> {label} | {istr[:50]}")

    if dati.get("faq"):
        print(f"\n  FAQ: {len(dati['faq'])} domande configurate")

    if dati.get("logo_path"):
        print(f"\n  Logo     : {dati['logo_path']}")
    if dati.get("template_path"):
        print(f"\n  Template : {dati['template_path']}")


# ─────────────────────────────────────────────
# Salvataggio su Supabase
# ─────────────────────────────────────────────

def rileva_ultimo_uid(imap_host: str, imap_port: int, imap_user: str, imap_password: str) -> int:
    """
    Si connette all'IMAP e recupera l'ultimo UID esistente.
    Così il worker parte solo dalle email NUOVE, non riprocessa tutto.
    Restituisce 0 in caso di errore.
    """
    import imaplib
    try:
        password = imap_password.replace(" ", "")
        host = imap_host.split(":")[0]

        print("\n  Connessione IMAP per rilevare ultimo UID...")
        if imap_port == 993:
            mail = imaplib.IMAP4_SSL(host, imap_port)
        else:
            mail = imaplib.IMAP4(host, imap_port)

        mail.login(imap_user, password)
        mail.select("INBOX")
        _, data = mail.search(None, "ALL")
        mail.logout()

        if data and data[0]:
            uids = data[0].split()
            if uids:
                last_uid = int(uids[-1])
                print(f"  ✅ Ultimo UID rilevato: {last_uid} — il bot partirà dalle email successive")
                return last_uid

        print("  ℹ️  Nessuna email in INBOX — il bot partirà da zero")
        return 0

    except Exception as e:
        print(f"  ⚠️  Impossibile rilevare UID IMAP: {e}")
        print(f"  ℹ️  imap_last_uid impostato a 0 — verifica manualmente se necessario")
        return 0


def salva_cliente(dati: dict) -> str:
    db = get_db()
    result = db.table("clients").insert({
        "name":                 dati["name"],
        "sector":               dati["sector"],
        "business_description": dati.get("business_description", ""),
        "services_offered":     dati.get("services_offered", ""),
        "llm_persona":          dati["llm_persona"],
        "llm_tone":             dati["llm_tone"],
        "signature":            dati["signature"],
        "intent_list":          dati["intent_list"],
        "priority_map":         dati.get("priority_map", {}),
        "custom_spam_keywords": dati.get("custom_spam_keywords", []),
        "intent_keywords":      dati.get("intent_keywords", {}),
        "intent_instructions":  dati.get("intent_instructions", {}),
        "contacts":             dati.get("contacts", {}),
        "orari":                dati.get("orari", {}),
        "faq":                  dati.get("faq", []),
        "language":             dati.get("language", "Italiano"),
        "telegram_chat_id":     dati.get("telegram_chat_id") or None,
        "imap_host":            dati.get("imap_host"),
        "imap_port":            dati.get("imap_port"),
        "imap_user":            dati.get("imap_user"),
        "imap_password":        dati.get("imap_password"),
        "smtp_host":            dati.get("smtp_host"),
        "smtp_port":            dati.get("smtp_port"),
        "smtp_user":            dati.get("smtp_user"),
        "smtp_password":        dati.get("smtp_password"),
        "logo_storage_path":    "",   # aggiornato dopo upload
        "template_storage_path": "",  # aggiornato dopo upload
        "imap_last_uid":         0,   # aggiornato subito dopo
    }).execute()
    client_id = result.data[0]["id"]

    # Rileva ultimo UID IMAP e aggiorna subito — così il bot non rilegge tutto
    last_uid = rileva_ultimo_uid(
        dati.get("imap_host", ""),
        dati.get("imap_port", 993),
        dati.get("imap_user", ""),
        dati.get("imap_password", ""),
    )
    if last_uid > 0:
        db.table("clients").update({"imap_last_uid": last_uid}).eq("id", client_id).execute()

    return client_id


def aggiorna_asset_paths(client_id: str, logo_path: str, template_path: str) -> None:
    """Aggiorna i path di logo e template dopo il salvataggio del cliente."""
    updates = {}
    if logo_path:     updates["logo_storage_path"]     = logo_path
    if template_path: updates["template_storage_path"] = template_path
    if updates:
        get_db().table("clients").update(updates).eq("id", client_id).execute()


# ─────────────────────────────────────────────
# Genera file di esempio JSON
# ─────────────────────────────────────────────

ESEMPIO_JSON = {
    "name":                 "Officina Rossi",
    "sector":               "Automotive",
    "business_description": "Officina meccanica specializzata in auto d'epoca e sportive, fondata nel 1990. Situata a Milano.",
    "services_offered":     "tagliando, revisione, riparazione motore, carrozzeria, gomme, diagnosi elettronica. NON facciamo retrofit EV.",
    "language":             "Italiano",
    "llm_tone":             "professionale e cordiale",
    "signature":            "Cordiali saluti,\nOfficina Rossi",
    "llm_persona":          "",
    "telegram_chat_id":     "123456789",
    "imap_host":            "imap.gmail.com",
    "imap_port":            993,
    "imap_user":            "officina@gmail.com",
    "imap_password":        "app-password-qui",
    "smtp_host":            "smtp.gmail.com",
    "smtp_port":            587,
    "contacts": {
        "telefono":  "02 1234567",
        "cellulare": "+39 333 1234567",
        "email":     "officina@gmail.com",
        "indirizzo": "Via Roma 1, 20100 Milano",
        "sito_web":  "www.officinaROSSI.it",
    },
    "orari": {
        "lunedi":    "08:30 - 18:00",
        "martedi":   "08:30 - 18:00",
        "mercoledi": "08:30 - 18:00",
        "giovedi":   "08:30 - 18:00",
        "venerdi":   "08:30 - 17:00",
        "sabato":    "09:00 - 13:00",
        "domenica":  "chiuso",
        "pausa_pranzo": "13:00 - 14:30",
    },
    "intent_list": ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "priority_map": {
        "preventivo":  2,
        "appuntamento": 2,
        "informazione": 1,
        "reclamo":      3,
        "pagamento":    2,
        "spam":         0,
        "cortesia":     0,
        "altro":        1,
    },
    "intent_instructions": {
        "preventivo":   "Chiedi marca, modello, anno e descrizione del problema. Non quotare prezzi senza diagnosi.",
        "appuntamento": "Proponi disponibilità nella settimana successiva. Conferma via email.",
        "informazione": "Rispondi in modo chiaro e conciso. Rimanda al sito per info generali.",
        "reclamo":      "Esprimi dispiacere, chiedi dettagli e proponi soluzione concreta entro 24h.",
        "pagamento":    "Indica IBAN e riferimento fattura. Conferma ricezione pagamento.",
    },
    "faq": [
        {"domanda": "Fate riparazioni su auto elettriche?", "risposta": "Al momento non effettuiamo interventi su veicoli elettrici o ibridi plug-in."},
        {"domanda": "Quanto dura un tagliando?", "risposta": "Un tagliando standard richiede circa 2-3 ore. Per veicoli d'epoca potrebbe richiedere più tempo."},
    ],
    "logo_path":    "",
    "template_path": "",
}


def genera_esempio():
    filename = "onboarding_esempio.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(ESEMPIO_JSON, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ File di esempio generato: {filename}")
    print(f"  Modifica i campi e poi esegui:")
    print(f"  python onboard_client.py --file {filename}\n")


# ─────────────────────────────────────────────
# Caricamento da file JSON
# ─────────────────────────────────────────────

def carica_da_file(filepath: str) -> dict:
    """Carica i dati di onboarding da un file JSON pre-compilato."""
    if not os.path.exists(filepath):
        print(f"\n  ❌ File non trovato: {filepath}")
        sys.exit(1)
    with open(filepath, encoding="utf-8") as f:
        dati = json.load(f)

    # Genera la persona automaticamente se non specificata
    if not dati.get("llm_persona"):
        servizi_str = f" Servizi offerti: {dati.get('services_offered', '')}." if dati.get("services_offered") else ""
        desc_str    = f" {dati.get('business_description', '')}." if dati.get("business_description") else ""
        dati["llm_persona"] = (
            f"Sei il classificatore email di {dati['name']}, "
            f"un'azienda nel settore {dati.get('sector', '')}.{desc_str}{servizi_str} "
            f"Considera out-of-scope qualsiasi richiesta di servizi non inclusi nell'elenco."
        )

    # Campi opzionali con default
    dati.setdefault("intent_keywords", {})
    dati.setdefault("custom_spam_keywords", [])
    dati.setdefault("faq", [])
    dati.setdefault("contacts", {})
    dati.setdefault("orari", {})
    dati.setdefault("telegram_chat_id", "")
    dati.setdefault("logo_path", "")
    dati.setdefault("template_path", "")

    return dati


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polpo AI — Onboarding cliente")
    parser.add_argument("--file",           metavar="FILE.json", help="Carica dati da file JSON pre-compilato")
    parser.add_argument("--genera-esempio", action="store_true", help="Genera un file JSON di esempio e termina")
    args = parser.parse_args()

    if args.genera_esempio:
        genera_esempio()
        sys.exit(0)

    try:
        if args.file:
            titolo("Polpo AI — Onboarding da File JSON")
            print(f"\n  Carico configurazione da: {args.file}")
            dati = carica_da_file(args.file)
        else:
            dati = raccogli_dati()

        mostra_riepilogo(dati)

        if not conferma("Confermi e salvi il cliente su Supabase?"):
            print("\n  Operazione annullata. Nessun dato salvato.")
            sys.exit(0)

        print("\n  Salvataggio in corso...")
        client_id = salva_cliente(dati)

        # Upload logo e template se specificati
        logo_storage     = ""
        template_storage = ""
        logo_path        = dati.get("logo_path", "")
        template_path    = dati.get("template_path", "")

        if logo_path:
            logo_storage = upload_asset(client_id, "logo", logo_path)
        elif not args.file and conferma("Vuoi caricare il logo aziendale ora?"):
            logo_storage = chiedi_asset_upload(client_id, "logo", "Logo aziendale (PNG/JPG)")

        if template_path:
            template_storage = upload_asset(client_id, "template", template_path)
        elif not args.file and conferma("Vuoi caricare il template Word (.docx) ora?"):
            template_storage = chiedi_asset_upload(client_id, "template", "Template Word (.docx)")

        if logo_storage or template_storage:
            aggiorna_asset_paths(client_id, logo_storage, template_storage)

        titolo("CLIENTE CREATO CON SUCCESSO 🐙")
        print(f"\n  Nome      : {dati['name']}")
        print(f"  Client ID : {client_id}")
        if logo_storage:
            print(f"  Logo      : {logo_storage}")
        if template_storage:
            print(f"  Template  : {template_storage}")
        print(f"\n  Per aggiornare logo/template in seguito:")
        print(f"  python onboard_client.py --file <file.json>  (con logo_path/template_path aggiornati)\n")

    except KeyboardInterrupt:
        print("\n\n  Onboarding interrotto.\n")
    except Exception as e:
        import traceback
        print(f"\n  ❌ Errore: {e}")
        traceback.print_exc()
