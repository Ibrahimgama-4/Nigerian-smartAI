-- KanoFarm AI — migration 0004: widen scope from Kano State to all of Nigeria.
-- Safe to run on a database that already has 0001-0003 applied. Does not touch existing data.

-- Farmers can now record which state they live in; drop the old Kano-only default so new
-- signups must pick their own. Existing rows keep whatever value they already have.
alter table profiles alter column state drop default;
alter table profiles alter column state drop not null;   -- relaxed so this migration can't fail on existing rows; the app still requires it on new profiles

-- Add Yoruba, Igbo and Nigerian Pidgin as language options (translations start empty, same as Hausa,
-- until reviewed by a native speaker; the app falls back to English until then).
alter table profiles drop constraint if exists profiles_language_check;
alter table profiles add constraint profiles_language_check check (language in ('en','ha','yo','ig','pcm'));

-- A farm's state can differ from its owner's home state (e.g. managing land in another state).
alter table farms add column if not exists state text;
