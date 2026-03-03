"""
client_config.py - Configurazione per cliente (Multi-Tenant)

Legge da Supabase la configurazione completa del cliente:
- persona LLM e lingua
- intenti e priorita
- keyword spam
- istruzioni per intent
- contatti, orari, FAQ
"""

import json
from typing import Optional
from dataclasses import dataclass, field
from database import get_client

# ─────────────────────────────────────────────
# Dataclass configurazione cliente
# ─────────────────────────────────────────────

@dataclass
class ClientConfig:
    client_id:            str
    name:                 str
    sector:               str
    language:             str
    llm_persona:          str
    llm_tone:             str
    signature:            str
    intent_list:          list
    priority_map:         dict    # {"reclamo": 3, "preventivo": 2, ...}
    custom_spam_keywords: list
    intent_instructions:  dict
    contacts:             dict
    orari:                dict
    faq:                  list
    telegram_chat_id:     Optional[str] = None  # None se il cliente non usa Telegram

    def all_intents_str(self) -> str:
        return " | ".join(f'"{i}"' for i in self.intent_list)

    def get_intent_instruction(self, intent: str) -> str:
        if intent in self.intent_instructions:
            return self.intent_instructions[intent]
        return DEFAULT_INTENT_INSTRUCTIONS.get(intent, DEFAULT_INTENT_INSTRUCTIONS["altro"])

    def format_contacts(self) -> str:
        """Formatta i contatti per inserirli nel prompt LLM."""
        if not self.contacts:
            return ""
        lines = ["Contatti azienda:"]
        labels = {
            "telefono": "Tel",
            "cellulare": "Cell/WhatsApp",
            "email": "Email",
            "indirizzo": "Indirizzo",
            "sito_web": "Sito",
            "prenotazione_online": "Prenotazioni online",
        }
        for k, v in self.contacts.items():
            label = labels.get(k, k)
            lines.append(f"  {label}: {v}")
        return "\n".join(lines)

    def format_orari(self) -> str:
        """Formatta gli orari per inserirli nel prompt LLM."""
        if not self.orari:
            return ""
        lines = ["Orari di apertura:"]
        for giorno, orario in self.orari.items():
            if giorno == "pausa_pranzo":
                lines.append(f"  Pausa pranzo: {orario}")
            else:
                lines.append(f"  {giorno.capitalize()}: {orario}")
        return "\n".join(lines)

    def format_faq(self) -> str:
        """Formatta le FAQ per inserirle nel prompt LLM."""
        if not self.faq:
            return ""
        lines = ["Domande frequenti e risposte ufficiali:"]
        for item in self.faq:
            lines.append(f"  D: {item.get('domanda', '')}")
            lines.append(f"  R: {item.get('risposta', '')}")
        return "\n".join(lines)


# ─────────────────────────────────────────────
# Istruzioni default per intent
# ─────────────────────────────────────────────

DEFAULT_INTENT_INSTRUCTIONS = {
    "preventivo": (
        "Ringrazia per l'interesse. Spiega che risponderai con un preventivo dettagliato. "
        "Chiedi eventuali informazioni mancanti. Proponi un appuntamento se utile."
    ),
    "appuntamento": (
        "Ringrazia per il contatto. Proponi 2-3 fasce orarie disponibili. "
        "Usa gli orari reali dell'azienda se disponibili."
    ),
    "informazione": (
        "Rispondi in modo chiaro e professionale. "
        "Usa le FAQ dell'azienda se la risposta e presente. "
        "Se non hai info sufficienti, invita a chiamare."
    ),
    "reclamo": (
        "Inizia con scuse sincere. Mostra empatia. "
        "Proponi una soluzione concreta. Dai il contatto diretto. "
        "ATTENZIONE: richiede revisione prima dell'invio."
    ),
    "pagamento": (
        "Rispondi in modo preciso. Non fare promesse di importi senza verifica. "
        "Se richiesta fattura: conferma invio entro [X giorni]."
    ),
    "spam": "Non rispondere. Archiviare.",
    "altro": (
        "Ringrazia per il contatto. Chiedi di specificare la richiesta. "
        "Proponi di chiamare per chiarire."
    ),
}


# ─────────────────────────────────────────────
# Cache in memoria con TTL (5 minuti)
# ─────────────────────────────────────────────

import time

_CACHE_TTL_SECONDS = 300  # 5 minuti
_config_cache: dict = {}  # { client_id: (ClientConfig, timestamp) }


def get_client_config(client_id: str, force_refresh: bool = False) -> Optional[ClientConfig]:
    """
    Carica la configurazione del cliente dal DB con cache in memoria (TTL 5 min).
    """
    now = time.time()
    if not force_refresh and client_id in _config_cache:
        cached_config, cached_at = _config_cache[client_id]
        if now - cached_at < _CACHE_TTL_SECONDS:
            return cached_config

    db = get_client()
    result = (
        db.table("clients")
        .select("*")
        .eq("id", client_id)
        .eq("active", True)
        .execute()
    )

    if not result.data:
        return None

    row = result.data[0]

    def parse_json_field(val, default):
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                pass
        return default

    config = ClientConfig(
        client_id=client_id,
        name=row["name"],
        sector=row["sector"],
        language=row.get("language", "Italiano"),
        llm_persona=row["llm_persona"],
        llm_tone=row.get("llm_tone", "professionale e cordiale"),
        signature=row.get("signature", "Cordiali saluti"),
        intent_list=parse_json_field(row.get("intent_list"), []),
        priority_map=parse_json_field(row.get("priority_map"), {}),
        custom_spam_keywords=parse_json_field(row.get("custom_spam_keywords"), []),
        intent_instructions=parse_json_field(row.get("intent_instructions"), {}),
        contacts=parse_json_field(row.get("contacts"), {}),
        orari=parse_json_field(row.get("orari"), {}),
        faq=parse_json_field(row.get("faq"), []),
        telegram_chat_id=row.get("telegram_chat_id"),
    )

    _config_cache[client_id] = (config, time.time())
    return config


def get_client_id_by_name(name: str) -> Optional[str]:
    """Utility per trovare client_id dal nome."""
    db = get_client()
    result = db.table("clients").select("id").eq("name", name).execute()
    return result.data[0]["id"] if result.data else None


def invalidate_cache(client_id: str) -> None:
    """Rimuove il cliente dalla cache dopo modifiche."""
    _config_cache.pop(client_id, None)
