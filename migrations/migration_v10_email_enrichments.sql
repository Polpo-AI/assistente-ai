-- migration_v10_email_enrichments.sql
ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS quoted_text       text  DEFAULT '',
    ADD COLUMN IF NOT EXISTS attachments_text  jsonb DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS detected_language text  DEFAULT '';
