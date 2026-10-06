-- KanoFarm AI — migration 0009: records saved offline (safe to replay) and farm finances.
-- Requires 0001 and 0002. Safe to run once.
--
-- Offline records: the phone gives every record it creates a random client_id BEFORE it tries to send it. If the signal drops
-- after the server saved the record but before the phone heard back, the phone sends it again; the (farm_id, client_id) unique
-- constraint turns that repeat into a no-op instead of a duplicate. Rows without a client_id (older records) are unaffected.
alter table farm_observations add column if not exists client_id uuid;
alter table farm_observations add constraint farm_observations_client_unique unique (farm_id, client_id);

-- ---------------------------------------------------------------- finances
create table farm_finance_entries (
  id            uuid primary key default gen_random_uuid(),
  farm_id       uuid not null references farms(id) on delete cascade,
  farm_crop_id  uuid references farm_crops(id) on delete set null,   -- optional: which crop the money belongs to
  client_id     uuid,
  kind          text not null check (kind in ('income','expense')),
  category      text not null,
  amount_ngn    numeric(14,2) not null check (amount_ngn > 0 and amount_ngn <= 100000000000),
  entry_date    date not null,
  season        text check (season is null or char_length(season) between 1 and 40),   -- free text, e.g. "2026 rainy season"
  note          text check (note is null or char_length(note) <= 300),
  data_kind     data_type not null default 'USER_PROVIDED',
  created_at    timestamptz not null default now(),
  constraint finance_category_matches_kind check (
    (kind = 'income'  and category in ('sale','other_income')) or
    (kind = 'expense' and category in ('seed','fertilizer','pesticide','labour','equipment','irrigation','transport',
                                       'land_rent','processing','other_expense'))),
  unique (farm_id, client_id)
);
create index farm_finance_farm_idx on farm_finance_entries (farm_id, entry_date desc);

alter table farm_finance_entries enable row level security;
create policy finance_owner on farm_finance_entries for all
  using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()))
  with check (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));

-- A crop attached to an entry must belong to the SAME farm (otherwise a user could point at another farm's crop id).
create or replace function finance_check_crop() returns trigger language plpgsql as $$
begin
  if new.farm_crop_id is not null and not exists (select 1 from farm_crops c where c.id = new.farm_crop_id and c.farm_id = new.farm_id) then
    raise exception 'crop does not belong to this farm';
  end if;
  if (select count(*) from farm_finance_entries where farm_id = new.farm_id) >= 10000 then
    raise exception 'too many finance entries for one farm';
  end if;
  return new;
end $$;
create trigger trg_finance_check before insert on farm_finance_entries for each row execute function finance_check_crop();
