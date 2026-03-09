-- ═══════════════════════════════════════════════════════════════
-- Polpo AI - Schema Supabase Completo (Consolidato v13)
-- ═══════════════════════════════════════════════════════════════

create extension if not exists "pgcrypto";

-- ─────────────────────────────────────────────
-- UTILITY — trigger updated_at
-- ─────────────────────────────────────────────
create or replace function update_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

-- ─────────────────────────────────────────────
-- CLIENTS — Un record per ogni cliente Polpo AI
-- ─────────────────────────────────────────────
create table if not exists clients (
    id                   uuid primary key default gen_random_uuid(),
    name                 text not null,
    sector               text not null,

    -- Configurazione LLM
    llm_persona          text not null,            
    llm_tone             text default 'professionale e cordiale',
    signature            text,                     

    -- Intenti
    intent_list          jsonb not null default '["preventivo","appuntamento","informazione","reclamo","pagamento","spam","altro"]',
    intent_keywords      jsonb default '{}',
    intent_instructions  jsonb default '{}',

    -- Business Profile
    business_description text default '',
    services_offered     text default '',
    priority_map         jsonb default '{}',

    -- File / Assets
    logo_storage_path     text default '',
    template_storage_path text default '',

    -- Mail Credentials (IMAP/SMTP)
    imap_host            text,
    imap_port            integer default 993,
    imap_user            text,
    imap_password        text,
    imap_last_uid        integer default 0,
    smtp_host            text,
    smtp_port            integer default 587,
    smtp_user            text,
    smtp_password        text,

    -- Custom
    contacts             jsonb default '{}',
    orari                jsonb default '{}',
    faq                  jsonb default '[]',
    language             text default 'Italiano',
    custom_spam_keywords jsonb default '[]',

    -- Integrazione Telegram
    telegram_chat_id     text,

    active               boolean default true,
    created_at           timestamptz default now(),
    updated_at           timestamptz default now()
);

create trigger clients_updated_at
    before update on clients
    for each row execute function update_updated_at();

-- ─────────────────────────────────────────────
-- CONTACTS — Rubrica mittenti (isolata per cliente)
-- ─────────────────────────────────────────────
create table if not exists contacts (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references clients(id) on delete cascade,
    email           text not null,
    name            text,
    contact_type    text not null default 'sconosciuto'
                        check (contact_type in ('cliente','fornitore','spam','personale','sconosciuto', 'bot')),
    notes           text,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),
    unique(client_id, email)
);

create trigger contacts_updated_at
    before update on contacts
    for each row execute function update_updated_at();

-- ─────────────────────────────────────────────
-- CONVERSATIONS — Thread email
-- ─────────────────────────────────────────────
create table if not exists conversations (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references clients(id) on delete cascade,
    contact_id      uuid references contacts(id) on delete set null,
    subject_thread  text,
    email_count     int default 1,
    last_email_at   timestamptz default now(),
    created_at      timestamptz default now()
);

-- ─────────────────────────────────────────────
-- EMAILS — Archivio email
-- ─────────────────────────────────────────────
create table if not exists emails (
    id                      uuid primary key default gen_random_uuid(),
    client_id               uuid not null references clients(id) on delete cascade,
    contact_id              uuid references contacts(id) on delete set null,
    conversation_id         uuid references conversations(id) on delete set null,
    sender_email            text not null,
    sender_name             text,
    subject                 text,
    body                    text,
    attachments             jsonb default '[]',
    received_at             timestamptz default now(),
    raw_headers             jsonb,
    
    -- Enrichments (v10)
    quoted_text             text default '',
    attachments_text        jsonb default '{}',
    detected_language       text default '',
    draft_generation_status text default 'pending',

    -- Threading (v11)
    direction               text default 'inbound' check (direction in ('inbound', 'outbound')),
    message_id              text default '',
    in_reply_to             text default '',
    references_ids          text[] default '{}',
    quoted_nested           boolean default false,
    thread_topic            text default ''
);

-- ─────────────────────────────────────────────
-- EMAIL_CLASSIFICATIONS — Risultati pipeline
-- ─────────────────────────────────────────────
create table if not exists email_classifications (
    id              uuid primary key default gen_random_uuid(),
    email_id        uuid not null references emails(id) on delete cascade,
    contact_type    text not null
                        check (contact_type in ('cliente','fornitore','spam','personale','sconosciuto', 'bot')),
    intent          text not null,
    priority        int not null check (priority in (0,1,2,3)),
    confidence      float not null check (confidence >= 0 and confidence <= 1),
    classified_by   text not null,
    summary         text,
    estimated_value float,
    created_at      timestamptz default now()
);

-- ─────────────────────────────────────────────
-- DRAFT_RESPONSES — Bozze del Responder
-- ─────────────────────────────────────────────
create table if not exists draft_responses (
    id                  uuid primary key default gen_random_uuid(),
    email_id            uuid not null references emails(id) on delete cascade unique,
    client_id           uuid not null references clients(id) on delete cascade,
    subject             text,
    body                text,
    suggested_actions   jsonb default '[]',
    final_intent        text,
    reclassified        boolean default false,
    warning             text,
    status              text default 'pending'
                            check (status in ('pending','approved','rejected','sent','ignored')),
    send_error          text,
    telegram_message_id bigint,
    sent_message_id     text default '',
    approved_by         text,
    approved_at         timestamptz,
    sent_at             timestamptz,
    created_at          timestamptz default now()
);

-- ─────────────────────────────────────────────
-- CHAT HISTORY — Operatore Assistente Bot
-- ─────────────────────────────────────────────
create table if not exists chat_history (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references clients(id) on delete cascade,
    chat_id         text not null,
    role            text not null check (role in ('user', 'assistant', 'system')),
    content         jsonb not null,
    created_at      timestamptz default now()
);

-- ─────────────────────────────────────────────
-- ATTACHMENTS PENDING
-- ─────────────────────────────────────────────
create table if not exists email_attachments_pending (
    id         uuid        primary key default gen_random_uuid(),
    email_id   uuid        not null references emails(id) on delete cascade,
    filename   text        not null,
    mime_type  text        not null default '',
    data_b64   text        not null,
    size_bytes integer     not null default 0,
    created_at timestamptz not null default now(),
    unique (email_id, filename)
);

-- ─────────────────────────────────────────────
-- INDICI
-- ─────────────────────────────────────────────
create index if not exists idx_contacts_client       on contacts(client_id);
create index if not exists idx_contacts_email        on contacts(client_id, email);

create index if not exists idx_emails_client         on emails(client_id);
create index if not exists idx_emails_contact        on emails(contact_id);
create index if not exists idx_emails_conversation   on emails(conversation_id);
create index if not exists idx_emails_received       on emails(received_at desc);
create index if not exists idx_emails_message_id     on emails(message_id) where message_id != '';
create index if not exists idx_emails_in_reply_to    on emails(in_reply_to) where in_reply_to != '';
create index if not exists idx_emails_direction      on emails(direction);

create index if not exists idx_class_email           on email_classifications(email_id);
create index if not exists idx_class_intent          on email_classifications(intent);
create index if not exists idx_class_priority        on email_classifications(priority desc);

create index if not exists idx_drafts_client         on draft_responses(client_id);
create index if not exists idx_drafts_status         on draft_responses(status);

create index if not exists idx_chat_client           on chat_history(client_id);
create index if not exists idx_chat_composite        on chat_history(chat_id, created_at asc);

create index if not exists idx_att_pending_created   on email_attachments_pending(created_at);

-- ─────────────────────────────────────────────
-- VIEW — Dashboard email
-- ─────────────────────────────────────────────
create or replace view v_emails_classified as
select
    e.id,
    e.client_id,
    e.conversation_id,
    e.received_at,
    e.sender_email,
    e.sender_name,
    e.subject,
    e.body,
    e.attachments,
    c.contact_type   as contact_type_db,
    cl.contact_type  as contact_type_classified,
    cl.intent,
    cl.priority,
    cl.confidence,
    cl.classified_by,
    cl.summary,
    cl.estimated_value,
    dr.id            as draft_id,
    dr.status        as draft_status,
    dr.subject       as draft_subject,
    dr.body          as draft_body,
    dr.warning       as draft_warning,
    dr.suggested_actions as draft_actions
from emails e
left join contacts c               on c.id = e.contact_id
left join email_classifications cl on cl.email_id = e.id
left join draft_responses dr       on dr.email_id = e.id
order by e.received_at desc;

-- ─────────────────────────────────────────────
-- RPC DASHBOARD (Raggruppamenti su Server)
-- ─────────────────────────────────────────────

create or replace function rpc_top_senders(p_client_id uuid, p_date_from timestamptz, p_date_to timestamptz, p_limit int)
returns table(sender_email varchar, count bigint) as $$
begin
    return query
    select e.sender_email::varchar, count(*) as count
    from emails e
    where e.client_id = p_client_id
      and e.received_at >= p_date_from
      and e.received_at <= p_date_to
    group by e.sender_email
    order by count desc
    limit p_limit;
end;
$$ language plpgsql;

create or replace function rpc_daily_volume(p_client_id uuid, p_date_from timestamptz, p_date_to timestamptz)
returns table(day date, received bigint, sent bigint) as $$
begin
    return query
    with dates as (
        select generate_series(p_date_from::date, p_date_to::date, '1 day'::interval)::date as day
    ),
    rx as (
        select received_at::date as day, count(*) as c
        from emails
        where client_id = p_client_id and direction = 'inbound' and received_at >= p_date_from and received_at <= p_date_to
        group by 1
    ),
    tx as (
        select sent_at::date as day, count(*) as c
        from draft_responses
        where client_id = p_client_id and status = 'sent' and sent_at >= p_date_from and sent_at <= p_date_to
        group by 1
    )
    select
        d.day,
        coalesce(r.c, 0) as received,
        coalesce(t.c, 0) as sent
    from dates d
    left join rx r on d.day = r.day
    left join tx t on d.day = t.day
    order by d.day;
end;
$$ language plpgsql;

-- ─────────────────────────────────────────────
-- CLIENTE DEMO per test
-- ─────────────────────────────────────────────
insert into clients (name, sector, llm_persona, llm_tone, signature, intent_list, intent_instructions)
values (
    'Officina Demo',
    'automotive',
    'Sei il classificatore email di un''officina/carrozzeria italiana professionale.',
    'professionale e cordiale',
    'Cordiali saluti,' || chr(10) || 'Officina Demo',
    '["preventivo","appuntamento","informazione","reclamo","pagamento","spam","altro"]',
    '{
        "preventivo":   "Ringrazia, chiedi modello auto e tipo danno, proponi sopralluogo gratuito.",
        "reclamo":      "Scusati sinceramente, proponi verifica gratuita, dai contatto diretto [TELEFONO].",
        "appuntamento": "Proponi 2-3 fasce [GIORNO] [ORA], ricorda libretto veicolo."
    }'
) on conflict do nothing;

