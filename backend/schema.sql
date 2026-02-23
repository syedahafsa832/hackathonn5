-- CUSTOMERS TABLE
create table if not exists customers (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid default '00000000-0000-0000-0000-000000000000', -- For isolation
    email text unique not null,
    name text,
    phone text,
    company text,
    created_at timestamp with time zone default now()
);

-- TICKETS TABLE (Structured AI Support)
create table if not exists tickets (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid default '00000000-0000-0000-0000-000000000000',
    customer_name text,
    customer_email text,
    subject text,
    message text,
    ai_reply text,
    ai_draft text, -- NEW: For paused mode
    intent text,
    sentiment text,
    risk_level text, -- low, medium, high
    confidence_score integer, -- 0-100
    escalate boolean default false,
    escalation_reason text,
    status text default 'open', -- open, escalated, ai_suggested, auto_resolved, closed, human_managing
    created_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

-- SYSTEM SETTINGS (AI Control Panel)
create table if not exists system_settings (
    store_id uuid primary key default '00000000-0000-0000-0000-000000000000',
    ai_mode text default 'active', -- active, paused, manual
    confidence_threshold float default 0.75,
    data_retention_days integer default 180,
    updated_at timestamp with time zone default now()
);

-- CONVERSATION OVERRIDES (Human Takeover)
create table if not exists conversation_overrides (
    id uuid primary key default uuid_generate_v4(),
    conversation_id uuid not null, -- references tickets(id)
    overridden_by text, -- user_id or email
    override_type text default 'human_takeover',
    active boolean default true,
    created_at timestamp with time zone default now()
);

-- AUDIT LOGS (Compliance & Tracking)
create table if not exists audit_logs (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid,
    action_type text not null, -- mode_change, takeover, erasure, export
    performed_by text,
    metadata jsonb,
    created_at timestamp with time zone default now()
);

-- KNOWLEDGE BASE TABLE
create table if not exists knowledge_base (
    id uuid primary key default uuid_generate_v4(),
    store_id uuid default '00000000-0000-0000-0000-000000000000',
    title text not null,
    content text not null,
    category text,
    tags text[],
    created_at timestamp with time zone default now()
);

-- SETTINGS TABLE (Legacy Tokens/Config)
create table if not exists settings (
    key text primary key,
    store_id uuid default '00000000-0000-0000-0000-000000000000',
    value jsonb not null,
    updated_at timestamp with time zone default now()
);

-- INDEXES for Performance
create index if not exists idx_tickets_status on tickets(status);
create index if not exists idx_tickets_created_at on tickets(created_at);
create index if not exists idx_tickets_store_id on tickets(store_id);
create index if not exists idx_overrides_active on conversation_overrides(active);
create index if not exists idx_overrides_convo on conversation_overrides(conversation_id);

-- AUTO-UPDATE updated_at trigger
create or replace function update_updated_at_column()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger update_tickets_updated_at before update on tickets for each row execute function update_updated_at_column();
create trigger update_sys_settings_updated_at before update on system_settings for each row execute function update_updated_at_column();
