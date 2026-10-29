alter table public.courses add column if not exists details jsonb not null default '{}'::jsonb;
