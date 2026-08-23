-- Run this once in Supabase Dashboard → SQL Editor.
-- The Streamlit app uses the server-side service key, stored only in Streamlit Secrets.

create table if not exists public.members (
    google_subject text primary key,
    email text not null unique,
    display_name text not null default '',
    avatar_url text not null default '',
    status text not null default 'pending' check (status in ('pending', 'active', 'suspended')),
    plan text not null default 'none' check (plan in ('none', 'simple', 'pro')),
    plan_expires_at timestamptz,
    admin_note text not null default '',
    registered_at timestamptz not null default now(),
    approved_at timestamptz,
    updated_at timestamptz not null default now()
);

create table if not exists public.export_usage (
    id bigint generated always as identity primary key,
    google_subject text not null references public.members(google_subject) on delete cascade,
    plan text not null check (plan in ('simple', 'pro')),
    export_day date not null,
    source_duration_seconds numeric(10, 2) not null check (source_duration_seconds >= 0),
    outcome text not null check (outcome in ('success', 'failed')),
    completed_at timestamptz not null default now()
);

-- A Pro member may have exactly one successful export per Myanmar calendar day.
create unique index if not exists one_successful_pro_export_per_day
    on public.export_usage (google_subject, export_day)
    where plan = 'pro' and outcome = 'success';

create table if not exists public.member_audit (
    id bigint generated always as identity primary key,
    google_subject text not null references public.members(google_subject) on delete cascade,
    action text not null check (action in ('registered', 'approved_simple', 'approved_pro', 'suspended', 'renewed')),
    actor text not null,
    note text not null default '',
    created_at timestamptz not null default now()
);

create or replace function public.set_members_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists members_updated_at on public.members;
create trigger members_updated_at
before update on public.members
for each row execute function public.set_members_updated_at();

alter table public.members enable row level security;
alter table public.export_usage enable row level security;
alter table public.member_audit enable row level security;

-- Do not add anonymous-client policies. The Streamlit server uses the service key
-- from Streamlit Secrets and is the only process allowed to read or update membership data.
