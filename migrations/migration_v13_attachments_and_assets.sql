-- migration_v13_attachments_and_assets.sql
CREATE TABLE IF NOT EXISTS email_attachments_pending (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    email_id   uuid        NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    filename   text        NOT NULL,
    mime_type  text        NOT NULL DEFAULT '',
    data_b64   text        NOT NULL,
    size_bytes integer     NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email_id, filename)
);
CREATE INDEX IF NOT EXISTS idx_att_pending_created ON email_attachments_pending (created_at);
ALTER TABLE clients
    ADD COLUMN IF NOT EXISTS logo_storage_path     text DEFAULT '',
    ADD COLUMN IF NOT EXISTS template_storage_path text DEFAULT '';
