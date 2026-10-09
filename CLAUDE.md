# CK3 EU5 Save-2-Mod

A Python tool (PySide6 GUI and CLI) that converts a Crusader Kings III save into a Europa Universalis V mod. The owner develops on Linux, with CK3 running natively and EU5 through Proton.

## How to work with the owner
- Ask at ANY design crossroads. Don't guess on choices that change gameplay. Ambiguous table rows (cultures, faiths, buildings) go to the owner as questions.
- Be direct and concise.
- Every release gets a new version number (0.1.8 is the latest). Never reuse one. Bump `save2mod/__init__.py`, the README and `CHANGELOG.md` together.
- Keep the README to bare program info. History goes in CHANGELOG.md.
- The repo is public under the MIT license. Never commit game files.
- Stay independent of the ParadoxGameConverters CK3ToEU5 project. Don't copy its code.

## Commands
- GUI: `python -m save2mod`
- Convert: `python -m save2mod convert --save <save.ck3>` (paths auto-detected; `--help` for options)
- Self-check a mod: `python -m save2mod check <mod folder>`
- Save structure: `python -m save2mod inspect <save.ck3>`
- Tests: `pytest tests`. The end-to-end test needs `CK3_GAME` and `EU5_GAME` set to the games' `game/` folders.
- Install paths are found by `save2mod/paths.py`. EU5's mod folder is in the Proton prefix: `compatdata/3450310/pfx/drive_c/users/steamuser/Documents/Paradox Interactive/Europa Universalis V/mod`.

## Architecture (save2mod/)
- `pdx.py`: Paradox script parser and writer.
- `ck3save.py`: save loader (zipped saves; ironman saves through rakaly).
- `ck3game.py`, `eu5game.py`: game data loaders.
- `vanilla.py`: EU5's 1337 setup.
- `mappings.py`: CSV tables. User copies in `~/.save2mod/tables` override `data/`.
- `geomap.py`: automatic map alignment (name-matched control points, RANSAC polynomial plus thin-plate correction, pixel overlap), cached in `~/.save2mod/cache`.
- `adjacency.py`: location borders from locations.png.
- `convert.py`: the conversion into a `World` object.
- `writer.py`: writes the mod (`DEFAULT_GAME_VERSION` lives here; the GUI has its own default in `gui.py`).
- `coa.py`: flags. `validate.py`: self-check. `pipeline.py`, `gui.py`, `__main__.py`.

## Game files the tool reads
- EU5: the 1337 setup (`main_menu/setup/1337/*.txt` since 1.4; `main_menu/setup/start/` before), `in_game/setup/countries`, `main_menu/setup/templates`, `in_game/map_data` (definitions.txt, named_locations, location_templates.txt, default.map, ports.csv, adjacencies.csv, locations.png), `in_game/common` (religions, cultures, building_types, town_setups, government_types, heir_selections, country_ranks, estates), `main_menu/common` (coat_of_arms, named_colors, bookmarks), localization, and the start date.
- CK3: the save, `common/landed_titles`, `common/culture`, `common/religion`, `common/buildings`, named_colors, coat_of_arms, `map_data` (definition.csv, default.map, provinces.png), `gfx/coat_of_arms`.

## Conversion rules (decided with the owner)
- Map: EU5 shapes stay untouched. Each EU5 location takes the CK3 barony covering most of it; CK3 impassable terrain counts as neutral. Unmapped pockets of up to 40 locations surrounded by CK3 land join the barony they border most. Everything outside the CK3 map stays vanilla 1337.
- Realms: independent rulers become countries. Vassals at or above the subject tier (default kingdom) become EU5 subjects; lower vassals merge into their liege. Governors of administrative or celestial realms stay part of their realm (toggle).
- Exclaves: parts not connected to the capital through own or subject land, a strait, or at most 2 sea, lake or wasteland locations become vassals with EU5-generated rulers. Every exclave counts, whatever its size.
- HRE: rebuilt if e_hre is held. All of the emperor's direct vassals, counts included, become independent members.
- Tags: reuse a matching EU5 tag (by name, or the title_tags.csv realm list) only if the realm overlaps that tag's 1337 land; reused tags keep their land outside the CK3 map. Otherwise a new tag with the CK3 name, colour and coat of arms. EU5's "historic" tags are never reused.
- Stats: ADM = 45% stewardship + 45% learning + 10% intrigue, DIP = 85% diplomacy + 15% intrigue, MIL = 85% martial + 15% intrigue (CK3 base skills, prowess unused), one multiplier per save so the best value is 100.
- Names: a reused tag that EU5 names by tag-specific rules (MAM → Mamluks) gets those rules switched off (toggle `own_names_for_tag_rules`, default on) ('tag = MAM' → 'always = no' in copies of customizable_localization files). The HRE emperor's country is named after his highest non-HRE title, ties broken by the title holding his capital, then by most of his counties.
- A reused MAM keeps vanilla's government block when religion group and government type match; toggle `strip_tag_government` (default off) drops mamluk_government, mamluk_* laws and regnal numbers.
- Characters: ruler, primary spouse and primary heir only. Rulers are aged up to at least 16. EU5 derives heirs itself.
- Governments: CK3 ecclesiastical → theocracy; steppe_admin → steppe_horde and counts as an administrative realm (kept whole).
- Title names: CK3's displayed names (title_name_data) are used only with the `ck3_title_names` toggle (default off).
- Culture and religion come from the tables. CK3 1.20 characters and counties point at a rite; the religion table is looked up by rite, then faith, then religion. EU5's regional splits of a CK3 culture keep the 1337 variant where one applies (e.g. Occitan becomes Limousin or Auvergnat). Slavic pagan → komi_paganism. Heresies → the nearest EU5 heresy. Magyar pagan → Tengrism. Lollardy is kept.
- Development is CK3 county dev times a multiplier, replacing EU5's regional base. CK3 control is applied at game start (toggle). Pops keep vanilla sizes and take CK3 culture and religion (toggle).
- Buildings come from building_map.csv: rank-aware, levels halved and capped at 3, and EU5's own buildings are kept (toggle). Forts: at most one per CK3 county; castle needs CK3 level 5+, walls and palisades level 4+, watchtowers and outposts never.
- Flags: CK3 coat of arms. CK3 textures EU5 lacks are copied from the local CK3 install into the mod. Charges are narrowed ×0.75 on the 3:2 flag; ordinaries span the flag.
- Vanilla countries are never deleted. Fully covered ones become landless and keep a capital; partly covered ones keep the rest of their land, with the capital moved if it was taken. Pop-type countries are untouched.

## EU5 setup rules (breaking these crashed the game with "Invalid Element Index")
- Every country definition must have a 10_countries entry (DUMMY, MER and PIR excepted).
- Every character's tag must exist in 10_countries.
- Every non-pop country needs a capital.
- No setup file may reference a country missing from 10_countries.
- (1.4) Every religious order's head_character must be in 05_characters; the writer drops heads it removed.

`validate.py` checks all of this, ignoring anything vanilla itself does. Add any new rule a patch introduces.

## Open items
- Load time hasn't been investigated; it needs debug.log timings with and without the mod.
- Several religion rows are marked "approximate" (Basque, Berber, Mande/Soninke, Nubian, Zun, Qiangic, Malay, Korean Muism, gnostic faiths, acharya).
- Not converted: wars, alliances, traits, nicknames, artifacts, court members.
