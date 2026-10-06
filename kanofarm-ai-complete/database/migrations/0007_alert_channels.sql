-- KanoFarm AI — migration 0007: alerts delivered outside the app (web push first; WhatsApp/SMS only if a provider is configured).
-- Requires 0001 and 0002. Safe to run once.
--
-- Design notes
--  * The backend still holds NO service-role key. A scheduled job (Vercel Cron) cannot carry a farmer's JWT, so it calls
--    the SECURITY DEFINER functions below, which only answer when given the correct cron secret (stored here only as a hash).
--  * Phone numbers are stored only with explicit consent, in the same +234XXXXXXXXXX form the Market uses.
--  * Every limit that matters is a database constraint, not only a Python check.

create table alert_prefs (
  user_id          uuid primary key references profiles(id) on delete cascade,
  push_enabled     boolean not null default false,
  whatsapp_enabled boolean not null default false,
  sms_enabled      boolean not null default false,
  phone            text check (phone is null or phone ~ '^\+234[0-9]{10}$'),
  phone_consent    boolean not null default false,
  min_level        text not null default 'watch' check (min_level in ('info','watch','warning')),
  stage_reminders  boolean not null default true,     -- gentle "new growth stage" reminders; separate from weather levels
  quiet_start      smallint check (quiet_start between 0 and 23),     -- Africa/Lagos hour; alerts are not sent from start up to (not including) end
  quiet_end        smallint check (quiet_end between 0 and 23),
  updated_at       timestamptz not null default now(),
  -- a phone channel can only be on when a number AND consent are stored
  constraint alert_prefs_phone_needs_consent
    check (not (whatsapp_enabled or sms_enabled) or (phone is not null and phone_consent)),
  constraint alert_prefs_quiet_pair check ((quiet_start is null) = (quiet_end is null))
);
create trigger trg_alert_prefs_upd before update on alert_prefs for each row execute function set_updated_at();

create table push_subscriptions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references profiles(id) on delete cascade,
  endpoint   text not null unique check (char_length(endpoint) between 20 and 1000),
  p256dh     text not null check (char_length(p256dh) between 20 and 200),
  auth       text not null check (char_length(auth) between 8 and 100),
  created_at timestamptz not null default now()
);
create index push_subscriptions_user_idx on push_subscriptions(user_id);

-- One row per alert actually sent (or being sent). The unique key is what stops duplicates.
create table alert_deliveries (
  id          uuid primary key default gen_random_uuid(),
  farm_id     uuid not null references farms(id) on delete cascade,
  message_key text not null,
  alert_date  date not null,
  channel     text not null check (channel in ('push','whatsapp','sms')),
  created_at  timestamptz not null default now(),
  unique (farm_id, message_key, alert_date, channel)
);

create table cron_secrets (
  name        text primary key,
  secret_hash text not null
);

alter table alert_prefs        enable row level security;
alter table push_subscriptions enable row level security;
alter table alert_deliveries   enable row level security;
alter table cron_secrets       enable row level security;   -- no policy at all: unreadable through the API

create policy alert_prefs_owner on alert_prefs for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy push_subs_owner   on push_subscriptions for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy deliveries_owner_read on alert_deliveries for select
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));

-- A user may keep at most 5 devices subscribed.
create or replace function limit_push_subscriptions() returns trigger language plpgsql as $$
begin
  if (select count(*) from push_subscriptions where user_id = new.user_id) >= 5 then
    raise exception 'too many subscribed devices';
  end if;
  return new;
end $$;
create trigger trg_push_subs_limit before insert on push_subscriptions for each row execute function limit_push_subscriptions();

-- ---------------------------------------------------------------- cron-only functions
create or replace function cron_secret_ok(p_secret text) returns boolean
language sql stable security definer set search_path = public as $$
  select coalesce(p_secret, '') <> '' and exists (
    select 1 from cron_secrets
    where name = 'alerts' and secret_hash = encode(sha256(convert_to(p_secret, 'UTF8')), 'hex'));
$$;

-- Farms whose owner switched at least one channel on, with the farm's own custom alert thresholds.
create or replace function cron_alert_targets(p_secret text, p_limit int default 200)
returns table (user_id uuid, farm_id uuid, farm_name text, latitude numeric, longitude numeric, language text,
               push_enabled boolean, whatsapp_enabled boolean, sms_enabled boolean, phone text,
               min_level text, stage_reminders boolean, quiet_start smallint, quiet_end smallint, rules jsonb)
language plpgsql stable security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  return query
    select f.owner_id, f.id, f.name, f.latitude, f.longitude, p.language,
           a.push_enabled, a.whatsapp_enabled, a.sms_enabled,
           case when a.phone_consent then a.phone else null end,
           a.min_level, a.stage_reminders, a.quiet_start, a.quiet_end,
           coalesce((select jsonb_agg(to_jsonb(r)) from alert_rules r where r.farm_id = f.id), '[]'::jsonb)
    from alert_prefs a
    join farms f    on f.owner_id = a.user_id
    join profiles p on p.id = a.user_id
    where a.push_enabled or a.whatsapp_enabled or a.sms_enabled
    order by f.id
    limit greatest(1, least(coalesce(p_limit, 200), 1000));
end $$;

create or replace function cron_push_subscriptions(p_secret text, p_users uuid[])
returns table (user_id uuid, endpoint text, p256dh text, auth text)
language plpgsql stable security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  return query select s.user_id, s.endpoint, s.p256dh, s.auth from push_subscriptions s where s.user_id = any(p_users);
end $$;

-- Crops on the given farms (for growth-stage reminders). Same secret check; returns no personal data beyond what the farm already holds.
create or replace function cron_farm_crops(p_secret text, p_farms uuid[])
returns table (farm_id uuid, slug text, name_en text, planting_date date, expected_harvest_date date)
language plpgsql stable security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  return query
    select fc.farm_id, c.slug, c.name_en, fc.planting_date, fc.expected_harvest_date
    from farm_crops fc join crops c on c.id = fc.crop_id
    where fc.farm_id = any(p_farms);
end $$;

-- Returns true only for the first caller for this (farm, message, day, channel): that caller sends.
create or replace function cron_alert_claim(p_secret text, p_farm uuid, p_key text, p_date date, p_channel text)
returns boolean language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  insert into alert_deliveries (farm_id, message_key, alert_date, channel) values (p_farm, p_key, p_date, p_channel)
    on conflict do nothing;
  get diagnostics n = row_count;
  return n = 1;
end $$;

-- If sending failed, give the claim back so the next run can retry.
create or replace function cron_alert_release(p_secret text, p_farm uuid, p_key text, p_date date, p_channel text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  delete from alert_deliveries where farm_id = p_farm and message_key = p_key and alert_date = p_date and channel = p_channel;
end $$;

-- The push service said the device is gone (404/410): forget it.
create or replace function cron_drop_subscription(p_secret text, p_endpoint text)
returns void language plpgsql security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  delete from push_subscriptions where endpoint = p_endpoint;
end $$;

revoke all on function cron_secret_ok(text), cron_alert_targets(text, int), cron_push_subscriptions(text, uuid[]), cron_farm_crops(text, uuid[]),
  cron_alert_claim(text, uuid, text, date, text), cron_alert_release(text, uuid, text, date, text),
  cron_drop_subscription(text, text) from public;
grant execute on function cron_alert_targets(text, int), cron_push_subscriptions(text, uuid[]), cron_farm_crops(text, uuid[]),
  cron_alert_claim(text, uuid, text, date, text), cron_alert_release(text, uuid, text, date, text),
  cron_drop_subscription(text, text) to anon, authenticated;

-- AFTER running this file, set the cron secret ONCE (replace LONG-RANDOM-SECRET with the same value you put in
-- Vercel as CRON_SECRET). Only a hash is stored:
--   insert into cron_secrets (name, secret_hash)
--   values ('alerts', encode(sha256(convert_to('LONG-RANDOM-SECRET', 'UTF8')), 'hex'))
--   on conflict (name) do update set secret_hash = excluded.secret_hash;
