-- ═══════════════════════════════════════════════════════════════
-- Migration v4 — Integrazione Bot Telegram
-- Esegui nell'editor SQL di Supabase dopo migration_v3.sql
-- ═══════════════════════════════════════════════════════════════

-- Aggiunge chat_id Telegram al cliente (opzionale: NULL se non usa Telegram)
alter table clients
    add column if not exists telegram_chat_id text;

-- Aggiunge ID messaggio Telegram alla bozza (per editare il messaggio dopo azioni)
alter table draft_responses
    add column if not exists telegram_message_id bigint;

-- Aggiunge lo stato 'ignored' e 'snooze' alla constraint esistente
-- Prima rimuove la vecchia constraint, poi la ricrea
alter table draft_responses
    drop constraint if exists draft_responses_status_check;

alter table draft_responses
    add constraint draft_responses_status_check
    check (status in ('pending','approved','rejected','sent','ignored'));
