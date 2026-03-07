-- migration_add_email_credentials.sql
-- Aggiunge le colonne IMAP/SMTP alla tabella clients
-- Eseguire una volta su Supabase → SQL Editor

ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS imap_host      TEXT,
    ADD COLUMN IF NOT EXISTS imap_port      INTEGER DEFAULT 993,
    ADD COLUMN IF NOT EXISTS imap_user      TEXT,
    ADD COLUMN IF NOT EXISTS imap_password  TEXT,
    ADD COLUMN IF NOT EXISTS smtp_host      TEXT,
    ADD COLUMN IF NOT EXISTS smtp_port      INTEGER DEFAULT 587,
    ADD COLUMN IF NOT EXISTS smtp_user      TEXT,
    ADD COLUMN IF NOT EXISTS smtp_password  TEXT,
    ADD COLUMN IF NOT EXISTS imap_last_uid  INTEGER DEFAULT 0;

-- Colonna per tracciare errori di invio SMTP
ALTER TABLE draft_responses
    ADD COLUMN IF NOT EXISTS send_error TEXT;

COMMENT ON COLUMN clients.imap_host     IS 'Es: imap.gmail.com / imap.aruba.it';
COMMENT ON COLUMN clients.imap_port     IS 'Default 993 (SSL)';
COMMENT ON COLUMN clients.smtp_host     IS 'Es: smtp.gmail.com / smtps.aruba.it';
COMMENT ON COLUMN clients.smtp_port     IS 'Default 587 (STARTTLS)';
COMMENT ON COLUMN clients.imap_last_uid IS 'UID ultima email processata — evita duplicati';
COMMENT ON COLUMN clients.imap_password IS 'App Password (non la password principale!)';
