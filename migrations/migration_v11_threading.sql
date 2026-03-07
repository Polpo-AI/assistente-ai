-- migration_v11_threading.sql
ALTER TABLE emails
    ADD COLUMN IF NOT EXISTS direction      text    DEFAULT 'inbound' CHECK (direction IN ('inbound', 'outbound')),
    ADD COLUMN IF NOT EXISTS message_id     text    DEFAULT '',
    ADD COLUMN IF NOT EXISTS in_reply_to    text    DEFAULT '',
    ADD COLUMN IF NOT EXISTS references_ids text[]  DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS quoted_nested  boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS thread_topic   text    DEFAULT '';
ALTER TABLE draft_responses
    ADD COLUMN IF NOT EXISTS sent_message_id text DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_emails_message_id  ON emails (message_id)  WHERE message_id  != '';
CREATE INDEX IF NOT EXISTS idx_emails_in_reply_to ON emails (in_reply_to) WHERE in_reply_to != '';
CREATE INDEX IF NOT EXISTS idx_emails_direction   ON emails (direction);
