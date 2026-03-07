-- Migration v8 — Unique constraint for draft_responses(email_id)
-- Necessario per l'operazione upsert in mark_email_no_reply

alter table draft_responses
add constraint draft_responses_email_id_key unique (email_id);
