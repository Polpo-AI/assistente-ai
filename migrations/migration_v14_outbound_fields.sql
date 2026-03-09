-- migration_v14_outbound_fields.sql
ALTER TABLE emails
ADD COLUMN IF NOT EXISTS confidence   FLOAT,
ADD COLUMN IF NOT EXISTS contact_type TEXT,
ADD COLUMN IF NOT EXISTS summary      TEXT,
ADD COLUMN IF NOT EXISTS intent       TEXT,
ADD COLUMN IF NOT EXISTS priority     INTEGER;
