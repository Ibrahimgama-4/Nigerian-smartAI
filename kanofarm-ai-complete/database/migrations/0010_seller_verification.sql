-- KanoFarm AI — migration 0010: "Verified seller" badges, and Farm Market limits enforced IN THE DATABASE.
-- Requires 0001, 0002, 0006 and 0007 (cron secret). Safe to run once.
--
-- WHAT THE BADGE MEANS (and does not mean)
--   An administrator contacted the seller on the phone number shown on their listings, the seller confirmed it is theirs, and the
--   administrator saw one piece of supporting evidence (cooperative or association membership, an extension officer's confirmation,
--   or a farm visit). It lasts at most 12 months and can be taken away at any time.
--   It does NOT guarantee the goods, the price or that a deal will be safe. No ID document is uploaded or stored by the app.
--
-- WHY THE LIMITS LIVE HERE
--   Until now the caps (20 live listings, 5 posts per hour, 4 photos, own-folder photos, 30-day life) were checked only in the
--   Python API. Any signed-in user can talk to Supabase directly with their own login and skip the API, so the database enforces them.

-- ---------------------------------------------------------------- verification requests
create table seller_verifications (
  user_id        uuid primary key references profiles(id) on delete cascade,
  seller_name    text not null check (char_length(seller_name) between 1 and 80),   -- copied from the profile so admins need no access to profiles
  status         text not null default 'pending' check (status in ('pending','verified','rejected','revoked','expired')),
  phone          text not null check (phone ~ '^\+234[0-9]{10}$'),
  proof_kind     text not null check (proof_kind in ('cooperative','extension_officer','farm_visit','market_association','other')),
  proof_detail   text not null check (char_length(proof_detail) between 3 and 300),  -- e.g. "Member, Kura Rice Farmers Cooperative". Never an ID number.
  requested_at   timestamptz not null default now(),
  reviewed_by    uuid references auth.users(id),
  reviewed_at    timestamptz,
  review_note    text check (review_note is null or char_length(review_note) <= 300),
  verified_until date,
  -- a verified row must record who verified it, when, and until when
  constraint verified_has_review check (status <> 'verified' or (reviewed_by is not null and reviewed_at is not null and verified_until is not null))
);
alter table seller_verifications enable row level security;

create policy sellerver_owner_read   on seller_verifications for select to authenticated using (user_id = auth.uid());
create policy sellerver_owner_insert on seller_verifications for insert to authenticated
  with check (user_id = auth.uid() and status = 'pending' and reviewed_by is null and reviewed_at is null and verified_until is null);
-- An owner may only re-apply (back to 'pending') after a rejection or expiry, never edit a verified or revoked row.
create policy sellerver_owner_update on seller_verifications for update to authenticated
  using (user_id = auth.uid() and status in ('pending','rejected','expired'))
  with check (user_id = auth.uid() and status = 'pending' and reviewed_by is null and reviewed_at is null and verified_until is null);
create policy sellerver_admin_all on seller_verifications for all to authenticated using (is_admin()) with check (is_admin());

-- After a rejection the seller waits 14 days before applying again (admins are exempt).
create or replace function sellerver_cooldown() returns trigger language plpgsql security definer set search_path = public as $$
begin
  if not is_admin() and old.status = 'rejected' and old.reviewed_at is not null and old.reviewed_at > now() - interval '14 days'
     and new.status = 'pending' then
    raise exception 'please wait 14 days after a rejection before applying again';
  end if;
  return new;
end $$;
create trigger trg_sellerver_cooldown before update on seller_verifications for each row execute function sellerver_cooldown();

-- ---------------------------------------------------------------- the badge on listings
alter table market_listings add column if not exists seller_verified boolean not null default false;

create or replace function seller_is_verified(p_user uuid) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from seller_verifications where user_id = p_user and status = 'verified' and verified_until >= current_date);
$$;

-- Verification changes flow to the seller's listings. Runs with the table owner's rights; the guard below lets nested updates through.
create or replace function sellerver_sync_badge() returns trigger language plpgsql security definer set search_path = public as $$
begin
  update market_listings set seller_verified = (new.status = 'verified' and new.verified_until >= current_date)
    where owner_id = new.user_id and seller_verified is distinct from (new.status = 'verified' and new.verified_until >= current_date);
  return null;
end $$;
create trigger trg_sellerver_sync after insert or update of status, verified_until on seller_verifications
  for each row execute function sellerver_sync_badge();

-- ---------------------------------------------------------------- listing guards (cannot be skipped by calling the REST API directly)
create or replace function market_listing_guard() returns trigger language plpgsql security definer set search_path = public as $$
declare p text;
  -- Who is acting? 'anon' / 'authenticated' are the roles every API caller gets, so those are the people the guard restrains.
  -- An administrator (is_admin), a database operator in the SQL editor (role is not anon/authenticated) and our own badge-sync
  -- trigger (nested trigger depth) are trusted.
  elevated boolean := is_admin() or coalesce(current_setting('role', true), 'none') not in ('anon', 'authenticated');
  nested   boolean := pg_trigger_depth() > 1;
begin
  -- Photos must live in the seller's own folder (<owner_id>/...), so nobody can point a listing at someone else's pictures.
  foreach p in array coalesce(new.image_paths, '{}') loop
    if split_part(p, '/', 1) <> new.owner_id::text then raise exception 'photo path is not in your own folder'; end if;
  end loop;

  if tg_op = 'INSERT' then
    new.seller_verified := seller_is_verified(new.owner_id);             -- never taken from the client
    new.expires_at := least(new.expires_at, now() + interval '30 days');
    if (select count(*) from market_listings where owner_id = new.owner_id and status = 'active') >= 20 then
      raise exception 'you already have 20 live listings';
    end if;
    if (select count(*) from market_listings where owner_id = new.owner_id and created_at > now() - interval '1 hour') >= 5 then
      raise exception 'you are posting too fast; please wait a while';
    end if;
  else
    if not (elevated or nested) then
      new.seller_verified := old.seller_verified;                          -- only an admin's review can change the badge
      new.owner_id := old.owner_id;
      new.created_at := old.created_at;
    end if;
    if not elevated then
      new.expires_at := least(new.expires_at, now() + interval '30 days'); -- renewing gives at most 30 more days
    end if;
    if new.status = 'active' and old.status <> 'active' and not elevated
       and (select count(*) from market_listings where owner_id = new.owner_id and status = 'active' and id <> new.id) >= 20 then
      raise exception 'you already have 20 live listings';
    end if;
  end if;
  return new;
end $$;
create trigger trg_market_guard before insert or update on market_listings for each row execute function market_listing_guard();

-- Photo storage: only JPEG/PNG/WebP and a size ceiling per file, and a ceiling on how many files one person can hold.
update storage.buckets set file_size_limit = 700000,  allowed_mime_types = array['image/jpeg','image/png','image/webp'] where id = 'market';
update storage.buckets set file_size_limit = 2100000, allowed_mime_types = array['image/jpeg','image/png','image/webp'] where id = 'scans';

drop policy if exists market_files_insert on storage.objects;
create policy market_files_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'market' and (storage.foldername(name))[1] = auth.uid()::text
              and (select count(*) from storage.objects o where o.bucket_id = 'market' and (storage.foldername(o.name))[1] = auth.uid()::text) < 100);

-- ---------------------------------------------------------------- reporting: harder to abuse
-- Two changes: the reporting account must be at least a day old (stops three throwaway accounts hiding a rival's listing), and a
-- verified seller's listing needs 5 reports instead of 3 before it is hidden pending review.
create or replace function report_listing(p_listing uuid, p_reason text) returns void
language plpgsql security definer set search_path = public as $$
declare n int; need_n int; made timestamptz;
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  select created_at into made from auth.users where id = auth.uid();
  if made is null or made > now() - interval '1 day' then raise exception 'new accounts cannot report listings yet'; end if;
  if not exists (select 1 from market_listings where id = p_listing and status = 'active' and owner_id <> auth.uid()) then
    raise exception 'listing not found';
  end if;
  insert into market_reports (listing_id, reporter_id, reason)
    values (p_listing, auth.uid(), left(coalesce(p_reason, ''), 300))
    on conflict (listing_id, reporter_id) do nothing;
  select count(*) into n from market_reports where listing_id = p_listing;
  select case when seller_verified then 5 else 3 end into need_n from market_listings where id = p_listing;
  if n >= need_n then update market_listings set status = 'hidden' where id = p_listing and status = 'active'; end if;
end $$;
revoke all on function report_listing(uuid, text) from public;
grant execute on function report_listing(uuid, text) to authenticated;

-- ---------------------------------------------------------------- expiry (called by the daily cron job, same secret as alerts)
create or replace function cron_expire_verifications(p_secret text) returns int
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  update seller_verifications set status = 'expired' where status = 'verified' and verified_until < current_date;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function cron_expire_verifications(text) from public;
grant execute on function cron_expire_verifications(text) to anon, authenticated;
