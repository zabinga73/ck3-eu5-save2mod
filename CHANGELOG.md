# Changelog

## 0.1.9
- Option to include CK3 trait and education skill bonuses in ruler stats (off by default).

## 0.1.8
- Option to drop vanilla's Mamluk government reform, laws and regnal numbers from a CK3 realm on the MAM tag (off by default).

## 0.1.7
- The Egypt/Mamluks name fix is an option (on by default).

## 0.1.6
- Ruler stats rescaled: ADM = 45% stewardship + 45% learning + 10% intrigue, DIP = 85% diplomacy + 15% intrigue, MIL = 85% martial + 15% intrigue, with one multiplier per save so the best value is 100 (they were far below vanilla's).
- Egypt no longer shows as the Mamluks when a CK3 realm takes EU5's MAM tag: EU5's MAM-only naming rules are switched off in the mod.
- The HRE emperor's country is named after the title holding his capital, else the one holding most of his counties.

## 0.1.5
- EU5 1.4 support: the start setup is read from and written to the bookmark's folder (`main_menu/setup/1337`). Mods made with earlier versions must be converted again.
- CK3 1.20 support: rites (religion → faith → rite) are mapped; the religion table takes rite, faith or religion keys.
- Religious orders whose 1337 head belonged to a country now ruled from CK3 start without a head; the self-check verifies order heads.
- CK3 ecclesiastical government becomes a theocracy; steppe administrative realms become hordes and keep their governors' land.
- New table rows: Miaphysite rites, Hussites, pre-schism Christianity, camel farms, water temples, kora-kora yards; nomad and herder camps, longhouses and wantilan are skipped.
- Option to name countries as CK3 shows them (historical names, renames).
- Mod metadata defaults to EU5 1.4.

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
