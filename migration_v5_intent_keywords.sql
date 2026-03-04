-- migration_v5_intent_keywords.sql
-- Aggiunge la colonna intent_keywords per classificazione deterministica migliorata
-- Eseguire su Supabase -> SQL Editor

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS intent_keywords JSONB DEFAULT '{}';

COMMENT ON COLUMN clients.intent_keywords IS 'Mappa intent -> [keywords/frasi]. Es: {"preventivo": ["torta", "battesimo"]}';
