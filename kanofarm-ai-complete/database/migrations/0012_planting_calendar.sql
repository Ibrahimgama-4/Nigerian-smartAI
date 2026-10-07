-- KanoFarm AI — migration 0012: Smart Planting Calendar (saved task plan per farm crop, reminders, timeline link).
-- Requires 0001, 0002 (farm_crops, farm_observations, is_admin) and 0007 (cron_secret_ok). Safe to run once.
--
-- The week-by-week plan itself is computed by the app from sourced data (data/crop_activities.json) and the live forecast.
-- What is STORED here is only what the farmer needs to keep: the plan inputs on the farm crop, and one row per task with
-- its window and whether it is done. Ticking a task off writes a note onto the farm timeline in the same transaction.

-- ---------------------------------------------------------------- inputs kept on the farm crop
alter table farm_crops add column if not exists water_source   text not null default 'rainfed' check (water_source in ('rainfed','irrigated'));
alter table farm_crops add column if not exists savanna_zone   text check (savanna_zone is null or savanna_zone in ('sahel','sudan','northern_guinea','southern_guinea'));
alter table farm_crops add column if not exists maturity_group text check (maturity_group is null or maturity_group in ('extra_early','early','medium','late'));
alter table farm_crops add column if not exists maturity_days  smallint check (maturity_days is null or maturity_days between 30 and 400);
alter table farm_crops add constraint farm_crops_id_farm_unique unique (id, farm_id);   -- lets a task prove it belongs to the same farm

-- ---------------------------------------------------------------- tasks
create table farm_plan_tasks (
  id            uuid primary key default gen_random_uuid(),
  farm_id       uuid not null,
  farm_crop_id  uuid not null,
  task_key      text not null check (task_key ~ '^[a-z0-9_]{1,40}$'),
  title         text not null check (char_length(title) between 3 and 200),
  category      text not null check (category in ('land_prep','planting','thinning','weeding','top_dressing','spraying','pest_scouting','harvest_prep','harvest','irrigation')),
  rain_class    text not null check (rain_class in ('field','planting','spray','fertilizer','irrigation')),
  optional      boolean not null default false,
  basis         text not null check (basis in ('source','planning_default')),
  window_start  date not null,
  window_end    date not null,
  status        text not null default 'pending' check (status in ('pending','done','skipped')),
  done_on       date,
  note          text check (note is null or char_length(note) <= 300),
  observation_id uuid references farm_observations(id) on delete set null,
  created_at    timestamptz not null default now(),
  constraint plan_task_window check (window_end >= window_start),
  constraint plan_task_done_date check ((status = 'done') = (done_on is not null)),
  constraint plan_task_same_farm foreign key (farm_crop_id, farm_id) references farm_crops(id, farm_id) on delete cascade,
  constraint plan_task_unique unique (farm_crop_id, task_key)
);
create index plan_tasks_due_idx on farm_plan_tasks(farm_id, status, window_start);
alter table farm_plan_tasks enable row level security;
-- Owners may READ and DELETE their tasks. Writes go only through the functions below, so a task cannot be ticked off without its timeline note.
create policy plan_tasks_owner_read   on farm_plan_tasks for select to authenticated using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));
create policy plan_tasks_owner_delete on farm_plan_tasks for delete to authenticated using (exists (select 1 from farms f where f.id = farm_id and f.owner_id = auth.uid()));

-- ---------------------------------------------------------------- save a computed plan (one call, one transaction)
create or replace function plan_tasks_save(p_farm_crop uuid, p_tasks jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare fid uuid; t jsonb; n int := 0; keys text[] := '{}';
begin
  select fc.farm_id into fid from farm_crops fc join farms f on f.id = fc.farm_id where fc.id = p_farm_crop and f.owner_id = auth.uid();
  if fid is null then raise exception 'farm crop not found'; end if;
  if jsonb_typeof(p_tasks) <> 'array' or jsonb_array_length(p_tasks) > 40 then raise exception 'a plan has at most 40 tasks'; end if;
  for t in select * from jsonb_array_elements(p_tasks) loop
    insert into farm_plan_tasks (farm_id, farm_crop_id, task_key, title, category, rain_class, optional, basis, window_start, window_end)
    values (fid, p_farm_crop, t->>'task_key', t->>'title', t->>'category', t->>'rain_class', coalesce((t->>'optional')::boolean, false),
            t->>'basis', (t->>'window_start')::date, (t->>'window_end')::date)
    on conflict (farm_crop_id, task_key) do update
      set title = excluded.title, category = excluded.category, rain_class = excluded.rain_class, optional = excluded.optional,
          basis = excluded.basis, window_start = excluded.window_start, window_end = excluded.window_end
      where farm_plan_tasks.status = 'pending';                 -- ticked tasks keep their dates and history
    keys := keys || (t->>'task_key');
    n := n + 1;
  end loop;
  delete from farm_plan_tasks where farm_crop_id = p_farm_crop and status = 'pending' and not (task_key = any(keys));   -- a changed plan drops tasks it no longer has
  return n;
end $$;
revoke all on function plan_tasks_save(uuid, jsonb) from public;
grant execute on function plan_tasks_save(uuid, jsonb) to authenticated;

-- ---------------------------------------------------------------- tick a task: also writes the farm timeline note
create or replace function plan_task_set(p_task uuid, p_status text, p_note text default null) returns text
language plpgsql security definer set search_path = public as $$
declare tk farm_plan_tasks%rowtype; kind text; oid uuid;
begin
  if p_status not in ('pending','done','skipped') then raise exception 'status must be pending, done or skipped'; end if;
  select t.* into tk from farm_plan_tasks t join farms f on f.id = t.farm_id where t.id = p_task and f.owner_id = auth.uid() for update of t;
  if tk.id is null then raise exception 'task not found'; end if;
  kind := case tk.category
            when 'planting' then 'planting' when 'top_dressing' then 'fertilizer' when 'spraying' then 'treatment'
            when 'pest_scouting' then 'observation' when 'harvest' then 'harvest' when 'irrigation' then 'irrigation' else 'note' end;
  if p_status = 'done' and tk.observation_id is null then
    insert into farm_observations (farm_id, farm_crop_id, kind, observed_on, text)
    values (tk.farm_id, tk.farm_crop_id, kind, current_date, left('Planting plan: ' || tk.title || coalesce(' — ' || nullif(trim(p_note), ''), ''), 2000))
    returning id into oid;
    update farm_plan_tasks set status = 'done', done_on = current_date, note = left(p_note, 300), observation_id = oid where id = p_task;
  elsif p_status = 'done' then
    update farm_plan_tasks set note = left(p_note, 300) where id = p_task;           -- already done: no second timeline note
  else
    if tk.observation_id is not null then delete from farm_observations where id = tk.observation_id; end if;
    update farm_plan_tasks set status = p_status, done_on = null, note = left(p_note, 300), observation_id = null where id = p_task;
  end if;
  return p_status;
end $$;
revoke all on function plan_task_set(uuid, text, text) from public;
grant execute on function plan_task_set(uuid, text, text) to authenticated;

-- ---------------------------------------------------------------- reminders (daily cron; same secret as the alert job)
create or replace function cron_plan_tasks(p_secret text, p_farms uuid[], p_today date)
returns table (id uuid, farm_id uuid, task_key text, title text, rain_class text, window_start date, window_end date, crop_name text, water_source text)
language plpgsql stable security definer set search_path = public as $$
begin
  if not cron_secret_ok(p_secret) then raise exception 'not allowed'; end if;
  return query
    select t.id, t.farm_id, t.task_key, t.title, t.rain_class, t.window_start, t.window_end, c.name_en, fc.water_source
    from farm_plan_tasks t join farm_crops fc on fc.id = t.farm_crop_id join crops c on c.id = fc.crop_id
    where t.farm_id = any(p_farms) and t.status = 'pending' and not t.optional and t.window_start <= p_today and t.window_end >= p_today;
end $$;
revoke all on function cron_plan_tasks(text, uuid[], date) from public;
grant execute on function cron_plan_tasks(text, uuid[], date) to anon, authenticated;
