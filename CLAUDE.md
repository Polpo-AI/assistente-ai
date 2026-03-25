# CLAUDE.md — Polpo AI Email Intelligence

Guida per AI che lavora su questo progetto. Leggi prima di toccare qualsiasi cosa.

---

## Cos'è questo progetto

Polpo AI è un sistema di email intelligence multi-tenant per PMI italiane. Legge email in arrivo via IMAP, le classifica con Claude, genera bozze di risposta e documenti PDF/Excel (preventivi, comunicazioni), e presenta tutto a un operatore umano su Telegram per approvazione prima dell'invio via SMTP.

Prodotto non ancora lanciato (2026-03-26). Cliente pilota: TecnoDomus360.

---

## Architettura — file chiave

| File | Ruolo |
|---|---|
| `email_worker.py` | Loop IMAP per tutti i client, polling + deduplicazione |
| `classifier.py` | Classificazione intent (Haiku → Sonnet fallback) |
| `responder.py` | Generazione bozza risposta + structured JSON |
| `document_generator.py` | PDF/Excel da JSON (ReportLab + openpyxl) |
| `telegram_bot.py` | Bot approvazione + chat conversazionale con tool use |
| `smtp_sender.py` | Invio SMTP asincrono dopo approvazione |
| `database.py` | Data layer Supabase (tutto qui, 757 righe) |
| `main.py` | FastAPI — endpoint classificazione + webhook |
| `client_config.py` | Config multi-tenant con cache 60s |
| `attachment_reader.py` | OCR allegati via Sonnet |
| `query_tools.py` | Tool per Claude nel bot Telegram |
| `notifications.py` | Email admin per feature mancanti |

---

## Stack

- **Python** async (asyncio) — no threading salvo `asyncio.to_thread` per chiamate bloccanti
- **Supabase** (PostgreSQL) — tutti i dati, credenziali IMAP/SMTP per client, draft, history
- **Claude API** — Haiku per classificazione rapida, Sonnet per risposta e OCR
- **Telegram Bot API** — human-in-the-loop
- **Systemd** — gestione processi su VPS

---

## VPS e deploy

```
SSH: root@46.225.212.159

Produzione:  /opt/polpo-ai          → branch main
Staging:     /opt/polpo-ai-staging  → branch preview
```

**Servizi systemd:**
- Produzione: `polpo-prod.service` + `polpo-worker.service`
- Staging: `polpo-staging.service` + `polpo-worker-staging.service`

**Deploy produzione:**
```bash
ssh root@46.225.212.159
cd /opt/polpo-ai && git pull && systemctl restart polpo-prod polpo-worker
```

**Deploy staging:**
```bash
ssh root@46.225.212.159
cd /opt/polpo-ai-staging && git pull && systemctl restart polpo-staging polpo-worker-staging
```

**Log live:**
```bash
journalctl -u polpo-worker -f          # produzione
journalctl -u polpo-worker-staging -f  # staging
```

---

## Workflow branch

```
sviluppo locale → push → preview → staging VPS (test manuale)
                              ↓ merge
                            main → produzione VPS
```

Lo staging NON si aggiorna automaticamente — va fatto `git pull` manuale sul VPS.

---

## Gotchas critici

### JSON e LLM output
- `clean_json()` in `responder.py` è il safety net per output malformati — non rimuoverla mai
- `max_tokens=2000` in `responder.py` è intenzionale — `attach_document` è verboso, abbassarlo causa troncamenti
- Il parsing JSON usa regex (`re.search`) — fragile con JSON annidati. Da migrare a Pydantic (TODO)

### Password
- **Non aggiungere mai** `.replace(" ", "")` alle password IMAP/SMTP — le rompe silenziosamente
- Le credenziali IMAP/SMTP di ogni cliente sono in Supabase, tabella `clients`

### Async
- Tutto il pipeline è async. Chiamate bloccanti (DB sync, file I/O) vanno wrappate in `asyncio.to_thread`
- Non mixare sync e async senza boundary chiari

### Multi-tenant
- Ogni cliente ha config separata in Supabase (IMAP, SMTP, Telegram chat_id, prompt personalizzato)
- `client_config.py` ha cache 60s — modifiche al DB impiegano fino a 1 minuto a propagarsi

### Deduplicazione email
- Usa `message_id` MIME + `safe_uid`. Race condition nota con più worker simultanei (TODO: transaction DB)
- Per forzare re-processing: abbassa `imap_last_uid` nel DB e cancella il record da `emails`

### Re-trigger email per test
1. Cancella da `emails` e `draft_responses` in Supabase
2. Abbassa `imap_last_uid` del cliente a `UID - 1`
3. Il worker ri-processerà alla prossima poll

---

## Bug noti / TODO prioritari

| Priorità | Issue | File |
|---|---|---|
| Alta | Race condition deduplicazione message_id | `email_worker.py:608` |
| Alta | `smtp_password` esposto nelle query | `database.py:443` |
| Alta | Nessun connection pooling IMAP | `email_worker.py:335` |
| Media | JSON parsing con regex → migrare a Pydantic | `responder.py`, `classifier.py` |
| Media | `database.py` monolitico (757 righe) da splittare | `database.py` |
| Media | `_doc_number()` collide al minuto | `document_generator.py:43` |
| Bassa | Magic strings (intent, callback) → enum | vari file |

---

## Modelli Claude usati

Definiti in `models_config.py`:
- **Haiku** → classificazione rapida (`classifier.py`)
- **Sonnet** → generazione bozza, OCR allegati, tool use Telegram (`responder.py`, `attachment_reader.py`, `telegram_bot.py`)
