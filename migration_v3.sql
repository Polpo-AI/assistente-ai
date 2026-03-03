-- ═══════════════════════════════════════════════════════════════
-- Migration v3 — Aggiunta priority_map alla tabella clients
-- Esegui questo script nell'editor SQL di Supabase
-- (solo se hai gia eseguito schema.sql e migration_v2.sql in precedenza)
-- ═══════════════════════════════════════════════════════════════

alter table clients
    add column if not exists priority_map jsonb default '{}';

-- Valore di default ragionevole per i clienti esistenti
-- (allinea con i default usati da classifier.py)
update clients
set priority_map = '{
    "reclamo":      3,
    "urgenza":      3,
    "preventivo":   2,
    "appuntamento": 2,
    "pagamento":    2,
    "visita":       2,
    "sopralluogo":  2,
    "consulenza":   2,
    "dichiarazione":2,
    "informazione": 1,
    "info":         1,
    "spam":         1,
    "altro":        1
}'::jsonb
where priority_map = '{}' or priority_map is null;
