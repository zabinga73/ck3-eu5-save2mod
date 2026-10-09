"""Load the pieces of an EU5 install the converter needs."""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

from .pdx import Block, parse_file, parse_text, read_text
from .loc import load_localization_dir

LEVELS = ["continent", "subcontinent", "region", "area", "province", "location"]


@dataclass
class BuildingType:
    key: str
    ranks: dict[str, bool] = field(default_factory=dict)   # rural_settlement/town/city/megalopolis
    category: str | None = None
    pop_type: str | None = None
    is_foreign: bool = False
    max_levels: str | None = None
    obsolete: str | None = None
    location_potential: Block | None = None
    estate: str | None = None
    unique: bool = False
    file: str = ""

    def allowed_in_rank(self, rank: str) -> bool:
        if not self.ranks:
            return True
        return bool(self.ranks.get(rank, False))


@dataclass
class LocationInfo:
    key: str
    color: int                     # packed 0xRRGGBB
    province: str | None = None
    area: str | None = None
    region: str | None = None
    subcontinent: str | None = None
    continent: str | None = None
    topography: str | None = None
    vegetation: str | None = None
    climate: str | None = None
    religion: str | None = None
    culture: str | None = None
    raw_material: str | None = None
    harbor: float = 0.0
    kind: str = "land"            # land / sea / lake / wasteland / corridor


@dataclass
class EU5Game:
    root: str
    locations: dict[str, LocationInfo] = field(default_factory=dict)
    loc_order: list[str] = field(default_factory=list)
    hierarchy_children: dict[str, list[str]] = field(default_factory=dict)  # unit -> child units / locations
    unit_locs: dict[str, list[str]] = field(default_factory=dict)          # unit -> all locations inside
    unit_level: dict[str, str] = field(default_factory=dict)                # unit -> level name
    coastal: set[str] = field(default_factory=set)
    religions: dict[str, Block] = field(default_factory=dict)
    religion_group: dict[str, str] = field(default_factory=dict)
    cultures: dict[str, Block] = field(default_factory=dict)
    culture_groups: dict[str, list[str]] = field(default_factory=dict)
    buildings: dict[str, BuildingType] = field(default_factory=dict)
    country_defs: dict[str, Block] = field(default_factory=dict)
    country_def_file: dict[str, str] = field(default_factory=dict)
    templates: set[str] = field(default_factory=set)
    town_setups: dict[str, Block] = field(default_factory=dict)
    coa_keys: set[str] = field(default_factory=set)
    heir_selections: dict[str, str] = field(default_factory=dict)   # key -> file stem (monarchy/republic..)
    government_types: list[str] = field(default_factory=list)
    country_ranks: list[str] = field(default_factory=list)
    estates: list[str] = field(default_factory=list)
    loc: dict[str, str] = field(default_factory=dict)
    start_date: str = "1337.4.1"
    setup_rel: tuple[str, ...] = ("main_menu", "setup", "start")   # the start's setup folder (1.4+: setup/1337)

    # paths -----------------------------------------------------------------
    def p(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    @property
    def locations_png(self) -> str:
        return self.p("in_game", "map_data", "locations.png")

    def setup_start(self, name: str) -> str:
        return self.p(*self.setup_rel, name)

    def setup_start_files(self) -> list[str]:
        return sorted(os.path.basename(f) for f in glob.glob(self.p(*self.setup_rel, "*.txt")))

    # helpers ---------------------------------------------------------------
    def land_locations(self) -> list[str]:
        return [k for k in self.loc_order if self.locations[k].kind == "land"]

    def locations_in(self, unit: str) -> list[str]:
        """Locations inside a region/area/province (or the location itself)."""
        if unit in self.unit_locs:
            return list(self.unit_locs[unit])
        if unit in self.locations:
            return [unit]
        return []

    def name(self, key: str) -> str:
        return self.loc.get(key) or key.replace("_", " ").title()


def find_game_dir(path: str) -> str:
    for c in (path, os.path.join(path, "game")):
        if os.path.isdir(os.path.join(c, "in_game")) and os.path.isdir(os.path.join(c, "main_menu")):
            return c
    raise FileNotFoundError(f"Not an EU5 game folder (needs in_game/ and main_menu/): {path}")


def _hex(s: str) -> int | None:
    s = s.strip().lstrip("#")
    try:
        return int(s, 16)
    except ValueError:
        return None


def _walk_hierarchy(b: Block, depth: int, parents: list[str], g: EU5Game) -> None:
    for k, v in b.pairs():
        if not isinstance(v, Block):
            continue
        lvl = LEVELS[depth] if depth < len(LEVELS) else "?"
        g.unit_level[k] = lvl
        if parents:
            g.hierarchy_children.setdefault(parents[-1], []).append(k)
        if lvl == "province":
            for loc in v.values():
                if not isinstance(loc, str):
                    continue
                g.hierarchy_children.setdefault(k, []).append(loc)
                for unit in parents + [k]:
                    g.unit_locs.setdefault(unit, []).append(loc)
                li = g.locations.get(loc)
                if li is None:
                    continue
                chain = parents + [k]
                # chain = continent, subcontinent, region, area, province
                names = dict(zip(LEVELS, chain))
                li.continent = names.get("continent")
                li.subcontinent = names.get("subcontinent")
                li.region = names.get("region")
                li.area = names.get("area")
                li.province = names.get("province")
        else:
            _walk_hierarchy(v, depth + 1, parents + [k], g)


def _list_block(text_block: Block, key: str) -> set[str]:
    out: set[str] = set()
    for v in text_block.getall(key):
        if isinstance(v, Block):
            out.update(x for x in v.values() if isinstance(x, str))
    return out


def _find_bookmark(g: EU5Game, log=print) -> None:
    """EU5 1.4 moved the start setup from main_menu/setup/start to the folder
    named by the bookmark (setup/1337). Take the bookmark of the game's start
    date, else the first one; keep the old folder when there is none."""
    marks: list[Block] = []
    for f in sorted(glob.glob(g.p("main_menu", "common", "bookmarks", "*.txt"))):
        try:
            marks += [v for _k, v in parse_file(f).pairs() if isinstance(v, Block) and v.str("setup_folder")]
        except Exception as e:          # noqa: BLE001
            log(f"(could not read bookmark file {os.path.basename(f)}: {e})")
    if not marks:
        return
    b = next((m for m in marks if m.str("start_date") == g.start_date), marks[0])
    rel = ("main_menu", *b.str("setup_folder").replace("\\", "/").strip("/").split("/"))
    if os.path.isdir(g.p(*rel)):
        g.setup_rel = rel
        g.start_date = b.str("start_date") or g.start_date
    log(f"EU5: start {g.start_date}, setup in {'/'.join(g.setup_rel)}")


def load_eu5_game(path: str, log=print, *, language: str = "english") -> EU5Game:
    root = find_game_dir(path)
    g = EU5Game(root=root)
    md = g.p("in_game", "map_data")

    # ---- start date
    for f in glob.glob(g.p("loading_screen", "common", "defines", "*.txt")):
        m = re.search(r'START_DATE\s*=\s*"([\d.]+)"', read_text(f))
        if m:
            g.start_date = m.group(1)
            break
    _find_bookmark(g, log)

    # ---- named locations (colors)
    for f in sorted(glob.glob(os.path.join(md, "named_locations", "*.txt"))):
        for line in read_text(f).splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" not in line:
                continue
            k, v = (x.strip() for x in line.split("=", 1))
            c = _hex(v)
            if k and c is not None and k not in g.locations:
                g.locations[k] = LocationInfo(key=k, color=c)
                g.loc_order.append(k)
    log(f"EU5: {len(g.locations)} named locations")

    # ---- hierarchy
    _walk_hierarchy(parse_file(os.path.join(md, "definitions.txt")), 0, [], g)

    # ---- location templates
    lt = parse_file(os.path.join(md, "location_templates.txt"))
    for k, v in lt.pairs():
        li = g.locations.get(k)
        if li is None or not isinstance(v, Block):
            continue
        li.topography = v.str("topography")
        li.vegetation = v.str("vegetation")
        li.climate = v.str("climate")
        li.religion = v.str("religion")
        li.culture = v.str("culture")
        li.raw_material = v.str("raw_material")
        li.harbor = v.num("natural_harbor_suitability", 0.0) or 0.0

    # ---- water / wasteland
    dm = parse_file(os.path.join(md, "default.map"))
    for key, kind in (("sea_zones", "sea"), ("lakes", "lake"),
                      ("impassable_mountains", "wasteland"), ("non_ownable", "corridor")):
        for loc in _list_block(dm, key):
            if loc in g.locations:
                g.locations[loc].kind = kind

    # ---- coastal (ports.csv)
    pc = os.path.join(md, "ports.csv")
    if os.path.exists(pc):
        for line in read_text(pc).splitlines():
            parts = line.split(";")
            if len(parts) >= 2 and parts[0] in g.locations:
                g.coastal.add(parts[0])

    common = g.p("in_game", "common")
    # ---- religions / cultures
    for f in glob.glob(os.path.join(common, "religions", "*.txt")):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.religions[k] = v
                grp = v.str("group")
                if grp:
                    g.religion_group[k] = grp
    for f in glob.glob(os.path.join(common, "cultures", "*.txt")):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.cultures[k] = v
                cg = v.get("culture_groups")
                g.culture_groups[k] = [x for x in cg.values()] if isinstance(cg, Block) else []
    log(f"EU5: {len(g.religions)} religions, {len(g.cultures)} cultures")

    # ---- building types
    for f in sorted(glob.glob(os.path.join(common, "building_types", "*.txt"))):
        stem = os.path.basename(f)
        for k, v in parse_file(f).pairs():
            if not isinstance(v, Block) or k.startswith("@"):
                continue
            bt = BuildingType(key=k, file=stem)
            for r in ("rural_settlement", "town", "city", "megalopolis"):
                val = v.str(r)
                if val is not None:
                    bt.ranks[r] = val == "yes"
            # buildings that specify only some ranks: unspecified -> not allowed
            if bt.ranks:
                for r in ("rural_settlement", "town", "city", "megalopolis"):
                    bt.ranks.setdefault(r, False)
            bt.category = v.str("category")
            bt.pop_type = v.str("pop_type")
            bt.is_foreign = v.str("is_foreign") == "yes"
            bt.max_levels = v.str("max_levels")
            bt.obsolete = v.str("obsolete")
            bt.estate = v.str("estate")
            lp = v.get("location_potential")
            bt.location_potential = lp if isinstance(lp, Block) else None
            bt.unique = stem.startswith(("unique", "event_only", "00_unique")) or stem in ("capital_buildings.txt",)
            g.buildings[k] = bt
    log(f"EU5: {len(g.buildings)} building types")

    for f in glob.glob(os.path.join(common, "town_setups", "*.txt")):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.town_setups[k] = v

    # ---- governments / heir selection / ranks / estates
    for f in glob.glob(os.path.join(common, "government_types", "*.txt")):
        g.government_types += [k for k, v in parse_file(f).pairs() if isinstance(v, Block)]
    for f in glob.glob(os.path.join(common, "heir_selections", "*.txt")):
        stem = os.path.splitext(os.path.basename(f))[0]
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.heir_selections[k] = stem
    for f in glob.glob(os.path.join(common, "country_ranks", "*.txt")):
        g.country_ranks += [k for k, v in parse_file(f).pairs() if isinstance(v, Block)]
    for f in glob.glob(os.path.join(common, "estates", "*.txt")):
        g.estates += [k for k, v in parse_file(f).pairs() if isinstance(v, Block)]

    # ---- country definitions
    for f in sorted(glob.glob(g.p("in_game", "setup", "countries", "*.txt"))):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.country_defs[k] = v
                g.country_def_file[k] = os.path.basename(f)
    g.templates = {os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(g.p("main_menu", "setup", "templates", "*.txt"))}
    for f in glob.glob(g.p("main_menu", "common", "coat_of_arms", "coat_of_arms", "*.txt")):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                g.coa_keys.add(k)
    log(f"EU5: {len(g.country_defs)} country definitions, {len(g.templates)} setup templates")

    # ---- localization (main_menu + in_game)
    g.loc = load_localization_dir(g.p("main_menu", "localization", language))
    g.loc.update(load_localization_dir(g.p("in_game", "localization", language)))
    log(f"EU5: {len(g.loc)} localization keys")
    return g
