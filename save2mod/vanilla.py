"""Read EU5's vanilla 1337 start (main_menu/setup/start) into structures the
converter can filter and re-emit."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .pdx import Block, parse_file

OWN_KEYS = ("own_control_core", "own_control_integrated", "own_control_conquered", "own_control_colony",
            "own_core", "own_conquered", "own_integrated", "own_colony")
CONTROL_KEYS = ("control_core", "control")
CORE_KEYS = ("our_cores_conquered_by_others",)


@dataclass
class VanillaCountry:
    tag: str
    block: Block
    owned: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    tech: int | None = None


@dataclass
class VanillaSetup:
    files: dict[str, Block] = field(default_factory=dict)        # file name -> parsed
    countries: dict[str, VanillaCountry] = field(default_factory=dict)
    owner_of: dict[str, str] = field(default_factory=dict)       # location -> tag
    pops: dict[str, list[Block]] = field(default_factory=dict)   # location -> define_pop blocks
    city_entries: dict[str, Block] = field(default_factory=dict) # location -> {rank, town_setup}
    buildings: list[tuple[str, Block]] = field(default_factory=list)   # (type, {tag level location})
    characters: dict[str, Block] = field(default_factory=dict)
    char_order: list[str] = field(default_factory=list)
    top_of_10: list[tuple] = field(default_factory=list)          # entries before "countries" (current_age ...)

    def majority_culture(self, loc: str) -> tuple[str | None, str | None]:
        best: dict[tuple[str, str], float] = {}
        for p in self.pops.get(loc, []):
            k = (p.str("culture") or "", p.str("religion") or "")
            best[k] = best.get(k, 0.0) + (p.num("size", 0.0) or 0.0)
        if not best:
            return None, None
        (c, r), _ = max(best.items(), key=lambda kv: kv[1])
        return c or None, r or None


def load_vanilla(eu5, log=print) -> VanillaSetup:
    v = VanillaSetup()
    for name in eu5.setup_start_files():
        try:
            v.files[name] = parse_file(eu5.setup_start(name))
        except Exception as e:  # pragma: no cover
            log(f"WARNING: could not parse {name}: {e}")

    # ---- countries
    b10 = v.files.get("10_countries.txt")
    if b10 is not None:
        for k, op, val in b10.items:
            if k != "countries":
                v.top_of_10.append((k, op, val))
        inner = b10.block("countries").block("countries")
        for tag, cb in inner.pairs():
            if not isinstance(cb, Block):
                continue
            vc = VanillaCountry(tag=tag, block=cb)
            for key in OWN_KEYS:
                for lst in cb.getall(key):
                    if isinstance(lst, Block):
                        vc.owned += [x for x in lst.values() if isinstance(x, str)]
            vc.includes = [x for x in cb.getall("include") if isinstance(x, str)]
            t = cb.num("starting_technology_level")
            vc.tech = int(t) if t is not None else None
            v.countries[tag] = vc
            for loc in vc.owned:
                v.owner_of.setdefault(loc, tag)

    # ---- pops
    b6 = v.files.get("06_pops.txt")
    if b6 is not None:
        for loc, lb in b6.block("locations").pairs():
            if isinstance(lb, Block):
                v.pops[loc] = [p for p in lb.getall("define_pop") if isinstance(p, Block)]

    # ---- cities & buildings
    b7 = v.files.get("07_cities_and_buildings.txt")
    if b7 is not None:
        for loc, lb in b7.block("locations").pairs():
            if isinstance(lb, Block):
                v.city_entries[loc] = lb
        for btype, bb in b7.block("building_manager").pairs():
            if isinstance(bb, Block):
                v.buildings.append((btype, bb))

    # ---- characters
    b5 = v.files.get("05_characters.txt")
    if b5 is not None:
        for key, cb in b5.block("character_db").pairs():
            if isinstance(cb, Block):
                v.characters[key] = cb
                v.char_order.append(key)
    log(f"Vanilla 1337 setup: {len(v.countries)} countries, {len(v.pops)} populated locations, "
        f"{len(v.characters)} characters")
    return v


def rank_of(v: VanillaSetup, loc: str) -> str:
    e = v.city_entries.get(loc)
    r = e.str("rank") if e is not None else None
    return r or "rural_settlement"


def date_tuple(s: str | None) -> tuple[int, int, int] | None:
    if not s:
        return None
    try:
        parts = [int(x) for x in s.split(".")[:3]]
        while len(parts) < 3:
            parts.append(1)
        return tuple(parts)  # type: ignore[return-value]
    except ValueError:
        return None
