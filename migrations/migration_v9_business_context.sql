-- migration_v9_business_context.sql
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS business_description text DEFAULT '',
    ADD COLUMN IF NOT EXISTS services_offered     text DEFAULT '';
COMMENT ON COLUMN clients.business_description IS 'Chi è l''azienda, cosa fa, storia e valori.';
COMMENT ON COLUMN clients.services_offered     IS 'Lista servizi/prodotti offerti. Definisce cosa è in-scope e out-of-scope.';
