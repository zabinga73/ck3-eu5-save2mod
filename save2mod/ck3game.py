"""Load the pieces of a CK3 install the converter needs."""
from __future__ import annotations

import csv
import glob
import os
import re
from dataclasses import dataclass, field

from .pdx import Block, parse_file, read_text
from .loc import load_localization_dir

TIER = {"b": 0, "c": 1, "d": 2, "k": 3, "e": 4, "h": 5}


@dataclass
class CK3TitleDef:
    key: str
    tier: int
    parent: str | None = None
    color: tuple[int, int, int] | None = None
    capital: str | None = None       # county key
    province: int | None = None      # baronies only
    children: list[str] = field(default_factory=list)


@dataclass
class CK3Game:
    root: str
    titles: dict[str, CK3TitleDef] = field(default_factory=dict)
    barony_of_province: dict[int, str] = field(default_factory=dict)
    province_colors: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    province_names: dict[int, str] = field(default_factory=dict)
    sea_provinces: set[int] = field(default_factory=set)
    lake_provinces: set[int] = field(default_factory=set)
    impassable_provinces: set[int] = field(default_factory=set)
    culture_heritage: dict[str, str] = field(default_factory=dict)
    culture_language: dict[str, str] = field(default_factory=dict)
    faith_religion: dict[str, str] = field(default_factory=dict)
    religion_family: dict[str, str] = field(default_factory=dict)
    buildings: list[str] = field(default_factory=list)
    loc: dict[str, str] = field(default_factory=dict)

    @property
    def provinces_png(self) -> str:
        return os.path.join(self.root, "map_data", "provinces.png")

    def county_of_barony(self, b: str) -> str | None:
        t = self.titles.get(b)
        return t.parent if t else None

    def name(self, key: str) -> str:
        return self.loc.get(key) or key.split("_", 1)[-1].replace("_", " ").title()


def find_game_dir(path: str) -> str:
    """Accept the install root, the 'game' dir or anything in between."""
    cands = [path, os.path.join(path, "game")]
    for c in cands:
        if os.path.isdir(os.path.join(c, "map_data")) and os.path.isdir(os.path.join(c, "common")):
            return c
    raise FileNotFoundError(f"Not a CK3 game folder (needs map_data/ and common/): {path}")


def _parse_ranges(text: str, key: str) -> set[int]:
    out: set[int] = set()
    for m in re.finditer(rf"^\s*{key}\s*=\s*(RANGE|LIST)\s*\{{([^}}]*)\}}", text, re.M):
        nums = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if m.group(1) == "RANGE" and len(nums) >= 2:
            out.update(range(nums[0], nums[1] + 1))
        else:
            out.update(nums)
    return out


def _walk_titles(block: Block, parent: str | None, out: dict[str, CK3TitleDef]) -> None:
    for k, v in block.pairs():
        if not isinstance(v, Block) or len(k) < 3 or k[1] != "_" or k[0] not in TIER:
            continue
        t = out.get(k) or CK3TitleDef(key=k, tier=TIER[k[0]])
        t.parent = t.parent or parent
        c = v.get("color")
        vals = None
        if isinstance(c, Block):
            vals = c.values()
        elif c is not None and hasattr(c, "block"):
            vals = c.block.values()
        if vals and len(vals) >= 3:
            try:
                f = [float(x) for x in vals[:3]]
                if max(f) <= 1.0:
                    f = [x * 255 for x in f]
                t.color = tuple(int(round(x)) for x in f)  # type: ignore[assignment]
            except ValueError:
                pass
        cap = v.get("capital")
        if isinstance(cap, str):
            t.capital = cap
        prov = v.get("province")
        if isinstance(prov, str) and prov.isdigit():
            t.province = int(prov)
        out[k] = t
        if parent and k not in out[parent].children:
            out[parent].children.append(k)
        _walk_titles(v, k, out)


def load_ck3_game(path: str, log=print, *, language: str = "english") -> CK3Game:
    root = find_game_dir(path)
    g = CK3Game(root=root)

    # ---- landed titles (de jure hierarchy + barony provinces)
    for f in sorted(glob.glob(os.path.join(root, "common", "landed_titles", "*.txt"))):
        _walk_titles(parse_file(f), None, g.titles)
    for t in g.titles.values():
        if t.tier == 0 and t.province is not None:
            g.barony_of_province[t.province] = t.key
    log(f"CK3: {len(g.titles)} titles, {len(g.barony_of_province)} baronies")

    # ---- definition.csv
    with open(os.path.join(root, "map_data", "definition.csv"), encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.reader(fh, delimiter=";"):
            if len(row) < 4 or not row[0].strip().isdigit():
                continue
            pid = int(row[0])
            if pid == 0:
                continue
            try:
                g.province_colors[pid] = (int(row[1]), int(row[2]), int(row[3]))
            except ValueError:
                continue
            if len(row) > 4:
                g.province_names[pid] = row[4]

    # ---- default.map water / impassable
    dm = read_text(os.path.join(root, "map_data", "default.map"))
    g.sea_provinces = _parse_ranges(dm, "sea_zones") | _parse_ranges(dm, "river_provinces")
    g.lake_provinces = _parse_ranges(dm, "lakes")
    g.impassable_provinces = (_parse_ranges(dm, "impassable_mountains") | _parse_ranges(dm, "impassable_seas")
                              | _parse_ranges(dm, "wasteland"))

    # ---- cultures (key -> heritage)
    for f in glob.glob(os.path.join(root, "common", "culture", "cultures", "*.txt")):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block):
                h = v.get("heritage")
                if isinstance(h, str):
                    g.culture_heritage[k] = h
                lang = v.get("language")
                if isinstance(lang, str):
                    g.culture_language[k] = lang

    # ---- faiths (faith -> religion -> family)
    for f in glob.glob(os.path.join(root, "common", "religion", "religion_types", "*.txt")):
        for rk, rv in parse_file(f).pairs():
            if not isinstance(rv, Block):
                continue
            fam = rv.get("family")
            if isinstance(fam, str):
                g.religion_family[rk] = fam
            faiths = rv.get("faiths")
            if isinstance(faiths, Block):
                for fk, fv in faiths.pairs():
                    if isinstance(fv, Block):
                        g.faith_religion[fk] = rk

    # ---- buildings
    for f in sorted(glob.glob(os.path.join(root, "common", "buildings", "*.txt"))):
        for k, v in parse_file(f).pairs():
            if isinstance(v, Block) and not k.startswith("@"):
                g.buildings.append(k)

    # ---- localization
    g.loc = load_localization_dir(os.path.join(root, "localization", language))
    log(f"CK3: {len(g.culture_heritage)} cultures, {len(g.faith_religion)} faiths, "
        f"{len(g.buildings)} building types, {len(g.loc)} loc keys")
    return g
