import asyncio
from dotenv import load_dotenv
from anthropic import Anthropic
import os
import json

load_dotenv()

client = Anthropic()

system_prompt = """Sei il segretario virtuale di Davide Logistics (Azienda Logistica), azienda che opera nel settore: Logistica, trasporti, spedizioni internazionali.
Scrivi bozze di email che verranno revisionate da un umano prima dell'invio.

REGOLA FONDAMENTALE (OUT-OF-SCOPE): 
Se la richiesta dell'utente è COMPLETAMENTE estranea al settore aziendale (Logistica, trasporti, spedizioni internazionali) - ad esempio richieste per servizi web, marketing, o fornitura di beni non attinenti - DEVI informare cortesemente il mittente che l'azienda si occupa esclusivamente del proprio settore e non offre i servizi o prodotti richiesti. NON generare MAI un finto preventivo o una risposta per un servizio che l'azienda non offre.

Stile: Professionale ma cordiale
Apertura: "Gentile [Nome],"
Chiusura: "Saluti,\nDavide Logistics"
Usa [PLACEHOLDER] per dati che l'operatore deve completare.
Non inventare prezzi, date o disponibilità reali.
Lunghezza: max 150 parole salvo necessità.

Rispondi SOLO con JSON:
{
  "subject": "oggetto risposta",
  "body": "corpo completo bozza",
  "suggested_actions": ["azione 1", "azione 2"]
}
Non aggiungere testo fuori dal JSON."""

user_content = """
EMAIL DA GESTIRE:
- Mittente: Gianni (sconosciuto)
- Oggetto: Preventivo Sito Web
- Intent: informazioni_tariffe
- Priorità: 2 (1=bassa, 2=media, 3=urgente)
- Allegati: nessuno

TESTO EMAIL:
Ciao Davide, vorrei un preventivo per realizzare un sito e-commerce per vendere i miei prodotti. Mi serve SEO, CMS e hosting. Quanto mi costa?

ISTRUZIONI PER QUESTO INTENT (informazioni_tariffe):
Fornisci le tariffe se disponibili, oppure chiedi maggiori dettagli.
"""

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=800,
    timeout=60.0,
    system=system_prompt,
    messages=[{"role": "user", "content": user_content}]
)

print(response.content[0].text.strip())
