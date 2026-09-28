-- KanoFarm AI — migration 0001: core farmer/farm/weather/alert tables (Supabase / PostgreSQL)
-- No seed data of any kind. Reference data (crops, LGAs, pesticides) must come from cited sources.

create extension if not exists pgcrypto;

create type data_type as enum
  ('REAL_TIME','NEAR_REAL_TIME','HISTORICAL','MODELLED','SATELLITE','USER_PROVIDED','DEMO');

create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

-- Provenance registry: every external number points here
create table data_sources (
  id            uuid primary key default gen_random_uuid(),
  name          text not null unique,
  url           text not null,
  data_kind     data_type not null,
  license       text not null,
  commercial_use_allowed boolean,
  attribution   text,
  limitations   text not null,
  last_verified date not null,
  created_at    timestamptz not null default now()
);

create table profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  full_name     text not null check (char_length(full_name) between 1 and 120),
  language      text not null default 'en' check (language in ('en','ha','yo','ig','pcm')),
  phone         text,
  state         text not null,   -- one of Nigeria's 36 states or 'Federal Capital Territory'
  lga           text,
  ward          text,
  community     text,
  experience_years smallint check (experience_years between 0 and 80),
  farm_type     text,
  is_demo       boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger trg_profiles_upd before update on profiles for each row execute function set_updated_at();

create table crops (
  id            uuid primary key default gen_random_uuid(),
  slug          text not null unique check (slug ~ '^[a-z0-9_]+$'),
  name_en       text not null,
  name_ha       text,                 -- NULL until reviewed by a native-speaking agronomist
  created_at    timestamptz not null default now()
);

create table farms (
  id            uuid primary key default gen_random_uuid(),
  owner_id      uuid not null references profiles(id) on delete cascade,
  name          text not null check (char_length(name) between 1 and 120),
  state         text,   -- one of Nigeria's 36 states or 'Federal Capital Territory'; may differ from the owner's home state
  lga           text, ward text, community text,
  latitude      numeric(9,6) not null check (latitude between 4 and 14),    -- Nigeria bbox (covers every state)
  longitude     numeric(9,6) not null check (longitude between 2.5 and 15),
  size_ha       numeric(10,2) check (size_ha > 0),
  irrigation_type text,
  notes         text,
  is_demo       boolean not null default false,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index farms_owner_idx on farms(owner_id);
create trigger trg_farms_upd before update on farms for each row execute function set_updated_at();

create table farm_crops (
  id            uuid primary key default gen_random_uuid(),
  farm_id       uuid not null references farms(id) on delete cascade,
  crop_id       uuid not null references crops(id),
  variety       text,
  planting_date date not null,
  expected_harvest_date date check (expected_harvest_date is null or expected_harvest_date >= planting_date),
  created_at    timestamptz not null default now()
);
create index farm_crops_farm_idx on farm_crops(farm_id);

-- Weather cache: one row per 0.1° grid cell and fetch
create table weather_cache (
  id            uuid primary key default gen_random_uuid(),
  grid_lat      numeric(5,1) not null,
  grid_lon      numeric(5,1) not null,
  source_id     uuid not null references data_sources(id),
  data_kind     data_type not null default 'MODELLED',
  payload       jsonb not null,
  fetched_at    timestamptz not null default now()
);
create index weather_cache_lookup_idx on weather_cache(grid_lat, grid_lon, fetched_at desc);

create table alert_rules (
  id            uuid primary key default gen_random_uuid(),
  farm_id       uuid not null references farms(id) on delete cascade,
  alert_type    text not null check (alert_type in
                ('heavy_rain','dry_spell','heat_stress','high_water_demand','rain_delay_irrigation')),
  threshold     numeric,             -- NULL = use documented default
  enabled       boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (farm_id, alert_type)
);

create table alerts (
  id            uuid primary key default gen_random_uuid(),
  farm_id       uuid not null references farms(id) on delete cascade,
  alert_type    text not null,
  level         text not null check (level in ('info','watch','warning')),
  message_key   text not null,
  evidence      jsonb not null,      -- the exact numbers that triggered it
  data_kind     data_type not null,
  created_at    timestamptz not null default now(),
  read_at       timestamptz
);
create index alerts_farm_idx on alerts(farm_id, created_at desc);

-- Row Level Security: farmers see only their own data
alter table profiles     enable row level security;
alter table farms        enable row level security;
alter table farm_crops   enable row level security;
alter table alert_rules  enable row level security;
alter table alerts       enable row level security;
alter table crops        enable row level security;
alter table data_sources enable row level security;
alter table weather_cache enable row level security;  -- no policy: service role only

create policy profiles_self on profiles for all using (id = auth.uid()) with check (id = auth.uid());
create policy farms_owner on farms for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy farm_crops_owner on farm_crops for all
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()))
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy alert_rules_owner on alert_rules for all
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()))
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy alerts_owner on alerts for select
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy crops_read on crops for select using (true);
create policy sources_read on data_sources for select using (true);
