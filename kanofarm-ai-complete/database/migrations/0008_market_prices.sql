-- KanoFarm AI — migration 0008: asking-price statistics from Farm Market listings.
-- Requires 0006. Safe to run once.
--
-- What these numbers are: ASKING prices that sellers typed into listings. They are NOT verified market prices and NOT
-- prices anyone actually paid. The functions return aggregates only (never a seller, phone number or single listing),
-- and refuse to answer when the sample is too small to be meaningful or could be one seller's doing:
--   * summary  : at least 5 listings from at least 3 different sellers
--   * weekly   : at least 3 listings from at least 2 different sellers in that week
-- Listings that were reported/hidden or removed are left out, and so are "wanted" ads.

-- Prices need a product name and a unit to be comparable.
-- A NOT VALID constraint skips old rows when it is added but is still checked whenever a row is updated, so old goods
-- listings without a product name would no longer be markable as sold. Give them their title as a product name first
-- (a one-off title never reaches the 5-listing minimum, so it cannot distort a price summary).
update market_listings set product = left(title, 60)
  where product is null and kind = 'for_sale' and category in ('produce','livestock');
alter table market_listings add constraint market_price_needs_unit
  check (price_ngn is null or price_unit is not null) not valid;
alter table market_listings add constraint market_priced_goods_need_product
  check (category not in ('produce','livestock') or kind = 'wanted' or product is not null) not valid;

create or replace function market_norm_product(p text) returns text
language sql immutable as $$ select nullif(regexp_replace(lower(btrim(coalesce(p, ''))), '\s+', ' ', 'g'), '') $$;

create index if not exists market_listings_price_idx on market_listings (market_norm_product(product), price_unit, created_at)
  where kind = 'for_sale' and price_ngn is not null and status in ('active','sold');

-- One row per product + unit: current picture over the last p_days days.
create or replace function market_price_summary(p_state text default null, p_days int default 60)
returns table (product text, price_unit text, listings bigint, sellers bigint, median_ngn numeric, low_ngn numeric, high_ngn numeric,
               last_listing date)
language sql stable security definer set search_path = public as $$
  select market_norm_product(l.product), l.price_unit, count(*), count(distinct l.owner_id),
         round((percentile_cont(0.5)  within group (order by l.price_ngn))::numeric, 0),
         round((percentile_cont(0.25) within group (order by l.price_ngn))::numeric, 0),
         round((percentile_cont(0.75) within group (order by l.price_ngn))::numeric, 0),
         max(l.created_at)::date
  from market_listings l
  where l.kind = 'for_sale' and l.price_ngn is not null and l.price_ngn > 0 and l.price_unit is not null
    and market_norm_product(l.product) is not null
    and l.status in ('active','sold')
    and l.created_at >= now() - make_interval(days => greatest(7, least(coalesce(p_days, 60), 180)))
    and (p_state is null or l.state = p_state)
  group by 1, 2
  having count(*) >= 5 and count(distinct l.owner_id) >= 3
  order by count(*) desc
  limit 60;
$$;

-- Weekly medians for one product + unit.
create or replace function market_price_weekly(p_product text, p_unit text, p_state text default null, p_days int default 90)
returns table (week_start date, listings bigint, sellers bigint, median_ngn numeric, low_ngn numeric, high_ngn numeric)
language sql stable security definer set search_path = public as $$
  select date_trunc('week', l.created_at)::date, count(*), count(distinct l.owner_id),
         round((percentile_cont(0.5)  within group (order by l.price_ngn))::numeric, 0),
         round((percentile_cont(0.25) within group (order by l.price_ngn))::numeric, 0),
         round((percentile_cont(0.75) within group (order by l.price_ngn))::numeric, 0)
  from market_listings l
  where l.kind = 'for_sale' and l.price_ngn is not null and l.price_ngn > 0
    and market_norm_product(l.product) = market_norm_product(p_product) and l.price_unit = p_unit
    and l.status in ('active','sold')
    and l.created_at >= now() - make_interval(days => greatest(14, least(coalesce(p_days, 90), 180)))
    and (p_state is null or l.state = p_state)
  group by 1
  having count(*) >= 3 and count(distinct l.owner_id) >= 2
  order by 1;
$$;

revoke all on function market_norm_product(text), market_price_summary(text, int), market_price_weekly(text, text, text, int) from public;
grant execute on function market_norm_product(text) to anon, authenticated;
grant execute on function market_price_summary(text, int), market_price_weekly(text, text, text, int) to anon, authenticated;
