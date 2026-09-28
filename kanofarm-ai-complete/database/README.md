# Database setup (Supabase)
Run in the Supabase SQL editor, in order: `0001_core.sql`, `0002_features.sql`, `0003_reference_crops.sql`,
`0004_nationwide.sql`, `0005_expand_crops.sql`, `0006_market.sql`.

**Upgrading an existing database that only covered Kano State?** You already ran 0001-0003. Just run `0004_nationwide.sql`
then `0005_expand_crops.sql` — both are safe to run once, and 0005 is also safe to run again if unsure.
To make yourself an admin (after signing up): 
`insert into user_roles (user_id, role) values ('<your auth user id>', 'admin');`
Deviation from the original spec: irrigation, fertilizer, harvest and treatment records are stored as kinds in
`farm_observations` instead of separate tables; diseases/pests reference content lives in `kanofarm/knowledge/`
until verified sources exist for a database table.

**Adding the Farm Market to an existing database?** Run just `0006_market.sql` (needs 0001 and 0002 already applied).
