-- ─────────────────────────────────────────────
-- MIGRATION V7 - CHAT HISTORY
-- ─────────────────────────────────────────────

create table if not exists chat_history (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references clients(id) on delete cascade,
    chat_id         text not null,
    role            text not null check (role in ('user', 'assistant')),
    content         jsonb not null,
    created_at      timestamptz default now()
);

create index if not exists idx_chat_client on chat_history(client_id);
create index if not exists idx_chat_composite on chat_history(chat_id, created_at asc);
