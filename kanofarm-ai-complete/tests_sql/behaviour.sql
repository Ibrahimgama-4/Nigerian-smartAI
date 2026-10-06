-- Behaviour tests that run against the REAL migrations (see run.sh). Each block either prints "ok: ..." or raises and stops the run.
-- Users act through the same role Supabase gives signed-in farmers ("authenticated") with their id in request.jwt.claim.sub,
-- exactly the path a person takes when they call Supabase directly instead of our API.

-- ---------------------------------------------------------------- helpers (dropped at the end)
create function t_user(n int, age interval default interval '3 days') returns uuid language plpgsql as $$
declare id uuid := ('aaaaaaaa-0000-4000-8000-' || lpad(n::text, 12, '0'))::uuid;
begin
  insert into auth.users (id, email, created_at) values (id, 'u' || n || '@example.com', now() - age) on conflict do nothing;
  insert into profiles (id, full_name, state) values (id, 'User ' || n, 'Kano') on conflict do nothing;
  return id;
end $$;

create function t_as(uid uuid) returns void language plpgsql as $$
begin perform set_config('request.jwt.claim.sub', uid::text, true); execute 'set local role authenticated'; end $$;

create function t_ok(name text) returns void language plpgsql as $$ begin raise notice 'ok: %', name; end $$;
create function t_assert(cond boolean, msg text) returns void language plpgsql as $$ begin
  if cond is not true then raise exception 'FAILED: %', msg; end if; end $$;

-- run sql, require that it FAILS with a message matching pat
create function t_fails(sql text, pat text, label text) returns void language plpgsql as $$
begin
  begin execute sql;
  exception when others then
    if sqlerrm ~* pat then perform t_ok(label); return; end if;
    raise exception 'FAILED: % raised the wrong error: %', label, sqlerrm;
  end;
  raise exception 'FAILED: % was allowed but must be refused', label;
end $$;

create function t_listing(owner uuid, title text default 'Fresh maize', paths text[] default '{}', extra jsonb default '{}') returns uuid
language plpgsql as $$
declare lid uuid := gen_random_uuid();
begin
  insert into market_listings (id, owner_id, title, category, product, state, contact_phone, seller_name, price_ngn, price_unit, image_paths, seller_verified)
  values (lid, owner, title, 'produce', coalesce(extra->>'product', 'maize'), coalesce(extra->>'state', 'Kano'), '+2348031234567', 'Seller',
          coalesce((extra->>'price')::numeric, 1000), 'bag', paths, coalesce((extra->>'forge')::boolean, false));
  return lid;
end $$;

insert into user_roles (user_id, role) select t_user(1), 'admin';       -- user 1 is the administrator

-- ---------------------------------------------------------------- 1. listing limits cannot be skipped
do $$ declare u uuid := t_user(10); lid uuid; v boolean; exp timestamptz; begin
  perform t_as(u);
  lid := t_listing(u, 'forged badge', '{}', '{"forge": true}');
  select seller_verified into v from market_listings where id = lid;
  perform t_assert(v = false, 'a seller cannot create a listing that already carries the badge');
  perform t_ok('badge value sent by the client is ignored on insert');

  update market_listings set seller_verified = true, expires_at = now() + interval '2 years' where id = lid;
  select seller_verified, expires_at into v, exp from market_listings where id = lid;
  perform t_assert(v = false, 'a seller cannot switch the badge on by updating their own listing');
  perform t_assert(exp <= now() + interval '30 days' + interval '1 minute', 'renewing cannot extend a listing beyond 30 days');
  perform t_ok('owner cannot set the badge or a far-future expiry by direct update');

  perform t_fails(format($f$select t_listing(%L, 'steals photo', array[%L])$f$, u, 'aaaaaaaa-0000-4000-8000-000000000999/x/1.jpg'),
                  'own folder', 'photo path pointing at someone else''s folder is refused');
  perform t_listing(u, 'ok photo', array[u::text || '/x/1.jpg']);
  perform t_ok('photo inside the seller''s own folder is accepted');

  -- 2 listings so far for u in the last hour (+1 above) -> 3; two more are fine, the sixth is not
  perform t_listing(u, 'third'); perform t_listing(u, 'fourth'); perform t_listing(u, 'fifth');
  perform t_fails(format($f$select t_listing(%L, 'sixth')$f$, u), 'too fast', 'sixth listing within an hour is refused by the database');
end $$;

do $$ declare u uuid := t_user(11); i int; begin
  reset role;
  for i in 1..20 loop   -- 20 live listings made long ago (so the hourly limit does not apply)
    insert into market_listings (owner_id, title, category, product, state, contact_phone, seller_name, price_ngn, price_unit, created_at)
    values (u, 'old ' || i, 'produce', 'maize', 'Kano', '+2348031234567', 'S', 1000, 'bag', now() - interval '3 days');
  end loop;
  perform t_as(u);
  perform t_fails(format($f$select t_listing(%L, 'twenty-first')$f$, u), '20 live', 'the 21st live listing is refused');
  -- sold listings can be re-opened only while under the cap
  reset role;
  update market_listings set status = 'sold' where owner_id = u and title = 'old 1';       -- now 19 live
  perform t_as(u);
  update market_listings set status = 'active' where owner_id = u and title = 'old 1';     -- back to exactly 20: allowed
  update market_listings set status = 'sold'   where owner_id = u and title = 'old 1';
  reset role;
  insert into market_listings (owner_id, title, category, product, state, contact_phone, seller_name, price_ngn, price_unit, created_at)
    values (u, 'old 21', 'produce', 'maize', 'Kano', '+2348031234567', 'S', 1000, 'bag', now() - interval '3 days');   -- 20 live again
  perform t_as(u);
  perform t_fails(format($f$update market_listings set status = 'active' where owner_id = %L and title = 'old 1'$f$, u), '20 live', 're-opening a sold listing cannot push a seller past 20 live');
  reset role;
end $$;

-- ---------------------------------------------------------------- 2. verified seller badge
do $$ declare a uuid := t_user(20); b uuid := t_user(21); adm uuid := ('aaaaaaaa-0000-4000-8000-000000000001')::uuid;
               la uuid; lb uuid; v boolean; st text; begin
  perform t_as(a); la := t_listing(a, 'a before'); reset role;
  perform t_as(b); lb := t_listing(b, 'b before'); reset role;

  perform t_as(a);
  insert into seller_verifications (user_id, seller_name, phone, proof_kind, proof_detail)
    values (a, 'User 20', '+2348031234567', 'cooperative', 'Member of Kura Rice Farmers Cooperative');
  perform t_ok('seller can ask for the badge');
  perform t_fails(format($f$update seller_verifications set status = 'verified', reviewed_by = %L, reviewed_at = now(), verified_until = current_date + 30 where user_id = %L$f$, a, a),
                  'row-level security|violates', 'seller cannot approve their own request');
  perform t_fails(format($f$insert into seller_verifications (user_id, seller_name, phone, proof_kind, proof_detail, status, reviewed_by, reviewed_at, verified_until)
                            values (%L, 'x', '+2348031234567', 'other', 'abc', 'verified', %L, now(), current_date + 30)$f$, b, b),
                  'row-level security|violates|duplicate', 'a seller cannot insert an already-verified row');
  reset role;

  perform t_as(b);
  perform t_assert((select count(*) from seller_verifications) = 0, 'sellers cannot read other people''s verification requests');
  perform t_ok('verification requests are private to their owner');
  reset role;

  -- the admin approves a; the badge reaches a's existing AND new listings, never b's
  perform t_as(adm);
  update seller_verifications set status = 'verified', reviewed_by = adm, reviewed_at = now(), verified_until = current_date + 365 where user_id = a;
  reset role;
  select seller_verified into v from market_listings where id = la; perform t_assert(v, 'badge reaches the seller''s existing listing');
  select seller_verified into v from market_listings where id = lb; perform t_assert(not v, 'badge does not reach other sellers');
  perform t_as(a); la := t_listing(a, 'a after');
  select seller_verified into v from market_listings where id = la; perform t_assert(v, 'a new listing from a verified seller carries the badge');
  reset role;
  perform t_ok('admin approval puts the badge on the right listings');

  -- an approval without reviewer details violates the table's own rule
  perform t_fails(format($f$update seller_verifications set status = 'verified', reviewed_by = null where user_id = %L$f$, a),
                  'verified_has_review', 'a verified row must record who approved it');
  update seller_verifications set status = 'verified', reviewed_by = adm where user_id = a and false;  -- no-op, keeps the planner honest

  -- the admin removes it
  perform t_as(adm); update seller_verifications set status = 'revoked', review_note = 'complaint upheld' where user_id = a; reset role;
  select seller_verified into v from market_listings where id = la; perform t_assert(not v, 'revoking removes the badge from listings');
  perform t_ok('revoking removes the badge');

  -- an owner cannot re-apply after a revoke (policy only lets pending/rejected/expired rows be edited)
  perform t_as(a);
  update seller_verifications set status = 'pending', reviewed_by = null, reviewed_at = null, verified_until = null where user_id = a;
  select status into st from seller_verifications where user_id = a;
  perform t_assert(st = 'revoked', 'a revoked seller cannot reopen the request themselves');
  reset role;
  perform t_ok('revoked sellers must go through an administrator');
end $$;

do $$ declare a uuid := t_user(22); adm uuid := ('aaaaaaaa-0000-4000-8000-000000000001')::uuid; lid uuid; st text; v boolean; n int; begin
  -- expiry through the cron function, with the secret check
  perform t_as(a); lid := t_listing(a, 'will expire'); reset role;
  insert into seller_verifications (user_id, seller_name, phone, proof_kind, proof_detail, status, reviewed_by, reviewed_at, verified_until)
    values (a, 'User 22', '+2348031234567', 'farm_visit', 'visited by officer', 'verified', adm, now(), current_date + 5);
  select seller_verified into v from market_listings where id = lid; perform t_assert(v, 'verified before expiry');
  update seller_verifications set verified_until = current_date - 1 where user_id = a;     -- time passes
  perform t_fails($f$select cron_expire_verifications('wrong')$f$, 'not allowed', 'expiry job refuses a wrong secret');
  insert into cron_secrets (name, secret_hash) values ('alerts', encode(sha256(convert_to('s3cret', 'UTF8')), 'hex'));
  n := cron_expire_verifications('s3cret');
  select status into st from seller_verifications where user_id = a;
  select seller_verified into v from market_listings where id = lid;
  perform t_assert(n >= 1 and st = 'expired' and not v, 'lapsed badges expire and disappear from listings');
  perform t_ok('badges expire after their date');

  -- after a rejection the seller must wait 14 days
  update seller_verifications set status = 'rejected', reviewed_by = adm, reviewed_at = now(), review_note = 'could not reach you', verified_until = null where user_id = a;
  perform t_as(a);
  perform t_fails(format($f$update seller_verifications set status = 'pending', reviewed_by = null, reviewed_at = null, verified_until = null where user_id = %L$f$, a),
                  '14 days', 're-applying right after a rejection is refused');
  reset role;
  update seller_verifications set reviewed_at = now() - interval '15 days' where user_id = a;
  perform t_as(a);
  update seller_verifications set status = 'pending', reviewed_by = null, reviewed_at = null, verified_until = null where user_id = a;
  select status into st from seller_verifications where user_id = a; perform t_assert(st = 'pending', 'seller can re-apply after the wait');
  reset role; perform t_ok('rejected sellers can re-apply after 14 days');
end $$;

-- ---------------------------------------------------------------- 3. reports are harder to abuse
do $$ declare owner uuid := t_user(30); lid uuid; vid uuid; st text; i int; r uuid; adm uuid := ('aaaaaaaa-0000-4000-8000-000000000001')::uuid; begin
  perform t_as(owner); lid := t_listing(owner, 'plain listing'); vid := t_listing(owner, 'verified listing'); reset role;
  update market_listings set seller_verified = true where id = vid;      -- as if the owner were verified

  perform t_as(t_user(31, interval '2 hours'));
  perform t_fails(format($f$select report_listing(%L, 'spam')$f$, lid), 'new accounts', 'an account less than a day old cannot report');
  reset role;

  for i in 40..42 loop r := t_user(i); perform t_as(r); perform report_listing(lid, 'bad'); reset role; end loop;
  select status into st from market_listings where id = lid; perform t_assert(st = 'hidden', 'three reports hide an ordinary listing');
  for i in 40..42 loop r := t_user(i); perform t_as(r); perform report_listing(vid, 'bad'); reset role; end loop;
  select status into st from market_listings where id = vid; perform t_assert(st = 'active', 'three reports do not hide a verified seller''s listing');
  for i in 43..44 loop r := t_user(i); perform t_as(r); perform report_listing(vid, 'bad'); reset role; end loop;
  select status into st from market_listings where id = vid; perform t_assert(st = 'hidden', 'five reports hide a verified seller''s listing');
  perform t_ok('report thresholds: 3 normally, 5 for verified, accounts must be a day old');
end $$;

-- ---------------------------------------------------------------- 4. alert channels and the cron functions
do $$ declare u uuid := t_user(50); f uuid; n int; ok boolean; begin
  perform t_as(u);
  perform t_fails(format($f$insert into alert_prefs (user_id, whatsapp_enabled) values (%L, true)$f$, u), 'phone_needs_consent|violates', 'WhatsApp cannot be on without a phone and consent');
  insert into alert_prefs (user_id, push_enabled, phone, phone_consent, whatsapp_enabled) values (u, true, '+2348031234567', true, true);
  insert into farms (owner_id, name, state, latitude, longitude) values (u, 'Farm 50', 'Kano', 12.0, 8.5) returning id into f;
  perform t_ok('alert preferences accept a phone only with consent');
  perform t_assert((select count(*) from alert_prefs) = 1, 'owner sees own prefs');
  reset role;

  perform t_as(t_user(51)); perform t_assert((select count(*) from alert_prefs) = 0, 'other users cannot see alert prefs');
  perform t_assert((select count(*) from cron_secrets) = 0, 'cron secrets are invisible to the API');
  perform t_fails($f$select * from cron_alert_targets('wrong')$f$, 'not allowed', 'cron targets refuse a wrong secret');
  perform t_fails($f$select * from cron_alert_targets('')$f$, 'not allowed', 'cron targets refuse an empty secret');
  perform t_assert((select count(*) from cron_alert_targets('s3cret') where farm_id = f and phone = '+2348031234567') = 1, 'cron targets return the farm with its phone');
  reset role;

  -- as the anon role (what the Vercel job uses): works with the right secret only
  execute 'set local role anon';
  perform t_assert(cron_alert_claim('s3cret', f, 'heat_stress_risk', current_date, 'push'), 'first claim wins');
  perform t_assert(not cron_alert_claim('s3cret', f, 'heat_stress_risk', current_date, 'push'), 'second claim for the same alert is a duplicate');
  perform t_assert(cron_alert_claim('s3cret', f, 'heat_stress_risk', current_date, 'whatsapp'), 'a different channel is claimed separately');
  perform cron_alert_release('s3cret', f, 'heat_stress_risk', current_date, 'push');
  perform t_assert(cron_alert_claim('s3cret', f, 'heat_stress_risk', current_date, 'push'), 'a released claim can be taken again (retry after failure)');
  perform t_fails($f$select cron_alert_claim('wrong', gen_random_uuid(), 'k', current_date, 'push')$f$, 'not allowed', 'claiming with a wrong secret is refused');
  reset role;
  perform t_assert((select count(*) from cron_secrets) = 1, 'secret row still there');
  perform t_ok('cron functions: secret required, one send per alert per day per channel, release allows retry');
end $$;

-- ---------------------------------------------------------------- 5. offline records and finances
do $$ declare a uuid := t_user(60); b uuid := t_user(61); fa uuid; fb uuid; cid uuid := gen_random_uuid(); cropb uuid; begin
  perform t_as(a); insert into farms (owner_id, name, state, latitude, longitude) values (a, 'A', 'Kano', 12, 8.5) returning id into fa; reset role;
  perform t_as(b); insert into farms (owner_id, name, state, latitude, longitude) values (b, 'B', 'Kano', 12, 8.5) returning id into fb;
  insert into farm_crops (farm_id, crop_id, planting_date) select fb, id, current_date - 10 from crops where slug = 'maize' returning id into cropb;
  reset role;

  perform t_as(a);
  insert into farm_observations (farm_id, kind, observed_on, text, client_id) values (fa, 'note', current_date, 'x', cid);
  perform t_fails(format($f$insert into farm_observations (farm_id, kind, observed_on, text, client_id) values (%L, 'note', current_date, 'x', %L)$f$, fa, cid),
                  'duplicate|unique', 'a replayed offline observation is not stored twice');
  insert into farm_finance_entries (farm_id, kind, category, amount_ngn, entry_date, client_id) values (fa, 'expense', 'seed', 5000, current_date, cid);
  perform t_fails(format($f$insert into farm_finance_entries (farm_id, kind, category, amount_ngn, entry_date, client_id) values (%L, 'expense', 'seed', 5000, current_date, %L)$f$, fa, cid),
                  'duplicate|unique', 'a replayed finance entry is not stored twice');
  perform t_fails(format($f$insert into farm_finance_entries (farm_id, kind, category, amount_ngn, entry_date) values (%L, 'expense', 'sale', 1, current_date)$f$, fa),
                  'category_matches_kind|violates', 'an expense cannot use an income category');
  perform t_fails(format($f$insert into farm_finance_entries (farm_id, kind, category, amount_ngn, entry_date) values (%L, 'income', 'sale', 0, current_date)$f$, fa),
                  'amount_ngn|violates', 'amount must be above zero');
  perform t_fails(format($f$insert into farm_finance_entries (farm_id, farm_crop_id, kind, category, amount_ngn, entry_date) values (%L, %L, 'income', 'sale', 10, current_date)$f$, fa, cropb),
                  'does not belong', 'a finance entry cannot point at another farm''s crop');
  perform t_fails(format($f$insert into farm_finance_entries (farm_id, kind, category, amount_ngn, entry_date) values (%L, 'income', 'sale', 10, current_date)$f$, fb),
                  'row-level security|violates', 'nobody can write finance entries onto someone else''s farm');
  reset role;

  perform t_as(b);
  perform t_assert((select count(*) from farm_finance_entries) = 0, 'finance entries are private to the farm owner');
  perform t_assert((select count(*) from farm_observations) = 0, 'observations are private to the farm owner');
  reset role; perform t_ok('offline replay is idempotent and finances are private');
end $$;

-- ---------------------------------------------------------------- 6. price statistics: small or single-seller samples are never shown
do $$ declare i int; s uuid; n int; r record; begin
  for i in 70..73 loop    -- four sellers, one listing each, product "sorghum" -> 4 listings: below the 5-listing minimum
    s := t_user(i); perform t_as(s); perform t_listing(s, 'sorghum ' || i, '{}', '{"product": "Sorghum", "price": 20000}'); reset role;
  end loop;
  execute 'set local role anon';
  select count(*) into n from market_price_summary(null, 60) where product = 'sorghum';
  perform t_assert(n = 0, '4 listings are too few to publish a price');
  reset role;
  s := t_user(74); perform t_as(s); perform t_listing(s, 'sorghum 74', '{}', '{"product": " SORGHUM ", "price": 24000}'); reset role;
  execute 'set local role anon';
  select * into r from market_price_summary(null, 60) where product = 'sorghum';
  perform t_assert(r.listings = 5 and r.sellers = 5 and r.median_ngn = 20000, '5 listings from 5 sellers are published (names are normalised)');
  reset role; perform t_ok('price summary appears only at 5 listings and 3 sellers');
end $$;

do $$ declare s uuid := t_user(80); i int; n int; begin
  -- one seller posting 5 times (inserted directly, past hourly limit) must not produce a "market price"
  for i in 1..5 loop
    insert into market_listings (owner_id, title, category, product, state, contact_phone, seller_name, price_ngn, price_unit)
    values (s, 'cassava ' || i, 'produce', 'cassava', 'Kano', '+2348031234567', 'S', 1000 * i, 'bag');
  end loop;
  execute 'set local role anon';
  select count(*) into n from market_price_summary(null, 60) where product = 'cassava';
  perform t_assert(n = 0, 'one seller alone cannot set a published price');
  reset role; perform t_ok('a single seller cannot create a published price');
end $$;

-- ---------------------------------------------------------------- 7. photo storage rules
do $$ begin
  perform t_assert((select file_size_limit from storage.buckets where id = 'market') = 700000, 'market photos have a size ceiling');
  perform t_assert((select 'image/jpeg' = any(allowed_mime_types) and not ('text/html' = any(allowed_mime_types)) from storage.buckets where id = 'market'),
                   'market bucket accepts images only');
  perform t_ok('storage buckets restrict size and type');
end $$;

drop function t_user(int, interval), t_as(uuid), t_ok(text), t_assert(boolean, text), t_fails(text, text, text), t_listing(uuid, text, text[], jsonb);
