-- KanoFarm AI — migration 0006: Farm Market (public listings with photos).
-- Requires 0001 and 0002 (uses profiles and is_admin()). Safe to run once.

create table market_listings (
  id            uuid primary key default gen_random_uuid(),
  owner_id      uuid not null references profiles(id) on delete cascade,
  kind          text not null default 'for_sale' check (kind in ('for_sale','wanted')),
  title         text not null check (char_length(title) between 3 and 100),
  category      text not null check (category in ('produce','livestock','seeds_inputs','equipment','processed','other')),
  product       text check (char_length(product) <= 60),
  description   text check (char_length(description) <= 1000),
  quantity      numeric check (quantity >= 0),
  quantity_unit text check (quantity_unit in ('kg','bag','tonne','crate','basket','bunch','piece','litre','tray','other')),
  price_ngn     numeric(14,2) check (price_ngn >= 0),
  price_unit    text check (price_unit in ('kg','bag','tonne','crate','basket','bunch','piece','litre','tray','other')),
  negotiable    boolean not null default false,
  state         text not null,
  lga           text,
  contact_phone text not null check (contact_phone ~ '^\+234[0-9]{10}$'),   -- published on purpose by the seller
  seller_name   text not null check (char_length(seller_name) between 1 and 80),
  image_paths   text[] not null default '{}' check (cardinality(image_paths) <= 4),
  status        text not null default 'active' check (status in ('active','sold','hidden','removed')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  expires_at    timestamptz not null default (now() + interval '30 days')
);
create index market_listings_feed_idx  on market_listings (status, created_at desc);
create index market_listings_state_idx on market_listings (state);
create index market_listings_owner_idx on market_listings (owner_id);
create trigger trg_market_upd before update on market_listings for each row execute function set_updated_at();

create table market_reports (
  id          uuid primary key default gen_random_uuid(),
  listing_id  uuid not null references market_listings(id) on delete cascade,
  reporter_id uuid not null references auth.users(id) on delete cascade,
  reason      text check (char_length(reason) <= 300),
  created_at  timestamptz not null default now(),
  unique (listing_id, reporter_id)      -- one report per person per listing
);

alter table market_listings enable row level security;
alter table market_reports  enable row level security;

-- Anyone (even signed out) may read live listings. Everything else is owner- or admin-only.
create policy market_public_read on market_listings for select to anon, authenticated
  using (status = 'active' and expires_at > now());
create policy market_owner_read   on market_listings for select to authenticated using (owner_id = auth.uid());
create policy market_admin_read   on market_listings for select to authenticated using (is_admin());
create policy market_owner_insert on market_listings for insert to authenticated
  with check (owner_id = auth.uid() and status = 'active');
-- Owners cannot touch a listing once it has been hidden for review, and cannot un-hide it themselves.
create policy market_owner_update on market_listings for update to authenticated
  using (owner_id = auth.uid() and status <> 'hidden')
  with check (owner_id = auth.uid() and status in ('active','sold','removed'));
create policy market_owner_delete on market_listings for delete to authenticated using (owner_id = auth.uid());
create policy market_admin_update on market_listings for update to authenticated using (is_admin()) with check (is_admin());
create policy market_admin_delete on market_listings for delete to authenticated using (is_admin());

create policy market_reports_admin on market_reports for all to authenticated using (is_admin()) with check (is_admin());

-- Reporting goes through this function only: it records one report per person and hides the listing
-- automatically once 3 different people have reported it, until an admin reviews it.
create or replace function report_listing(p_listing uuid, p_reason text) returns void
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  if not exists (select 1 from market_listings where id = p_listing and status = 'active' and owner_id <> auth.uid()) then
    raise exception 'listing not found';
  end if;
  insert into market_reports (listing_id, reporter_id, reason)
    values (p_listing, auth.uid(), left(coalesce(p_reason, ''), 300))
    on conflict (listing_id, reporter_id) do nothing;
  select count(*) into n from market_reports where listing_id = p_listing;
  if n >= 3 then update market_listings set status = 'hidden' where id = p_listing and status = 'active'; end if;
end $$;
revoke all on function report_listing(uuid, text) from public;
grant execute on function report_listing(uuid, text) to authenticated;

-- Public bucket for listing photos (anyone can view a photo URL; only the owner can add/delete in their own folder).
insert into storage.buckets (id, name, public) values ('market', 'market', true) on conflict do nothing;
create policy market_files_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'market' and (storage.foldername(name))[1] = auth.uid()::text);
create policy market_files_delete_own on storage.objects for delete to authenticated
  using (bucket_id = 'market' and (storage.foldername(name))[1] = auth.uid()::text);
create policy market_files_delete_admin on storage.objects for delete to authenticated
  using (bucket_id = 'market' and is_admin());
