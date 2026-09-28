-- KanoFarm AI — migration 0005: add crops needed once the app covers all of Nigeria (not just the north).
-- Safe to run on a database that already has 0001-0004 applied.
insert into crops (slug, name_en) values
 ('yam','Yam'),('cocoa','Cocoa'),('oil_palm','Oil palm'),('plantain','Plantain / banana'),
 ('sesame','Sesame (beniseed)'),('cashew','Cashew'),('rubber','Rubber')
on conflict (slug) do nothing;
