# CK3 EU5 Save-2-Mod

Converts a Crusader Kings III save into a Europa Universalis V mod. The mod keeps EU5's map and 1337 start, and replaces the land covered by the CK3 map with the realms, rulers, cultures, religions, development, control and buildings from the save.

Version 0.1.5. Targets EU5 1.4 and CK3 1.20 (older CK3 saves still load).

## Requirements

- Python 3.10+
- numpy, pillow, scipy, PySide6

```
pip install -r requirements.txt
```

## Usage

GUI:

```
python -m save2mod
```

or run `run_save2mod.sh` (Linux/macOS) or `run_save2mod.bat` (Windows).

1. Set the CK3 game folder, EU5 game folder, CK3 save and EU5 mod folder. **Detect** finds Steam installs (native, Flatpak and Proton).
2. Choose options and press **Convert**.
3. Enable the generated mod in the EU5 launcher and start a new game.

Command line:

```
python -m save2mod convert --save path/to/save.ck3
python -m save2mod check path/to/generated_mod
python -m save2mod inspect path/to/save.ck3
```

Run `python -m save2mod convert --help` for all options.

Ironman saves are binary and must be converted to text first with [rakaly](https://github.com/rakaly/cli) (`rakaly melt save.ck3`). If `rakaly` is on the PATH this is done automatically.

## Conversion rules

| Area | Rule |
|---|---|
| Map | EU5 locations keep their shapes. Each takes the CK3 barony covering most of it. Unmapped EU5 land surrounded by CK3 land joins the barony it borders most. Land outside CK3's map stays vanilla. |
| Realms | Independent CK3 rulers become countries. Vassals at or above the subject tier (default: kingdom) become subjects; lower vassals merge into their liege. Governors of administrative realms stay part of their realm (option). |
| Exclaves | Parts of a country not connected to its capital (by land, a strait or a short sea crossing) become its vassals, with EU5-generated rulers (option). |
| HRE | If the save has an emperor, the EU5 HRE is rebuilt from his direct vassals. |
| Tags | Realms reuse the matching EU5 tag where one exists, otherwise get a new tag with the CK3 name, colour and coat of arms. |
| Flags | New countries use their CK3 coat of arms. CK3 textures missing from EU5 are copied from the local CK3 install into the mod. |
| Characters | Ruler, primary spouse and primary heir (option). |
| Culture / religion | Mapped through editable tables. |
| Pops | Vanilla sizes; culture and religion from CK3 (option: majority group only). |
| Development | CK3 county development (with a multiplier) replaces EU5's regional base. |
| Control | CK3 county control applied at game start (option). |
| Buildings | Mapped through an editable table, levels scaled down. At most one fort per CK3 county. EU5's own buildings are kept (option). |
| Everything else | Vanilla 1337. Vanilla countries whose land is all taken become landless. |

## Customisation

The **Mappings** tab edits the tables used by the conversion:

- `culture_map.csv`: CK3 culture → EU5 culture
- `religion_map.csv`: CK3 rite, faith or religion → EU5 religion
- `building_map.csv`: CK3 building → EU5 building
- `title_tags.csv` (Realms & tags): per-realm EU5 tag and name overrides; filled with every realm after each conversion

Edited tables are saved to `~/.save2mod/tables/` and override the defaults in `data/`. Single locations can be reassigned in `~/.save2mod/tables/map_overrides.csv` (`eu5_location,ck3_barony`, or `-` to keep it vanilla).

The first conversion aligns the two maps and caches the result in `~/.save2mod/cache/`. Set `SAVE2MOD_HOME` to use a different folder.

## Output

The mod folder contains `save2mod_report.txt`, which lists every country, unmapped cultures, religions and buildings, and the result of a consistency check of the generated setup files. It also contains `save2mod_preview.png`, a map of the result.

## Not converted

Wars, alliances, traits, nicknames, artifacts and characters other than rulers, spouses and heirs.

## Tests

```
pytest tests
```

The end-to-end test needs the games' text files. Set `CK3_GAME` and `EU5_GAME` to their `game` folders.

## License

MIT, see `LICENSE`. Crusader Kings III and Europa Universalis V are trademarks of Paradox Interactive. This project is not affiliated with Paradox Interactive and contains no game files.
