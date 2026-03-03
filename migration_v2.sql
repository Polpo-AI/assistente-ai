-- ═══════════════════════════════════════════════════════════════
-- Migration v2 — Nuove colonne tabella clients
-- Esegui questo script nell'editor SQL di Supabase
-- (solo se hai gia eseguito schema.sql in precedenza)
-- ═══════════════════════════════════════════════════════════════

alter table clients
    add column if not exists contacts  jsonb default '{}',
    add column if not exists orari     jsonb default '{}',
    add column if not exists faq       jsonb default '[]',
    add column if not exists language  text  default 'Italiano';
