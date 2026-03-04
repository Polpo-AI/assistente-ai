# Polpo AI Mail Intelligence - Project Summary

Questo file funge da riferimento centrale per l'intelligenza artificiale per comprendere l'architettura e la logica del progetto senza riesaminare tutti i file.

## 📌 Visione d'Insieme
Polpo AI è un sistema di automazione email multi-tenant progettato per classificare le email in arrivo, generare bozze di risposta personalizzate e gestire l'approvazione umana tramite Telegram o dashboard.

## ⚙️ Architettura e Flusso Logico
1.  **Trigger (`email_worker.py`)**: Il flusso inizia con il polling IMAP asincrono che controlla le nuove email per ogni cliente attivo (sostituisce n8n).
2.  **Classificazione (`classifier.py`)**: Sistema a cascata a 3 livelli:
    *   *Livello 1 (DB Lookup)*: Verifica se il mittente è un contatto noto.
    *   *Livello 2 (Regole)*: Filtro spam e intenti basati su keyword/regex.
    *   *Livello 3 (LLM Haiku)*: Fallback su AI per email complesse (priority, intent, summary).
3.  **Risposta (`responder.py`)**:
    *   Generazione bozze con **Claude 3.5 Sonnet**.
    *   Utilizzo di `ClientConfig` per adattare tono, firma e istruzioni al settore del cliente.
    *   Riclassificazione automatica se l'intento iniziale è incerto.
4.  **Interazione Umana (`telegram_bot.py`)**:
    *   Notifiche Push per email con priorità >= 2.
    *   Bottoni inline: Invia (approva su DB), Modifica (via chat), Snooze, Ignora.
    *   Assistente conversazionale (`query_tools.py`) per interrogare il DB delle email.
5.  **Invio (`email_worker.py`)**: Un watcher in background rileva le bozze approvate e le invia via SMTP utilizzando le credenziali specifiche del cliente.

## 🛠 Tech Stack
*   **Backend**: FastAPI (Python)
*   **Worker**: Asyncio (IMAP/SMTP Polling)
*   **Database**: Supabase (PostgreSQL)
*   **AI**: Anthropic Claude (Haiku per classificazione, Sonnet per draft/ragionamento/tool-calling)
*   **Integrazione**: Telegram Bot API
*   **Infrastructure**: Multi-tenant (configurazioni e credenziali mail isolate per client_id)

## 📂 Struttura File Core
*   `main.py`: Entry point API, router webhook Telegram e rotte dashboard.
*   `email_worker.py`: Worker asincrono per polling IMAP (lettura) e invio SMTP (invio bozze approvate).
*   `classifier.py`: Logica di classificazione a cascata.
*   `responder.py`: Pipeline di generazione bozze e refinement.
*   `telegram_bot.py`: Logica del bot Telegram (notifiche e conversazione).
*   `query_tools.py`: Tool di interrogazione DB per l'assistente AI (function calling).
*   `database.py`: Wrapper per le chiamate a Supabase.
*   `client_config.py`: Gestione configurazioni dinamiche dei clienti.
*   `notifications.py`: Gestione notifiche amministrative (es. funzioni mancanti).
*   `onboard_client.py`: Script CLI per configurare nuovi tenant.

---
*Ultimo aggiornamento: 2026-03-03*
