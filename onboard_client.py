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
    print("    1. BASSA   -> risponde in autonomia")
    print("    2. MEDIA   -> WhatsApp con bottoni Approva/Modifica")
    print("    3. URGENTE -> alert immediato al titolare")
    while True:
        scelta = input("  Scelta [1/2/3]: ").strip()
        if scelta in ("1", "2", "3"):
            return int(scelta)
        print("  Inserisci 1, 2 o 3.")

def conferma(domanda):
    risposta = input(f"\n  {domanda} [s/n]: ").strip().lower()
    return risposta in ("s", "si", "y", "yes")


# ─────────────────────────────────────────────
# Intenti default per settore
# ─────────────────────────────────────────────

INTENTI_PER_SETTORE = {
    "automotive":     ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "altro"],
    "sanitario":      ["visita", "urgenza", "informazione", "reclamo", "pagamento", "spam", "altro"],
    "legale":         ["consulenza", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "altro"],
    "edilizia":       ["preventivo", "sopralluogo", "informazione", "reclamo", "pagamento", "spam", "altro"],
    "commercialista": ["dichiarazione", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "altro"],
    "altro":          ["preventivo", "appuntamento", "informazione", "reclamo", "pagamento", "spam", "altro"],
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
    "altro":         1,
}

GIORNI = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]


# ─────────────────────────────────────────────
# Suggerimento keyword spam via LLM
# ─────────────────────────────────────────────

def suggerisci_spam_keywords(nome_azienda, settore):
    try:
        from anthropic import Anthropic
        client = Anthropic()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=300,
            system=(
                "Sei un esperto di email spam. "
                "Rispondi SOLO con un array JSON di stringhe. "
                "Nessun testo fuori dal JSON."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Genera 8-10 keyword o frasi brevi tipiche dello spam "
                    f"che potrebbero arrivare a '{nome_azienda}' nel settore '{settore}'. "
                    f"Devono essere specifiche per il settore, non generiche. "
                    f"Formato: [\"keyword 1\", \"keyword 2\", ...]"
                )
            }]
        )
        keywords = json.loads(response.content[0].text.strip())
        return [kw.lower() for kw in keywords if isinstance(kw, str)]
    except Exception as e:
        print(f"  (Suggerimento AI non disponibile: {e})")
        return []


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
    dati["sector"] = chiedi_scelta("Settore di attivita:", SETTORI)

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

    # ── CONTATTI ─────────────────────────────
    sezione("3. CONTATTI AZIENDA")
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
    sezione("4. ORARI DI LAVORO")
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
    sezione("5. TIPI DI EMAIL (INTENTI)")
    intenti_default = INTENTI_PER_SETTORE.get(dati["sector"], INTENTI_PER_SETTORE["altro"])
    print(f"\n  Intenti suggeriti per '{dati['sector']}':")
    for i in intenti_default:
        print(f"    - {i}")

    if conferma("Vuoi usare questi intenti?"):
        intenti = intenti_default
    else:
        intenti = chiedi_lista(
            "Intenti del cliente:",
            "visita, urgenza, preventivo, info, reclamo, pagamento, spam, altro"
        )
        if "spam" not in intenti:
            intenti.append("spam")
        if "altro" not in intenti:
            intenti.append("altro")

    dati["intent_list"] = intenti

    # ── PRIORITA PER INTENT ──────────────────
    sezione("6. PRIORITA PER OGNI TIPO DI EMAIL")
    print("\n  BASSA=autonomo  MEDIA=WhatsApp bottoni  URGENTE=alert titolare\n")

    priority_map = {}
    for intent in intenti:
        if intent in ("spam", "altro"):
            priority_map[intent] = 1
            print(f"  '{intent}' -> BASSA (automatico)")
            continue
        default_p = PRIORITA_DEFAULT.get(intent, 2)
        label = {1: "BASSA", 2: "MEDIA", 3: "URGENTE"}[default_p]
        print(f"\n  [{intent.upper()}] - default: {label}")
        if conferma(f"  Confermi il default per '{intent}'?"):
            priority_map[intent] = default_p
        else:
            priority_map[intent] = chiedi_priorita(intent)

    dati["priority_map"] = priority_map

    # ── ISTRUZIONI PER INTENT ────────────────
    sezione("7. ISTRUZIONI RISPOSTA PER OGNI TIPO")
    print("\n  Personalizza cosa scrive il bot per ogni tipo di email.")
    print("  Lascia vuoto per usare il template generico di Polpo AI.\n")

    intent_instructions = {}
    for intent in intenti:
        if intent in ("spam", "altro"):
            continue
        print(f"\n  [{intent.upper()}]")
        istruzione = chiedi(
            f"Cosa deve fare la bozza per '{intent}'? (vuoto = default)", ""
        )
        if istruzione:
            intent_instructions[intent] = istruzione

    dati["intent_instructions"] = intent_instructions

    # ── FAQ ──────────────────────────────────
    sezione("8. FAQ - DOMANDE FREQUENTI")
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

    # ── SPAM KEYWORDS CON AI ──────────────────
    sezione("9. PAROLE CHIAVE SPAM EXTRA")
    print("\n  Genero suggerimenti specifici per il settore...\n")

    suggeriti = suggerisci_spam_keywords(dati["name"], dati["sector"])
    spam_kw = []

    if suggeriti:
        print("  Keyword spam suggerite dall'IA:")
        for i, kw in enumerate(suggeriti, 1):
            print(f"    {i}. {kw}")

        modalita = chiedi_scelta(
            "Cosa vuoi fare con questi suggerimenti?",
            [
                "Aggiungi tutte",
                "Scegli quali aggiungere",
                "Ignora e inserisci manualmente",
                "Non aggiungere keyword extra",
            ]
        )

        if modalita == "Aggiungi tutte":
            spam_kw = suggeriti
        elif modalita == "Scegli quali aggiungere":
            print("\n  Numeri delle keyword da aggiungere (es: 1 3 5):")
            scelte = input("  -> ").strip().split()
            for s in scelte:
                if s.isdigit() and 1 <= int(s) <= len(suggeriti):
                    spam_kw.append(suggeriti[int(s) - 1])
        elif modalita == "Ignora e inserisci manualmente":
            spam_kw = chiedi_lista("Keyword spam manuali:", "newsletter, promo")

        if modalita != "Non aggiungere keyword extra":
            if conferma("Vuoi aggiungere altre keyword manualmente?"):
                extra = chiedi_lista("Keyword aggiuntive:")
                spam_kw.extend(extra)
    else:
        spam_kw = chiedi_lista(
            "Keyword spam aggiuntive (vuoto per saltare):",
            "newsletter, promo, offerta speciale"
        )

    dati["custom_spam_keywords"] = spam_kw

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
        label = {1: "BASSA   ", 2: "MEDIA   ", 3: "URGENTE"}[p]
        istr = dati["intent_instructions"].get(intent, "(default Polpo AI)")
        print(f"    - {intent:<15} -> {label} | {istr[:45]}")

    if dati.get("faq"):
        print(f"\n  FAQ: {len(dati['faq'])} domande configurate")

    if dati.get("custom_spam_keywords"):
        print(f"\n  Spam extra: {', '.join(dati['custom_spam_keywords'])}")


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
        "intent_instructions":  dati["intent_instructions"],
        "contacts":             dati.get("contacts", {}),
        "orari":                dati.get("orari", {}),
        "faq":                  dati.get("faq", []),
        "language":             dati.get("language", "Italiano"),
        "telegram_chat_id":     dati.get("telegram_chat_id") or None,
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
