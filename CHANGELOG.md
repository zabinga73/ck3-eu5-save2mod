# Changelog

## 0.1.4
- Renamed to CK3 EU5 Save-2-Mod (package `save2mod`, command `python -m save2mod`, data folder `~/.save2mod`, moved automatically from `~/.ck3toeu5`).

## 0.1.3
- Prepared for public release: MIT license, simplified README, tests no longer need hard-coded game paths.

## 0.1.2
- CK3 impassable terrain no longer prevents EU5 locations from being mapped.
- Unmapped EU5 land surrounded by CK3 land joins the barony it borders most.
- Flag emblems narrowed less.

## 0.1.1
- CK3 flag textures missing from EU5 are copied into the mod; sub-coats and coat-of-arms templates are converted.

## 0.1.0
- Default subject tier is kingdom; administrative realms stay whole.
- Exclaves become vassals.
- At most one fort per CK3 county, with stricter level requirements.
- Realms & tags table with per-realm tag and name overrides.

## 0.0.4
- Fixed a crash when starting a game: vanilla countries are no longer deleted and always have a capital.
- Consistency check of the generated mod after each conversion.

## 0.0.3
- Generated mods target EU5 1.3.
- Option to skip character conversion.

## 0.0.2
- Fixed the GUI closing when a conversion starts.
- CK3 1.19 save support.

## 0.0.1
- First version.
