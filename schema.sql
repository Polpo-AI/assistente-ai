-- ═══════════════════════════════════════════════════════════════
-- Polpo AI - Email Bot Schema Supabase v2 (Multi-Tenant)
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
    llm_persona          text not null,            -- prompt base del classificatore
    llm_tone             text default 'professionale e cordiale',
    signature            text,                     -- firma per le bozze risposta

    -- Intenti personalizzati per settore
    -- es. dentista: ["visita","urgenza","preventivo","info","reclamo","pagamento","spam","altro"]
    intent_list          jsonb not null default
        '["preventivo","appuntamento","informazione","reclamo","pagamento","spam","altro"]',

    -- Keyword spam extra per questo cliente
    custom_spam_keywords jsonb default '[]',

    -- Istruzioni risposta per intent (sovrascrivono i default del responder)
    -- { "reclamo": "Scusati, proponi verifica gratuita...", "preventivo": "..." }
    intent_instructions  jsonb default '{}',

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
                        check (contact_type in ('cliente','fornitore','spam','personale','sconosciuto')),
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
-- EMAILS — Archivio email ricevute
-- ─────────────────────────────────────────────
create table if not exists emails (
    id              uuid primary key default gen_random_uuid(),
    client_id       uuid not null references clients(id) on delete cascade,
    contact_id      uuid references contacts(id) on delete set null,
    conversation_id uuid references conversations(id) on delete set null,
    sender_email    text not null,
    sender_name     text,
    subject         text,
    body            text,
    attachments     jsonb default '[]',
    received_at     timestamptz default now(),
    raw_headers     jsonb
);

-- ─────────────────────────────────────────────
-- EMAIL_CLASSIFICATIONS — Risultati pipeline
-- intent è TEXT libero: ogni cliente ha i suoi intent
-- ─────────────────────────────────────────────
create table if not exists email_classifications (
    id              uuid primary key default gen_random_uuid(),
    email_id        uuid not null references emails(id) on delete cascade,
    contact_type    text not null
                        check (contact_type in ('cliente','fornitore','spam','personale','sconosciuto')),
    intent          text not null,
    priority        int not null check (priority in (1,2,3)),
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
    id                uuid primary key default gen_random_uuid(),
    email_id          uuid not null references emails(id) on delete cascade,
    client_id         uuid not null references clients(id) on delete cascade,
    subject           text,
    body              text,
    suggested_actions jsonb default '[]',
    final_intent      text,
    reclassified      boolean default false,
    warning           text,
    status            text default 'pending'
                          check (status in ('pending','approved','rejected','sent')),
    approved_by       text,
    approved_at       timestamptz,
    sent_at           timestamptz,
    created_at        timestamptz default now()
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
create index if not exists idx_class_email           on email_classifications(email_id);
create index if not exists idx_class_intent          on email_classifications(intent);
create index if not exists idx_class_priority        on email_classifications(priority desc);
create index if not exists idx_drafts_client         on draft_responses(client_id);
create index if not exists idx_drafts_status         on draft_responses(status);

-- ─────────────────────────────────────────────
-- VIEW — Dashboard email (FIX v2: aggiunge body, conversation_id, bozza)
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
