-- Crop names only (no agronomic claims). Hausa names stay NULL until reviewed by a native-speaking agronomist.
insert into crops (slug, name_en) values
 ('maize','Maize'),('rice','Rice'),('sorghum','Sorghum'),('millet','Millet'),('wheat','Wheat'),
 ('cowpea','Cowpea'),('groundnut','Groundnut'),('soybean','Soybean'),('tomato','Tomato'),
 ('pepper','Pepper'),('onion','Onion'),('cassava','Cassava'),('potato','Potato'),('vegetables','Vegetables (other)')
on conflict (slug) do nothing;
