"""
onboard_client.py - Onboarding nuovo cliente Polpo AI

Esegui dopo una call col cliente:
    python onboard_client.py
"""

import json
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
    print(f"\n-- {testo} --")

def chiedi(domanda, default=""):
    hint = f" [{default}]" if default else ""
    risposta = input(f"  {domanda}{hint}: ").strip()
    return risposta if risposta else default

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
    print("    0. NON RISPONDERE -> archiviata, nessuna bozza generata")
    print("    1. BASSA          -> risponde in autonomia")
    print("    2. MEDIA          -> WhatsApp con bottoni Approva/Modifica")
    print("    3. URGENTE        -> alert immediato al titolare")
    while True:
        scelta = input("  Scelta [0/1/2/3]: ").strip()
        if scelta in ("0", "1", "2", "3"):
            return int(scelta)
        print("  Inserisci 0, 1, 2 o 3.")

def conferma(domanda):
    risposta = input(f"\n  {domanda} [s/n]: ").strip().lower()
    return risposta in ("s", "si", "y", "yes")


# ─────────────────────────────────────────────
# Intenti default per settore
# ─────────────────────────────────────────────

INTENTI_PER_SETTORE = {
    "automotive":     ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "sanitario":      ["visita", "urgenza", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "legale":         ["consulenza", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "edilizia":       ["preventivo", "sopralluogo", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "commercialista": ["dichiarazione", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
    "altro":          ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "cortesia", "altro"],
}

SETTORI = list(INTENTI_PER_SETTORE.keys())

PRIORITA_DEFAULT = {
    "preventivo":    2,
    "appuntamento":  2,
    "visita":        2,
    "sopralluogo":   2,
    "consulenza":    2,
    "dichiarazione": 2,
    "urgenza":       3,
    "reclamo":       3,
    "informazione":  1,
    "pagamento":     2,
    "spam":          1,
    "cortesia":      0,
    "altro":         1,
}

GIORNI = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]


# ─────────────────────────────────────────────
# Suggerimento Configurazione per Settore via LLM
# ─────────────────────────────────────────────

def suggerisci_config_settore(settore):
    """
    Usa Claude per suggerire una lista di intenti e relative istruzioni 
    basandosi su un settore a testo libero.
    """
    try:
        from anthropic import Anthropic
        from models_config import ONBOARDING_SECTOR_SUGGESTER_MODEL
        client = Anthropic()
        response = client.messages.create(
            model=ONBOARDING_SECTOR_SUGGESTER_MODEL,
            max_tokens=3000,
            system=(
                "Sei un esperto di customer service automation per Polpo AI. "
                "Rispondi SOLO con un JSON valido strutturato così:\n"
                "{\n"
                "  \"intent_list\": [\"intent1\", \"intent2\", ...],\n"
                "  \"intent_instructions\": {\"intent1\": \"istruzione\", ...}\n"
                "}\n"
                "Sii conciso nelle istruzioni. Nessun testo fuori dal JSON."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Genera una configurazione ottimale per Polpo AI per un'azienda nel settore '{settore}'.\n"
                    "Includi 5-7 intenti comuni.\n"
                    "Ogni istruzione deve essere chiara ma SINTETICA (max 2 frasi)."
                )
            }]
        )
        testo = response.content[0].text.strip()
        # Estrae il blocco JSON anche se l'AI aggiunge testo attorno
        inizio = testo.find("{")
        fine = testo.rfind("}") + 1
        if inizio == -1 or fine == 0:
            raise ValueError("Nessun JSON trovato nella risposta AI")
        return json.loads(testo[inizio:fine])
    except Exception as e:
        print(f"  (Suggerimento AI non disponibile: {e})")
        return None


# ─────────────────────────────────────────────
# Suggerimento keyword per intent via LLM
# ─────────────────────────────────────────────

def suggerisci_keywords_per_intent(nome_azienda, settore, intent):
    """
    Genera keyword rappresentative per un dato intent tramite LLM.
    Usate come trigger per il classifier (Livello 2 - regole).
    """
    try:
        from anthropic import Anthropic
        from models_config import ONBOARDING_KEYWORDS_SUGGESTER_MODEL
        client = Anthropic()

        if intent == "spam":
            system_prompt = (
                "Sei un esperto di filtraggio email. "
                "Rispondi SOLO con un array JSON di stringhe. "
                "Nessun testo fuori dal JSON."
            )
            user_prompt = (
                f"Genera 8-10 keyword o frasi brevi tipiche dello SPAM "
                f"che potrebbero arrivare a '{nome_azienda}' nel settore '{settore}'. "
                f"Devono essere specifiche per il settore, non generiche. "
                f"Formato: [\"keyword 1\", \"keyword 2\", ...]"
            )
        else:
            system_prompt = (
                "Sei un esperto di classificazione email per aziende. "
                "Rispondi SOLO con un array JSON di stringhe. "
                "Nessun testo fuori dal JSON."
            )
            user_prompt = (
                f"Genera 8-10 keyword o frasi brevi che identificano email di tipo '{intent}' "
                f"per '{nome_azienda}', un'azienda nel settore '{settore}'. "
                f"Sono usate come trigger per classificare automaticamente le email in arrivo. "
                f"Devono essere specifiche per il settore e per questo tipo di email. "
                f"Formato: [\"keyword 1\", \"keyword 2\", ...]"
            )

        response = client.messages.create(
            model=ONBOARDING_KEYWORDS_SUGGESTER_MODEL,
            max_tokens=1000,
            system=system_prompt + " Sii sintetico. Rispondi solo in JSON.",
            messages=[{"role": "user", "content": user_prompt}]
        )
        testo = response.content[0].text.strip()
        inizio = testo.find("[")
        fine = testo.rfind("]") + 1
        if inizio == -1 or fine == 0:
            raise ValueError("Nessun JSON trovato nella risposta AI")
        keywords = json.loads(testo[inizio:fine])
        return [kw.lower() for kw in keywords if isinstance(kw, str)]
    except Exception as e:
        print(f"  (Suggerimento AI non disponibile: {e})")
        return []


def suggerisci_tutte_le_keywords(nome_azienda, settore, intenti):
    """
    Chiama suggerisci_keywords_per_intent per ogni intent nella lista.
    Restituisce un dict {intent: [keywords]}.
    """
    risultati = {}
    for intent in intenti:
        print(f"  Genero keyword per '{intent}'...", end=" ", flush=True)
        kw = suggerisci_keywords_per_intent(nome_azienda, settore, intent)
        risultati[intent] = kw
        print(f"OK ({len(kw)} keyword)")
    return risultati


# ─────────────────────────────────────────────
# Raccolta dati
# ─────────────────────────────────────────────

def raccogli_dati():
    titolo("Polpo AI - Onboarding Nuovo Cliente")
    print("\n  Questo script crea la configurazione del cliente su Supabase.")
    print("  Tempo stimato: 10-15 minuti.\n")

    dati = {}

    # ── PROFILO ──────────────────────────────
    sezione("1. PROFILO AZIENDA")
    dati["name"]   = chiedi("Nome azienda")
    dati["sector"] = chiedi("Settore di attivita (es: Pasticceria Artigianale, Studio Legale)")

    lingue = ["Italiano", "Italiano + Inglese", "Inglese", "Altro"]
    dati["language"] = chiedi_scelta("Lingua delle risposte:", lingue)

    # ── TONO E FIRMA ─────────────────────────
    sezione("2. STILE COMUNICAZIONE")
    tono_opzioni = [
        "professionale e cordiale",
        "formale e preciso",
        "friendly e informale",
        "tecnico e diretto",
    ]
    dati["llm_tone"] = chiedi_scelta("Tono delle risposte:", tono_opzioni)
    firma_nome = chiedi("Nome/ragione sociale per la firma", dati["name"])
    dati["signature"] = f"Cordiali saluti,\n{firma_nome}"
    dati["llm_persona"] = (
        f"Sei il classificatore email di {dati['name']}, "
        f"un'azienda nel settore {dati['sector']}. "
        f"Analizza le email in arrivo e classificale con precisione."
    )

    # ── TELEGRAM ─────────────────────────────
    sezione("3. TELEGRAM")
    print("\n  Per ricevere le notifiche, devi inserire il tuo Chat ID.")
    print("  Puoi trovarlo scrivendo a @userinfobot su Telegram.")
    dati["telegram_chat_id"] = chiedi("Il tuo Chat ID Telegram (opzionale)", "")

    # ── EMAIL (IMAP/SMTP) ─────────────────────
    sezione("4. CONNESSIONE EMAIL (Autonoma)")
    print("\n  Configura le credenziali per leggere e inviare email senza n8n.")

    dati["imap_host"] = chiedi("IMAP Host (es: imap.gmail.com)", "imap.gmail.com")
    dati["imap_port"] = int(chiedi("IMAP Port", "993"))
    dati["imap_user"] = chiedi("IMAP User (tua email)")
    dati["imap_password"] = chiedi("IMAP Password (app password)")

    dati["smtp_host"] = chiedi("SMTP Host (es: smtp.gmail.com)", "smtp.gmail.com")
    dati["smtp_port"] = int(chiedi("SMTP Port", "587"))
    dati["smtp_user"] = dati["imap_user"]
    dati["smtp_password"] = dati["imap_password"]

    # ── CONTATTI ─────────────────────────────
    sezione("5. CONTATTI AZIENDA")
    print("\n  Questi dati vengono inseriti automaticamente nelle bozze")
    print("  al posto dei [PLACEHOLDER]. Lascia vuoto se non applicabile.\n")

    dati["contacts"] = {
        "telefono":   chiedi("Telefono principale",      ""),
        "cellulare":  chiedi("Cellulare/WhatsApp",       ""),
        "email":      chiedi("Email di risposta",        ""),
        "indirizzo":  chiedi("Indirizzo fisico",         ""),
        "sito_web":   chiedi("Sito web",                 ""),
        "prenotazione_online": chiedi("Link prenotazione online (se disponibile)", ""),
    }
    # Rimuove campi vuoti
    dati["contacts"] = {k: v for k, v in dati["contacts"].items() if v}

    # ── ORARI ────────────────────────────────
    sezione("6. ORARI DI LAVORO")
    print("\n  Il bot usera questi orari per proporre appuntamenti.")
    print("  Inserisci nel formato HH:MM - HH:MM (es: 09:00 - 13:00)\n")

    orari = {}
    for giorno in GIORNI:
        orario = chiedi(f"  {giorno}", "").strip()
        if orario.lower() in ("chiuso", ""):
            if conferma(f"  {giorno} e chiuso?"):
                orari[giorno.lower()] = "chiuso"
        else:
            orari[giorno.lower()] = orario

    # Gestione pausa pranzo
    if conferma("C'e una pausa pranzo?"):
        pausa = chiedi("Orario pausa (es: 13:00 - 15:00)", "13:00 - 15:00")
        orari["pausa_pranzo"] = pausa

    dati["orari"] = orari

    # ── INTENTI ──────────────────────────────
    sezione("6. TIPI DI EMAIL (INTENTI)")
    
    print(f"\n  Sto cercando suggerimenti intelligenti per il settore '{dati['sector']}'...")
    config_suggerita = suggerisci_config_settore(dati["sector"])
    
    intent_instructions = {}
    if config_suggerita:
        print(f"\n  L'IA suggerisce questi intenti per '{dati['sector']}':")
        for i in config_suggerita["intent_list"]:
            print(f"    - {i}")
        
        if conferma("Vuoi usare questi suggerimenti?"):
            intenti = config_suggerita["intent_list"]
            intent_instructions = config_suggerita["intent_instructions"]
        else:
            config_suggerita = None

    if not config_suggerita:
        intenti = chiedi_lista(
            "Intenti del cliente:",
            "visita, urgenza, preventivo, info, reclamo, pagamento, spam, altro"
        )
        if "spam" not in intenti:
            intenti.append("spam")
        if "cortesia" not in intenti:
            intenti.append("cortesia")
        if "altro" not in intenti:
            intenti.append("altro")

    dati["intent_list"] = intenti

    # ── PRIORITA PER INTENT ──────────────────
    sezione("7. PRIORITA PER OGNI TIPO DI EMAIL")
    print("\n  0=non rispondere  1=BASSA autonomo  2=MEDIA WhatsApp  3=URGENTE alert\n")

    priority_map = {}
    for intent in intenti:
        if intent == "cortesia":
            priority_map[intent] = 0
            print(f"  'cortesia' -> NON RISPONDERE (automatico)")
            continue
        if intent in ("spam", "altro"):
            priority_map[intent] = 1
            print(f"  '{intent}' -> BASSA (automatico)")
            continue
        default_p = PRIORITA_DEFAULT.get(intent, 2)
        label = {0: "NON RISPONDERE", 1: "BASSA", 2: "MEDIA", 3: "URGENTE"}.get(default_p, "BASSA")
        print(f"\n  [{intent.upper()}] - default: {label}")
        if conferma(f"  Confermi il default per '{intent}'?"):
            priority_map[intent] = default_p
        else:
            priority_map[intent] = chiedi_priorita(intent)

    dati["priority_map"] = priority_map

    # ── ISTRUZIONI PER INTENT ────────────────
    sezione("8. ISTRUZIONI RISPOSTA PER OGNI TIPO")
    
    if not intent_instructions:
        print("\n  Personalizza cosa scrive il bot per ogni tipo di email.")
        print("  Lascia vuoto per usare il template generico di Polpo AI.\n")

        for intent in intenti:
            if intent in ("spam", "cortesia", "altro"):
                continue
            print(f"\n  [{intent.upper()}]")
            istruzione = chiedi(
                f"Cosa deve fare la bozza per '{intent}'? (vuoto = default)", ""
            )
            if istruzione:
                intent_instructions[intent] = istruzione
    else:
        print("\n  Istruzioni caricate dall'IA. Puoi modificarle se necessario:")
        for intent in intenti:
            if intent in ("spam", "altro"): continue
            print(f"    - {intent}: {intent_instructions.get(intent, 'default')[:60]}...")
            if conferma(f"      Vuoi modificare l'istruzione per '{intent}'?"):
                intent_instructions[intent] = chiedi(f"      Nuova istruzione per '{intent}'", intent_instructions.get(intent, ""))

    dati["intent_instructions"] = intent_instructions

    # ── FAQ ──────────────────────────────────
    sezione("9. FAQ - DOMANDE FREQUENTI")
    print("\n  Inserisci le domande piu frequenti con le risposte ufficiali.")
    print("  Il bot le usera per rispondere in autonomia alle richieste info.\n")

    faq = []
    if conferma("Vuoi aggiungere delle FAQ?"):
        print("  Inserisci domanda e risposta. Invio vuoto sulla domanda per finire.\n")
        while True:
            domanda = input("  Domanda: ").strip()
            if not domanda:
                break
            risposta = input("  Risposta: ").strip()
            if risposta:
                faq.append({"domanda": domanda, "risposta": risposta})
                print(f"  OK aggiunta ({len(faq)} FAQ totali)\n")

    dati["faq"] = faq

    # ── KEYWORD PER INTENT CON AI ─────────────
    sezione("10. PAROLE CHIAVE PER OGNI CATEGORIA")
    print("\n  L'IA genera keyword di classificazione specifiche per ogni categoria.")
    print("  Vengono usate come trigger nel classifier (Livello 2 - regole).\n")

    intent_keywords = {}
    custom_spam_keywords = []

    # spam: obbligatorio (filtro Livello 2)
    # cortesia: gestita da regex hardcoded in classifier.py, non configurabile
    # altro: catch-all, non ha trigger keyword
    INTENT_OBBLIGATORI = {"spam"}
    INTENT_SENZA_KEYWORD = {"altro", "cortesia"}

    if conferma("Vuoi che l'IA generi keyword per tutte le categorie?"):
        print()
        suggerimenti = suggerisci_tutte_le_keywords(
            dati["name"], dati["sector"], dati["intent_list"]
        )

        for intent in dati["intent_list"]:
            # cortesia e altro: nessuna keyword configurabile
            if intent in INTENT_SENZA_KEYWORD:
                print(f"\n  [{intent.upper()}] -> skippato (gestito automaticamente)")
                continue

            suggeriti = suggerimenti.get(intent, [])

            # Per gli intent obbligatori ritentiamo se l'AI non ha risposto
            if not suggeriti and intent in INTENT_OBBLIGATORI:
                print(f"\n  [{intent.upper()}] - Riprovo la generazione (categoria obbligatoria)...")
                suggeriti = suggerisci_keywords_per_intent(dati["name"], dati["sector"], intent)

            if not suggeriti:
                continue

            label_obbl = " (OBBLIGATORIO)" if intent in INTENT_OBBLIGATORI else ""
            print(f"\n  [{intent.upper()}]{label_obbl} - Keyword suggerite:")
            for i, kw in enumerate(suggeriti, 1):
                print(f"    {i}. {kw}")

            # Per gli intent obbligatori non si può saltare
            if intent in INTENT_OBBLIGATORI:
                opzioni_modalita = [
                    "Aggiungi tutte",
                    "Scegli quali aggiungere",
                    "Ignora e inserisci manualmente",
                ]
            else:
                opzioni_modalita = [
                    "Aggiungi tutte",
                    "Scegli quali aggiungere",
                    "Ignora e inserisci manualmente",
                    "Salta questa categoria",
                ]

            modalita = chiedi_scelta(f"Cosa vuoi fare per '{intent}'?", opzioni_modalita)

            kw_finali = []
            if modalita == "Aggiungi tutte":
                kw_finali = suggeriti
            elif modalita == "Scegli quali aggiungere":
                print("\n  Numeri delle keyword da aggiungere (es: 1 3 5):")
                scelte = input("  -> ").strip().split()
                for s in scelte:
                    if s.isdigit() and 1 <= int(s) <= len(suggeriti):
                        kw_finali.append(suggeriti[int(s) - 1])
            elif modalita == "Ignora e inserisci manualmente":
                kw_finali = chiedi_lista(f"Keyword per '{intent}':", "es: parola chiave 1, parola chiave 2")

            # Obbligatori: forziamo inserimento manuale se ancora vuoto
            if intent in INTENT_OBBLIGATORI and not kw_finali:
                print(f"\n  Attenzione: '{intent}' è obbligatorio. Inserisci almeno una keyword.")
                kw_finali = chiedi_lista(f"Keyword per '{intent}':", "newsletter, promo, offerta")

            if modalita != "Salta questa categoria":
                if conferma(f"Vuoi aggiungere altre keyword manualmente per '{intent}'?"):
                    extra = chiedi_lista("Keyword aggiuntive:")
                    kw_finali.extend(extra)

            if kw_finali:
                intent_keywords[intent] = kw_finali
                if intent == "spam":
                    custom_spam_keywords = kw_finali

    else:
        # Inserimento manuale per ogni intent
        for intent in dati["intent_list"]:
            if intent in INTENT_SENZA_KEYWORD:
                print(f"\n  [{intent.upper()}] -> skippato (gestito automaticamente)")
                continue

            obbligatorio = intent in INTENT_OBBLIGATORI
            print(f"\n  [{intent.upper()}]{' (OBBLIGATORIO)' if obbligatorio else ''}")
            kw = chiedi_lista(
                f"Keyword per '{intent}' {'(almeno una richiesta)' if obbligatorio else '(vuoto per saltare)'}:",
                "es: parola chiave 1, parola chiave 2"
            )
            while obbligatorio and not kw:
                print(f"  Attenzione: '{intent}' è obbligatorio. Inserisci almeno una keyword.")
                kw = chiedi_lista(f"Keyword per '{intent}':", "newsletter, promo, offerta")
            if kw:
                intent_keywords[intent] = kw
                if intent == "spam":
                    custom_spam_keywords = kw

    dati["intent_keywords"] = intent_keywords
    dati["custom_spam_keywords"] = custom_spam_keywords

    # ── TELEGRAM ─────────────────────────────
    sezione("10. BOT TELEGRAM (opzionale)")
    print("\n  Per trovare il tuo chat_id:")
    print("    1. Apri Telegram e scrivi a @userinfobot")
    print("    2. Il bot risponde con il tuo ID numerico")
    print("    3. Incollalo qui sotto\n")
    dati["telegram_chat_id"] = chiedi("Chat ID Telegram (lascia vuoto per saltare)", "")

    return dati


# ─────────────────────────────────────────────
# Riepilogo
# ─────────────────────────────────────────────

def mostra_riepilogo(dati):
    titolo(f"RIEPILOGO - {dati['name']}")

    print(f"\n  Settore  : {dati['sector']}")
    print(f"  Lingua   : {dati['language']}")
    print(f"  Tono     : {dati['llm_tone']}")
    print(f"  Firma    : {dati['signature'].replace(chr(10), ' / ')}")

    if dati.get("contacts"):
        print(f"\n  Contatti:")
        for k, v in dati["contacts"].items():
            print(f"    - {k}: {v}")

    if dati.get("orari"):
        print(f"\n  Orari:")
        for g, o in dati["orari"].items():
            print(f"    - {g}: {o}")

    print(f"\n  Intenti e priorita:")
    for intent in dati["intent_list"]:
        p = dati["priority_map"].get(intent, 1)
        label = {0: "NO REPLY", 1: "BASSA   ", 2: "MEDIA   ", 3: "URGENTE"}.get(p, "BASSA   ")
        istr = dati["intent_instructions"].get(intent, "(default Polpo AI)")
        print(f"    - {intent:<15} -> {label} | {istr[:45]}")

    if dati.get("faq"):
        print(f"\n  FAQ: {len(dati['faq'])} domande configurate")

    if dati.get("intent_keywords"):
        print(f"\n  Keyword per categoria:")
        for intent, kw_list in dati["intent_keywords"].items():
            print(f"    - {intent:<15}: {', '.join(kw_list[:4])}{'...' if len(kw_list) > 4 else ''}")


# ─────────────────────────────────────────────
# Salvataggio su Supabase
# ─────────────────────────────────────────────

def salva_cliente(dati):
    db = get_db()
    result = db.table("clients").insert({
        "name":                 dati["name"],
        "sector":               dati["sector"],
        "llm_persona":          dati["llm_persona"],
        "llm_tone":             dati["llm_tone"],
        "signature":            dati["signature"],
        "intent_list":          dati["intent_list"],
        "priority_map":         dati.get("priority_map", {}),
        "custom_spam_keywords": dati["custom_spam_keywords"],
        "intent_keywords":      dati.get("intent_keywords", {}),
        "intent_instructions":  dati["intent_instructions"],
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
    }).execute()
    return result.data[0]["id"]


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        dati = raccogli_dati()
        mostra_riepilogo(dati)

        if not conferma("Confermi e salvi il cliente su Supabase?"):
            print("\n  Operazione annullata. Nessun dato salvato.")
            exit(0)

        print("\n  Salvataggio in corso...")
        client_id = salva_cliente(dati)

        titolo("CLIENTE CREATO CON SUCCESSO")
        print(f"\n  Nome      : {dati['name']}")
        print(f"  Client ID : {client_id}")
        print(f"\n  Usa questo ID nelle chiamate API:")
        print(f"  \"client_id\": \"{client_id}\"")
        print(f"\n  Il bot e pronto per {dati['name']}! 🐙\n")

    except KeyboardInterrupt:
        print("\n\n  Onboarding interrotto.\n")
    except Exception as e:
        print(f"\n  Errore: {e}\n")
