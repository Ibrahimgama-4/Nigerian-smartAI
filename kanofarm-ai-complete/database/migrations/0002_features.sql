-- KanoFarm AI — migration 0002: roles, records, plant scans, expert review, pesticide register, feedback.
-- Depends on 0001. No fabricated reference data: pesticides/crop_calendar are filled only from cited sources.

create table user_roles (
  user_id uuid not null references auth.users(id) on delete cascade,
  role    text not null check (role in ('admin','expert')),
  primary key (user_id, role)
);
alter table user_roles enable row level security;
create policy roles_self_read on user_roles for select using (user_id = auth.uid());

create or replace function is_staff() returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from user_roles where user_id = auth.uid());
$$;
create or replace function is_admin() returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from user_roles where user_id = auth.uid() and role = 'admin');
$$;

-- Timeline of everything a farmer records (planting, pests, treatments, fertilizer, irrigation, harvest, ...)
create table farm_observations (
  id          uuid primary key default gen_random_uuid(),
  farm_id     uuid not null references farms(id) on delete cascade,
  farm_crop_id uuid references farm_crops(id) on delete set null,
  kind        text not null check (kind in
              ('planting','germination','observation','pest','disease','treatment','fertilizer',
               'irrigation','harvest','weather_event','note')),
  observed_on date not null,
  text        text check (char_length(text) <= 2000),
  data        jsonb not null default '{}'::jsonb,
  data_kind   data_type not null default 'USER_PROVIDED',
  created_at  timestamptz not null default now()
);
create index farm_obs_farm_idx on farm_observations(farm_id, observed_on desc);

create table soil_records (
  id uuid primary key default gen_random_uuid(),
  farm_id uuid not null references farms(id) on delete cascade,
  recorded_on date not null default current_date,
  soil_type text, ph numeric(3,1) check (ph between 3 and 10),
  organic_matter_pct numeric(4,1) check (organic_matter_pct between 0 and 100),
  nitrogen numeric, phosphorus numeric, potassium numeric, npk_units_method text,
  previous_crop text, fertilizer_applied text,
  data_kind data_type not null default 'USER_PROVIDED',
  created_at timestamptz not null default now()
);

create table model_versions (
  id uuid primary key default gen_random_uuid(),
  version text not null unique,
  architecture text not null,
  dataset_names text[] not null,
  dataset_version text not null,
  trained_at timestamptz not null,
  classes text[] not null,
  metrics jsonb not null,                     -- accuracy, per-class precision/recall/F1, confusion matrix
  validated_on_nigerian_field_data boolean not null default false,
  notes text,
  created_at timestamptz not null default now()
);
alter table model_versions enable row level security;
create policy model_versions_read on model_versions for select using (auth.role() = 'authenticated');
create policy model_versions_admin on model_versions for all using (is_admin()) with check (is_admin());

create table plant_scans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  farm_id uuid references farms(id) on delete set null,
  crop_hint text,
  quality jsonb not null,
  image_path text,                             -- only set if the farmer opted in to contribute the image
  contributed_for_training boolean not null default false,
  created_at timestamptz not null default now()
);
create index plant_scans_user_idx on plant_scans(user_id, created_at desc);

create table diagnoses (
  id uuid primary key default gen_random_uuid(),
  scan_id uuid not null references plant_scans(id) on delete cascade,
  status text not null check (status in ('ok','low_confidence','model_unavailable','quality_failed','crop_mismatch')),
  model_version text,
  top_label text, confidence numeric(4,3), level text,
  probs jsonb,
  severity text not null default 'not_assessed',
  created_at timestamptz not null default now()
);
create index diagnoses_scan_idx on diagnoses(scan_id);

create table expert_reviews (
  id uuid primary key default gen_random_uuid(),
  diagnosis_id uuid not null references diagnoses(id) on delete cascade,
  reviewer_id uuid not null references auth.users(id),
  verdict text not null check (verdict in ('correct','incorrect','alternative','needs_more_info')),
  alternative_label text, notes text,
  created_at timestamptz not null default now()
);

-- Pesticide register: EVERY row needs a source, verification date and expiry. Empty until verified data is entered.
create table pesticides (
  id uuid primary key default gen_random_uuid(),
  product_name text not null, active_ingredient text not null, manufacturer text,
  registration_number text not null, registration_status text not null
     check (registration_status in ('registered','suspended','withdrawn','unknown')),
  formulation text,
  target_crop text not null, target_pest_or_disease text not null,
  application_info text, safety_info text, pre_harvest_interval_days smallint,
  source_name text not null, source_url text,
  last_verified date not null,
  expires_at date not null check (expires_at > last_verified),
  entered_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);
create index pesticides_target_idx on pesticides(target_crop, target_pest_or_disease);

create table feedback (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references profiles(id) on delete set null,
  topic text not null default 'general',
  message text not null check (char_length(message) between 1 and 2000),
  created_at timestamptz not null default now()
);

alter table alerts add column alert_date date not null default current_date;
alter table alerts add constraint alerts_dedupe unique (farm_id, message_key, alert_date);

alter table farm_observations enable row level security;
alter table soil_records      enable row level security;
alter table plant_scans       enable row level security;
alter table diagnoses         enable row level security;
alter table expert_reviews    enable row level security;
alter table pesticides        enable row level security;
alter table feedback          enable row level security;

create policy obs_owner on farm_observations for all
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()))
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy soil_owner on soil_records for all
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()))
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy scans_owner on plant_scans for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy scans_staff_read on plant_scans for select using (is_staff());
create policy diag_owner_read on diagnoses for select
  using (exists (select 1 from plant_scans s where s.id = scan_id and s.user_id = auth.uid()));
create policy diag_owner_insert on diagnoses for insert
  with check (exists (select 1 from plant_scans s where s.id = scan_id and s.user_id = auth.uid()));
create policy diag_staff_read on diagnoses for select using (is_staff());
create policy reviews_staff on expert_reviews for all using (is_staff()) with check (is_staff() and reviewer_id = auth.uid());
create policy reviews_owner_read on expert_reviews for select
  using (exists (select 1 from diagnoses d join plant_scans s on s.id = d.scan_id
                 where d.id = diagnosis_id and s.user_id = auth.uid()));
create policy pesticides_read on pesticides for select using (auth.role() = 'authenticated');
create policy pesticides_admin on pesticides for all using (is_admin()) with check (is_admin());
create policy feedback_insert on feedback for insert with check (user_id = auth.uid());
create policy feedback_admin_read on feedback for select using (is_admin());
create policy alerts_owner_insert on alerts for insert
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy alerts_owner_update on alerts for update
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy data_sources_admin on data_sources for all using (is_admin()) with check (is_admin());
create policy crops_admin on crops for all using (is_admin()) with check (is_admin());

-- Private bucket for images farmers explicitly opt in to contribute (path = <user_id>/<scan_id>.jpg)
insert into storage.buckets (id, name, public) values ('scans', 'scans', false) on conflict do nothing;
create policy scans_bucket_owner_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'scans' and (storage.foldername(name))[1] = auth.uid()::text);
create policy scans_bucket_owner_read on storage.objects for select to authenticated
  using (bucket_id = 'scans' and ((storage.foldername(name))[1] = auth.uid()::text or is_staff()));
