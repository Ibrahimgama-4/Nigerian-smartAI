-- KanoFarm AI — migration 0011: "Input safety" reports and the soil record / fertiliser plan.
-- Requires 0001, 0002 (soil_records, is_admin) and 0010. Safe to run once.
--
-- WHAT THIS ADDS
--   * soil_records gets two structured test columns (Bray-1 phosphorus in mg/kg and exchangeable potassium in cmol/kg) so the app
--     can apply the published "no P / no K needed" levels without guessing units.
--   * fertilizer_recommendations: a reference table that ONLY an administrator fills, one row per sourced recommendation.
--     It ships EMPTY. The app never invents a fertiliser rate: with no matching sourced row it says so.
--   * input_reports: a farmer can privately report a suspected fake, banned or expired farm input. Only the reporter and
--     administrators can read it. Nothing is published and no seller is named to other farmers.

-- ---------------------------------------------------------------- soil record
alter table soil_records add column if not exists p_bray1_ppm    numeric check (p_bray1_ppm is null or p_bray1_ppm between 0 and 1000);
alter table soil_records add column if not exists k_exch_cmolkg  numeric check (k_exch_cmolkg is null or k_exch_cmolkg between 0 and 20);
alter table soil_records add constraint soil_recorded_not_future check (recorded_on <= current_date + 1);
alter table soil_records add column if not exists lab_name text check (lab_name is null or char_length(lab_name) <= 120);
create index if not exists soil_records_farm_idx on soil_records(farm_id, recorded_on desc);

-- A farm keeps at most 200 soil records (stops a script filling the table).
create or replace function soil_records_cap() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from soil_records where farm_id = new.farm_id) >= 200 then
    raise exception 'this farm already has 200 soil records';
  end if;
  return new;
end $$;
create trigger trg_soil_cap before insert on soil_records for each row execute function soil_records_cap();

-- ---------------------------------------------------------------- sourced fertiliser recommendations (admin-entered, ships empty)
create table fertilizer_recommendations (
  id            uuid primary key default gen_random_uuid(),
  zone          text not null check (zone in ('all','north_west','north_east','north_central','south_west','south_east','south_south')),
  crop_slug     text not null references crops(slug),
  n_kg_ha       numeric check (n_kg_ha     is null or n_kg_ha     between 0 and 500),
  p2o5_kg_ha    numeric check (p2o5_kg_ha  is null or p2o5_kg_ha  between 0 and 500),
  k2o_kg_ha     numeric check (k2o_kg_ha   is null or k2o_kg_ha   between 0 and 500),
  notes         text check (notes is null or char_length(notes) <= 600),   -- e.g. "split N: half at planting, half 4-6 weeks later" ONLY if the source says so
  source_name   text not null check (char_length(source_name) between 3 and 200),
  source_url    text check (source_url is null or source_url ~ '^https?://'),
  source_year   smallint check (source_year is null or source_year between 1980 and 2100),
  last_verified date not null,
  expires_at    date not null,
  entered_by    uuid references auth.users(id),
  created_at    timestamptz not null default now(),
  constraint fert_has_a_rate check (n_kg_ha is not null or p2o5_kg_ha is not null or k2o_kg_ha is not null),
  constraint fert_expiry_after_verified check (expires_at > last_verified)
);
create unique index fert_reco_unique on fertilizer_recommendations(zone, crop_slug, source_name);
alter table fertilizer_recommendations enable row level security;
create policy fert_reco_read  on fertilizer_recommendations for select to authenticated using (expires_at >= current_date or is_admin());
create policy fert_reco_admin on fertilizer_recommendations for all    to authenticated using (is_admin()) with check (is_admin() and entered_by = auth.uid());

-- ---------------------------------------------------------------- private reports of suspicious farm inputs
create table input_reports (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  kind         text not null check (kind in ('agrochemical','fertilizer','seed','other')),
  product_name text not null check (char_length(product_name) between 2 and 120),
  problem      text not null check (char_length(problem) between 5 and 500),
  state        text check (state is null or char_length(state) <= 40),
  lga          text check (lga   is null or char_length(lga)   <= 80),
  bought_on    date check (bought_on is null or bought_on <= current_date),
  status       text not null default 'new' check (status in ('new','reviewed','forwarded','closed')),
  admin_note   text check (admin_note is null or char_length(admin_note) <= 300),
  reviewed_by  uuid references auth.users(id),
  reviewed_at  timestamptz,
  created_at   timestamptz not null default now()
);
create index input_reports_status_idx on input_reports(status, created_at desc);
alter table input_reports enable row level security;
create policy inrep_owner_read   on input_reports for select to authenticated using (user_id = auth.uid());
create policy inrep_owner_insert on input_reports for insert to authenticated
  with check (user_id = auth.uid() and status = 'new' and admin_note is null and reviewed_by is null and reviewed_at is null);
create policy inrep_admin_all    on input_reports for all to authenticated using (is_admin()) with check (is_admin());

-- At most 5 reports per person per day, enforced here so it cannot be skipped by calling the REST API directly.
create or replace function input_reports_limit() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from input_reports where user_id = new.user_id and created_at > now() - interval '1 day') >= 5 then
    raise exception 'you can send at most 5 reports a day';
  end if;
  return new;
end $$;
create trigger trg_inrep_limit before insert on input_reports for each row execute function input_reports_limit();
