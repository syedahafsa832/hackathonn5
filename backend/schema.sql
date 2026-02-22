-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- CUSTOMERS TABLE
create table if not exists customers (
    id uuid primary key default uuid_generate_v4(),
    email text unique not null,
    name text,
    phone text,
    company text,
    created_at timestamp with time zone default now()
);

-- TICKETS TABLE (Structured AI Support)
create table if not exists tickets (
    id uuid primary key default uuid_generate_v4(),
    customer_name text,
    customer_email text,
    subject text,
    message text,
    ai_reply text,
    intent text,
    sentiment text,
    risk_level text, -- low, medium, high
    confidence_score integer, -- 0-100
    escalate boolean default false,
    escalation_reason text,
    status text default 'open', -- open, escalated, auto_resolved, closed
    created_at timestamp with time zone default now(),
    updated_at timestamp with time zone default now()
);

-- KNOWLEDGE BASE TABLE (For RAG)
create table if not exists knowledge_base (
    id uuid primary key default uuid_generate_v4(),
    title text not null,
    content text not null,
    category text,
    tags text[],
    created_at timestamp with time zone default now()
);

-- INDEXES for Performance
create index if not exists idx_tickets_status on tickets(status);
create index if not exists idx_tickets_created_at on tickets(created_at);
create index if not exists idx_tickets_escalate on tickets(escalate);
create index if not exists idx_tickets_customer_email on tickets(customer_email);
create index if not exists idx_customers_email on customers(email);

-- AUTO-UPDATE updated_at trigger
create or replace function update_updated_at_column()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger update_tickets_updated_at
before update on tickets
for each row
execute function update_updated_at_column();
