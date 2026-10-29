create table if not exists public.conversations (
    id text primary key,
    data jsonb not null,
    updated_at timestamptz not null default now()
);
create index if not exists conversations_updated_at_idx on public.conversations(updated_at desc);
alter table public.conversations enable row level security;
revoke all on public.conversations from anon, authenticated;
grant select, insert, update on public.conversations to service_role;

create table if not exists public.courses (
    id bigint generated always as identity primary key,
    titulo text not null,
    estado text not null,
    duracion text,
    contenido text,
    enlace text,
    is_demo boolean not null default false,
    details jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
alter table public.courses enable row level security;
revoke all on public.courses from anon, authenticated;
grant select, insert, update on public.courses to service_role;
grant usage, select on sequence public.courses_id_seq to service_role;
