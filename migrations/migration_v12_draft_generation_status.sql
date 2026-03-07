-- migration_v12_draft_generation_status.sql
ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS draft_generation_status TEXT DEFAULT 'pending';
