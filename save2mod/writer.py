"""Write the converted world out as an EU5 mod."""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime

from . import __version__
from .coa import CoaConverter
from .convert import World, Country
from .loc import write_yml
from .pdx import Block, QStr, Tagged, dump, fmt_value, parse_file, parse_text
from .vanilla import (VanillaSetup, OWN_KEYS, CONTROL_KEYS, CORE_KEYS, date_tuple, rank_of)

LANGS = ["english", "french", "german", "spanish", "polish", "russian", "simp_chinese",
         "japanese", "korean", "turkish", "braz_por"]
DEFAULT_GAME_VERSION = "1.3.*"      # EU5 1.3.x "Pavia"; change in the GUI when a new patch is out
RELIGIOUS_IOS = {"catholic_church", "autocephalous_patriarchate", "shinto"}


def mod_id_from_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return s or "ck3_conversion"


class ModWriter:
    def __init__(self, world: World, eu5, ck3, save, vanilla: VanillaSetup, out_dir: str, log=print):
        self.w = world
        self.eu5 = eu5
        self.ck3 = ck3
        self.sv = save
        self.v = vanilla
        self.log = log
        self.mod_id = mod_id_from_name(world.options.mod_name)
        self.root = os.path.join(out_dir, self.mod_id)
        self.alive_tags = self._alive_tags()
        # every tag written to 10_countries (landed or not)
        self.setup_tags = set(self.v.countries) | {t for t, c in world.countries.items() if c.locations}
        self.removed_chars: set[str] = set()

    # ------------------------------------------------------------ basics
    def path(self, *p: str) -> str:
        fp = os.path.join(self.root, *p)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        return fp

    def write_block(self, rel: tuple[str, ...], block: Block, header: str = "", bom: bool = False) -> None:
        text = (header + "\n" if header else "") + dump(block)
        with open(self.path(*rel), "w", encoding="utf-8-sig" if bom else "utf-8", newline="\n") as fh:
            fh.write(text)

    def _alive_tags(self) -> set[str]:
        """Countries that still hold land (or pops) at the start."""
        tags = {t for t, c in self.w.countries.items() if c.locations}
        for t in self.v.countries:
            if t not in self.w.removed_tags:
                tags.add(t)
        return tags

    # -------------------------------------------------------------- main
    def write(self) -> str:
        if os.path.isdir(self.root):
            shutil.rmtree(self.root)
        os.makedirs(self.root, exist_ok=True)
        self._metadata()
        self._characters()          # decides removed vanilla characters first
        self._dynasties()
        self._countries_setup()
        self._country_definitions()
        self._pops()
        self._cities_and_buildings()
        self._development()
        self._location_templates()
        self._diplomacy()
        self._ios()
        self._misc_setup()
        self._on_actions()
        self._coas()
        self._localization()
        self._report()
        self.log(f"Mod written to {self.root}")
        return self.root

    # --------------------------------------------------------- metadata
    def _metadata(self) -> None:
        ver = self._eu5_version()
        md = {
            "name": self.w.options.mod_name,
            "id": self.mod_id,
            "version": __version__,
            "game_id": "eu5",
            "supported_game_version": ver,
            "short_description": f"Converted from a CK3 save ({self.sv.date}) by save2mod {__version__}",
            "picture": "thumbnail.png",
            "tags": ["Map", "Historical"],
            "relationships": [],
            "game_custom_data": {},
        }
        with open(self.path(".metadata", "metadata.json"), "w", encoding="utf-8") as fh:
            json.dump(md, fh, indent=2, ensure_ascii=False)

    def _eu5_version(self) -> str:
        if self.w.options.game_version.strip():
            return self.w.options.game_version.strip()
        for cand in (os.path.join(os.path.dirname(self.eu5.root), "launcher-settings.json"),
                     os.path.join(os.path.dirname(self.eu5.root), "launcher", "launcher-settings.json"),
                     os.path.join(self.eu5.root, "launcher-settings.json")):
            try:
                with open(cand, encoding="utf-8") as fh:
                    d = json.load(fh)
                raw = d.get("rawVersion") or d.get("version") or ""
                m = re.search(r"(\d+)\.(\d+)", str(raw))
                if m:
                    return f"{m.group(1)}.{m.group(2)}.*"
            except (OSError, ValueError):
                continue
        return DEFAULT_GAME_VERSION

    # ------------------------------------------------------- characters
    def _characters(self) -> None:
        start = date_tuple(self.eu5.start_date) or (1337, 4, 1)
        # Only the living characters of tags now used by a CK3 realm are replaced.
        # Vanilla countries left without land keep theirs (vanilla has hundreds of
        # landless countries with rulers), and every character keeps a tag that
        # exists in 10_countries, as in vanilla.
        replaced = self.w.reused_tags
        setup_tags = self.setup_tags
        keep = Block()
        # 1. vanilla characters: drop living ones of replaced countries
        for key in self.v.char_order:
            cb = self.v.characters[key]
            tag = cb.str("tag")
            death = date_tuple(cb.str("death_date"))
            alive = death is None or death > start
            if (tag in replaced and alive) or (tag and tag not in setup_tags):
                self.removed_chars.add(key)
        for key in self.v.char_order:
            if key in self.removed_chars:
                continue
            cb = self.v.characters[key]
            nb = Block([it for it in cb.items
                        if not (it[0] in ("father", "mother", "spouse") and isinstance(it[2], str)
                                and it[2] in self.removed_chars)])
            keep.add(key, nb)
        # 2. converted characters: rulers first, then consorts (spouse -> ruler), then heirs
        order = sorted(self.w.characters.values(),
                       key=lambda c: (0 if c.spouse is None and not self._is_heir(c.key) else
                                      (1 if c.spouse else 2), c.key))
        for c in order:
            b = Block()
            b.add("first_name", Block([("name", "=", c.first_name)]))
            b.add("culture", c.culture)
            b.add("religion", c.religion)
            b.items.append(("adm", "=", str(c.adm)))
            b.items.append(("dip", "=", str(c.dip)))
            b.items.append(("mil", "=", str(c.mil)))
            b.add("birth_date", c.birth)
            if c.female:
                b.add("female", "yes")
            if c.birth_loc:
                b.add("birth", c.birth_loc)
            if c.estate:
                b.add("estate", c.estate)
            if c.dynasty:
                b.add("dynasty", c.dynasty)
            country = self.w.countries[c.tag]
            if self._is_heir(c.key):
                ruler_key = getattr(country, "ruler_key", None)
                ruler = self.sv.characters.get(country.ruler)
                if ruler is not None and ruler_key and c.ck3_id in ruler.children:
                    b.add("father" if not ruler.female else "mother", ruler_key)
                    if country.consort:
                        cons = self.w.characters.get(country.consort)
                        if cons is not None:
                            b.add("mother" if not ruler.female else "father", country.consort)
            if c.spouse:
                b.add("spouse", c.spouse)
            b.add("tag", c.tag)
            keep.add(c.key, b)
        out = Block([("character_db", "=", keep)])
        self.write_block(("main_menu", "setup", "start", "05_characters.txt"), out,
                         "# Rewritten by save2mod: vanilla characters of replaced countries removed,\n"
                         "# converted CK3 rulers/consorts/heirs appended. Children after parents.")
        self.log(f"Characters: removed {len(self.removed_chars)} vanilla, added {len(self.w.characters)} from CK3")

    def _is_heir(self, key: str) -> bool:
        return any(c.heir == key for c in self.w.countries.values())

    def _dynasties(self) -> None:
        src = self.v.files.get("04_dynasties.txt")
        dm = Block()
        if src is not None:
            dm.items.extend(src.block("dynasty_manager").items)
        for key, d in sorted(self.w.dynasties.items()):
            b = Block()
            b.add("name", Block([("name", "=", key)]))
            if d.get("home"):
                b.add("home", d["home"])
            dm.add(key, b)
        self.write_block(("main_menu", "setup", "start", "04_dynasties.txt"), Block([("dynasty_manager", "=", dm)]))

    # ---------------------------------------------------------- countries
    def _countries_setup(self) -> None:
        w, eu5 = self.w, self.eu5
        covered = w.covered
        inner = Block()
        moved_caps = 0
        # Every vanilla country stays in the setup (EU5 wants all defined
        # countries there, each with a capital). CK3 land is taken out of their
        # lists; one that loses all its land becomes landless like vanilla's
        # releasables (capital kept, pointing into someone else's land).
        for tag, vc in self.v.countries.items():
            if tag in w.countries:
                continue
            if vc.block.str("type") == "pop":
                nb = Block([(k, op, self._filter_government(v) if k == "government" and isinstance(v, Block) else v)
                            for k, op, v in vc.block.items])
                inner.add(tag, nb)
                continue
            left = [l for l in vc.owned if l not in covered]
            nb = Block()
            for k, op, val in vc.block.items:
                if k in OWN_KEYS + CONTROL_KEYS + CORE_KEYS + ("add_pops_from_locations",) \
                        and isinstance(val, Block):
                    vals = [x for x in val.values() if isinstance(x, str) and x not in covered]
                    if vals or (not val.values()):
                        nb.add(k, Block([(None, None, x) for x in vals]), op)
                    continue
                if k == "capital" and isinstance(val, str) and val in covered and left:
                    val = self._best_capital(left)
                    moved_caps += 1
                if k == "government" and isinstance(val, Block):
                    val = self._filter_government(val)
                nb.items.append((k, op, val))
            inner.add(tag, nb)
        if moved_caps:
            self.log(f"Vanilla countries that lost their capital to CK3 land: {moved_caps} (capital moved to their "
                     f"best remaining location)")
        # converted countries
        for tag, c in sorted(w.countries.items(), key=lambda kv: kv[0]):
            if not c.locations:
                continue
            inner.add(tag, self._country_block(c))
        top = Block(list(self.v.top_of_10))
        top.add("countries", Block([("countries", "=", inner)]))
        self.write_block(("main_menu", "setup", "start", "10_countries.txt"), top)

    def _best_capital(self, locs: list[str]) -> str:
        rank = {"megalopolis": 3, "city": 2, "town": 1}
        return max(locs, key=lambda l: (rank.get(rank_of(self.v, l), 0),
                                         sum(p.num("size", 0.0) or 0.0 for p in self.v.pops.get(l, [])), l))

    def _filter_government(self, gov: Block) -> Block:
        out = Block()
        gone = {k for k, _op, v in gov.items
                if k in ("ruler", "consort", "heir", "active_regent") and isinstance(v, str) and v in self.removed_chars}
        drop = set(gone)
        if "active_regent" in gone:
            drop |= {"start_regency_date", "end_regency_date"}
        if "heir" in gone:
            drop.add("designated_heir_reason")
        for k, op, v in gov.items:
            if k in drop:
                continue
            if k == "ruler_term" and isinstance(v, Block):
                chars = [x for x in v.getall("character") if isinstance(x, str)]
                if any(ch in self.removed_chars for ch in chars):
                    continue
            out.items.append((k, op, v))
        return out

    def _country_block(self, c: Country) -> Block:
        eu5 = self.eu5
        b = Block()
        locs = [l for l in c.locations]
        # a reused tag keeps its vanilla land that lies outside CK3's map
        extra: dict[str, list[str]] = {}
        vc = self.v.countries.get(c.tag) if not c.new_tag else None
        if vc is not None:
            for k in OWN_KEYS:
                for lst in vc.block.getall(k):
                    if isinstance(lst, Block):
                        extra.setdefault(k, []).extend(x for x in lst.values()
                                                       if isinstance(x, str) and x not in self.w.covered)
        b.add("own_control_core", Block([(None, None, l) for l in locs + extra.pop("own_control_core", [])]))
        for k, vals in extra.items():
            if vals:
                b.add(k, Block([(None, None, l) for l in vals]))
        if c.capital:
            b.add("capital", c.capital)
        if c.tech is not None:
            b.add("starting_technology_level", str(c.tech))
        for inc in c.includes:
            b.add("include", QStr(inc))
        b.add("country_rank", c.rank)
        # new tags get their culture/religion from their country definition (like
        # vanilla); reused tags only override when CK3 differs from EU5's default
        vdef = eu5.country_defs.get(c.tag) if not c.new_tag else None
        if vdef is not None:
            if c.culture and c.culture != vdef.str("culture_definition"):
                b.add("culture", c.culture)
            if c.religion and c.religion != vdef.str("religion_definition"):
                b.add("religion", c.religion)
        if c.dynasty_house:
            dyn = f"ck3d_{c.dynasty_house}"
            if dyn in self.w.dynasties:
                b.add("dynasty", dyn)
        gov = Block()
        base = c.vanilla_block.get("government") if c.vanilla_block is not None else None
        keep_vanilla_gov = isinstance(base, Block) and bool(c.includes) and \
            self._vanilla_religion_matches(c) and self._vanilla_gov_type(c) == c.gov_type
        if keep_vanilla_gov:
            for k, op, v in base.items:
                if k in ("ruler", "consort", "heir", "active_regent", "designated_heir_reason", "type",
                         "heir_selection"):
                    continue
                if k == "ruler_term":
                    continue
                gov.items.append((k, op, v))
        gov.add("type", c.gov_type)
        if c.heir_selection:
            gov.add("heir_selection", c.heir_selection)
        rk = getattr(c, "ruler_key", None)
        gov.add("ruler", rk if rk else "random")
        if c.consort:
            gov.add("consort", c.consort)
        if c.heir and self.w.options.set_heirs:
            gov.add("heir", c.heir)
        b.add("government", gov)
        # keep a few harmless vanilla fields for reused tags
        if c.vanilla_block is not None:
            for k in ("ai_advance_preference_tags", "is_valid_for_release", "court_language"):
                v = c.vanilla_block.get(k)
                if v is not None:
                    b.add(k, v)
        return b

    def _vanilla_gov_type(self, c: Country) -> str:
        vb = c.vanilla_block
        gov = vb.get("government") if vb is not None else None
        if isinstance(gov, Block) and gov.str("type"):
            return gov.str("type")
        for inc in (vb.getall("include") if vb is not None else []):
            if not isinstance(inc, str) or inc.startswith("expl_"):
                continue
            tb = self.eu5_template(inc)
            t = tb.path("government", "type") if tb is not None else None
            if isinstance(t, str):
                return t
        return "monarchy"

    def eu5_template(self, name: str) -> Block | None:
        if not hasattr(self, "_tpl_cache"):
            self._tpl_cache = {}
        if name not in self._tpl_cache:
            fp = os.path.join(self.eu5.root, "main_menu", "setup", "templates", name + ".txt")
            try:
                self._tpl_cache[name] = parse_file(fp)
            except OSError:
                self._tpl_cache[name] = None
        return self._tpl_cache[name]

    def _vanilla_religion_matches(self, c: Country) -> bool:
        d = self.eu5.country_defs.get(c.tag)
        rel = d.str("religion_definition") if d is not None else None
        g = self.eu5.religion_group
        return bool(rel) and g.get(rel) == g.get(c.religion or "")

    def _country_definitions(self) -> None:
        out = Block()
        taken: set[tuple[int, int, int]] = set()
        for d in self.eu5.country_defs.values():
            col = d.get("color") if isinstance(d, Block) else None
            if isinstance(col, Tagged) and col.tag == "rgb":
                try:
                    taken.add(tuple(int(float(x)) for x in col.block.values()[:3]))  # type: ignore[arg-type]
                except (ValueError, TypeError):
                    pass
        for tag, c in sorted(self.w.countries.items()):
            if not c.new_tag or not c.locations:
                continue
            b = Block()
            c.color = _unique_color(tuple(int(x) for x in c.color), taken)
            r, g, bl = c.color
            b.add("color", Tagged("rgb", Block([(None, None, str(r)), (None, None, str(g)), (None, None, str(bl))])))
            r2, g2, b2 = _darker(c.color)
            b.add("color2", Tagged("rgb", Block([(None, None, str(r2)), (None, None, str(g2)), (None, None, str(b2))])))
            b.add("culture_definition", c.culture)
            b.add("religion_definition", c.religion)
            out.add(tag, b)
        self.write_block(("in_game", "setup", "countries", "zz_save2mod_countries.txt"), out, bom=True)

    # --------------------------------------------------------------- pops
    def _pops(self) -> None:
        w = self.w
        locs = Block()
        all_ck3 = w.options.all_pops_take_ck3
        for loc, pops in self.v.pops.items():
            lb = Block()
            if loc in w.covered:
                cul = w.loc_culture.get(loc)
                rel = w.loc_religion.get(loc)
                vmaj = self.v.majority_culture(loc)
                merged: dict[tuple[str, str, str], float] = {}
                order: list[tuple[str, str, str]] = []
                for p in pops:
                    t = p.str("type") or "peasants"
                    pc, pr = p.str("culture") or "", p.str("religion") or ""
                    size = p.num("size", 0.0) or 0.0
                    if all_ck3 or (pc, pr) == (vmaj[0] or "", vmaj[1] or ""):
                        pc = cul or pc
                        pr = rel or pr
                    k = (t, pc, pr)
                    if k not in merged:
                        order.append(k)
                        merged[k] = 0.0
                    merged[k] += size
                for k in order:
                    t, pc, pr = k
                    lb.add("define_pop", Block([("type", "=", t), ("size", "=", f"{merged[k]:.3f}"),
                                                ("culture", "=", pc), ("religion", "=", pr)]))
            else:
                for p in pops:
                    lb.add("define_pop", p)
            locs.add(loc, lb)
        self.write_block(("main_menu", "setup", "start", "06_pops.txt"), Block([("locations", "=", locs)]))

    # ---------------------------------------------------------- buildings
    def _cities_and_buildings(self) -> None:
        w, eu5 = self.w, self.eu5
        keep_vanilla = w.options.keep_vanilla_buildings
        loc_block = Block()
        per_loc: dict[str, dict[str, tuple[int, str]]] = {}   # loc -> building -> (level, tag)
        for loc, entry in self.v.city_entries.items():
            nb = Block()
            for k, op, v in entry.items:
                if k == "town_setup" and isinstance(v, str) and loc in w.covered:
                    if keep_vanilla:
                        ts = eu5.town_setups.get(v)
                        if ts is not None:
                            for bk, bv in ts.pairs():
                                try:
                                    lvl = int(float(bv))
                                except (TypeError, ValueError):
                                    continue
                                cur = per_loc.setdefault(loc, {}).get(bk)
                                per_loc[loc][bk] = (max(lvl, cur[0] if cur else 0), w.owner[loc])
                    continue
                nb.items.append((k, op, v))
            if nb.items:
                loc_block.add(loc, nb)
        manager = Block()
        for btype, bb in self.v.buildings:
            loc = bb.str("location")
            tag = bb.str("tag")
            if loc in w.covered:
                if not keep_vanilla:
                    continue
                bt = eu5.buildings.get(btype)
                owner = w.owner[loc]
                if bt is not None and bt.is_foreign and tag in self.alive_tags and tag not in w.reused_tags:
                    manager.add(btype, bb)            # foreign building of a surviving country
                    continue
                lvl = int(bb.num("level", 1) or 1)
                cur = per_loc.setdefault(loc, {}).get(btype)
                per_loc[loc][btype] = (max(lvl, cur[0] if cur else 0), owner)
            else:
                if tag and tag not in self.alive_tags:
                    owner = self._owner_of_uncovered(loc)
                    if not owner:
                        continue
                    bb = Block([(k, op, owner if k == "tag" else v) for k, op, v in bb.items])
                manager.add(btype, bb)
        # CK3 buildings
        for loc, blds in w.ck3_buildings.items():
            owner = w.owner[loc]
            for bk, lvl in blds.items():
                cur = per_loc.setdefault(loc, {}).get(bk)
                per_loc[loc][bk] = (max(lvl, cur[0] if cur else 0), owner)
        n = 0
        for loc in sorted(per_loc):
            for bk, (lvl, tag) in sorted(per_loc[loc].items()):
                if bk not in eu5.buildings:
                    continue
                manager.add(bk, Block([("tag", "=", tag), ("level", "=", str(lvl)), ("location", "=", loc)]))
                n += 1
        out = Block([("locations", "=", loc_block), ("building_manager", "=", manager)])
        self.write_block(("main_menu", "setup", "start", "07_cities_and_buildings.txt"), out)
        self.log(f"Buildings: {n} placed in converted land")

    def _owner_of_uncovered(self, loc: str) -> str | None:
        t = self.v.owner_of.get(loc)
        return t if t in self.alive_tags else None

    # --------------------------------------------------------- development
    def _development(self) -> None:
        src = self.v.files.get("14_development.txt")
        if src is None:
            return
        dev = src.block("development")
        eu5, w = self.eu5, self.w
        geo = Block()
        hist: dict[str, float] = {}          # unit -> value
        for k, op, v in dev.items:
            if k is None:
                continue
            if k in eu5.unit_level or k in eu5.locations:
                try:
                    hist[k] = float(v)
                except (TypeError, ValueError):
                    pass
            else:
                geo.items.append((k, op, v))
        covered = w.covered
        out = Block(list(geo.items))
        # keep untouched units whole; expand partially covered ones to locations
        per_loc: dict[str, float] = {}
        for unit, val in hist.items():
            locs = eu5.locations_in(unit)
            if not locs:
                continue
            if not any(l in covered for l in locs):
                out.add(unit, fmt_value(val))
                continue
            for l in locs:
                if l not in covered:
                    per_loc[l] = per_loc.get(l, 0.0) + val
        for l, val in sorted(per_loc.items()):
            if abs(val) > 1e-9:
                out.add(l, fmt_value(round(val, 3)))
        for l in sorted(covered):
            if l in w.loc_dev:
                out.add(l, fmt_value(w.loc_dev[l]))
            else:
                # no CK3 value: keep that location's vanilla historical part
                val = sum(v for u, v in hist.items() if l in self._units_of(l, u))
                if val:
                    out.add(l, fmt_value(round(val, 3)))
        self.write_block(("main_menu", "setup", "start", "14_development.txt"), Block([("development", "=", out)]),
                         "# save2mod: CK3 county development replaces EU5's regional/area history values;\n"
                         "# terrain, coast, river, road and rank modifiers are unchanged.")

    def _units_of(self, loc: str, unit: str) -> set[str]:
        li = self.eu5.locations.get(loc)
        if li is None:
            return set()
        return {loc} if unit in (li.key, li.province, li.area, li.region) else set()

    # --------------------------------------------------- location templates
    def _location_templates(self) -> None:
        src = os.path.join(self.eu5.root, "in_game", "map_data", "location_templates.txt")
        w = self.w
        if not os.path.exists(src):
            return
        out_lines: list[str] = []
        pat = re.compile(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*\{)(.*)\}(\s*(?:#.*)?)$")
        with open(src, encoding="utf-8-sig") as fh:
            for line in fh:
                m = pat.match(line.rstrip("\n"))
                if m and m.group(2) in w.covered:
                    body = m.group(4)
                    loc = m.group(2)
                    if loc in w.loc_culture:
                        body = re.sub(r"\bculture\s*=\s*\S+", f"culture = {w.loc_culture[loc]}", body)
                    if loc in w.loc_religion:
                        body = re.sub(r"\breligion\s*=\s*\S+", f"religion = {w.loc_religion[loc]}", body)
                    line = f"{m.group(1)}{loc}{m.group(3)}{body}}}{m.group(5)}\n"
                out_lines.append(line if line.endswith("\n") else line + "\n")
        with open(self.path("in_game", "map_data", "location_templates.txt"), "w", encoding="utf-8-sig",
                  newline="\n") as fh:
            fh.writelines(out_lines)

    # ----------------------------------------------------------- diplomacy
    def _tag_ok(self, t) -> bool:
        return isinstance(t, str) and t in self.alive_tags and t not in self.w.reused_tags

    def _diplomacy(self) -> None:
        w = self.w
        src = self.v.files.get("12_diplomacy.txt")
        dm = Block()
        if src is not None:
            for k, op, v in src.block("diplomacy_manager").items:
                if isinstance(v, Block):
                    tags = [v.str("first"), v.str("second")]
                    if not all(self._tag_ok(t) for t in tags if t):
                        continue
                dm.items.append((k, op, v))
        start = self.eu5.start_date
        for tag, c in sorted(w.countries.items()):
            if c.overlord and c.locations and w.countries.get(c.overlord) and w.countries[c.overlord].locations:
                dm.add("dependency", Block([("first", "=", c.overlord), ("second", "=", tag),
                                            ("subject_type", "=", w.options.subject_type),
                                            ("start_date", "=", _minus_years(start, 1))]))
        self.write_block(("main_menu", "setup", "start", "12_diplomacy.txt"), Block([("diplomacy_manager", "=", dm)]))
        for name in ("18_opinions.txt", "20_rivals.txt"):
            src = self.v.files.get(name)
            if src is None:
                continue
            dm2 = Block()
            for k, op, v in src.block("diplomacy_manager").items:
                if isinstance(v, Block):
                    tags = [v.str("first"), v.str("second")]
                    if not all(self._tag_ok(t) for t in tags if t):
                        continue
                dm2.items.append((k, op, v))
            self.write_block(("main_menu", "setup", "start", name), Block([("diplomacy_manager", "=", dm2)]))

    # ----------------------------------------------- international orgs
    def _ios(self) -> None:
        src = self.v.files.get("15_international_organizations.txt")
        if src is None:
            return
        mgr = Block()
        for k, op, io in src.block("international_organization_manager").items:
            if k != "add_international_organization" or not isinstance(io, Block):
                mgr.items.append((k, op, io))
                continue
            typ = io.str("type")
            if typ == "hre":
                if self.w.hre:
                    mgr.add(k, self._hre_block(io))
                continue
            leader = io.str("leader")
            members = [x for x in io.block("members").values() if isinstance(x, str)] if io.get("members") else []
            survivors = [m for m in members if self._tag_ok(m)]
            religious = typ in RELIGIOUS_IOS
            if religious:
                survivors = [m for m in members if isinstance(m, str) and m in self.alive_tags]
            if not religious and leader and not self._tag_ok(leader):
                self.w.report.append(f"IO dropped: {typ} (leader {leader} no longer exists in vanilla form)")
                continue
            if members and not survivors:
                self.w.report.append(f"IO dropped: {typ} (no members left)")
                continue
            nb = Block()
            for kk, oo, vv in io.items:
                if kk == "members":
                    nb.add("members", Block([(None, None, m) for m in survivors]))
                elif kk == "leader":
                    nb.add("leader", vv if vv in survivors or not survivors else survivors[0])
                elif isinstance(vv, Block) and vv.is_list() and vv.values() and \
                        all(isinstance(x, str) and re.fullmatch(r"[A-Z][A-Z0-9]{2}", x) for x in vv.values()):
                    keepv = [x for x in vv.values() if x in survivors]
                    if keepv:
                        nb.add(kk, Block([(None, None, x) for x in keepv]), oo)
                elif kk == "ruler_term" and isinstance(vv, Block) and \
                        any(ch in self.removed_chars for ch in vv.getall("character")):
                    continue
                else:
                    nb.items.append((kk, oo, vv))
            mgr.add(k, nb)
        self.write_block(("main_menu", "setup", "start", "15_international_organizations.txt"),
                         Block([("international_organization_manager", "=", mgr)]))

    def _hre_block(self, vanilla_io: Block) -> Block:
        hre = self.w.hre
        members = hre["members"]
        emperor = hre["emperor"]
        cs = self.w.countries
        theo = [t for t in members if t != emperor and cs[t].gov_type == "theocracy"]
        secular = [t for t in members if t != emperor and cs[t].gov_type != "theocracy"]
        size = lambda t: len(cs[t].locations)
        arch = sorted(theo, key=size, reverse=True)[:3]
        sec = sorted(secular, key=size, reverse=True)[:7 - len(arch)]
        b = Block()
        b.add("type", "hre")
        for k in ("creation_date", "map_color"):
            v = vanilla_io.get(k)
            if v is not None:
                b.add(k, v)
        b.add("members", Block([(None, None, t) for t in members]))
        b.add("leader", emperor)
        b.add("locations", Block([(None, None, l) for l in hre["locations"]]))
        b.add("emperor", Block([(None, None, emperor)]))
        if sec:
            b.add("elector", Block([(None, None, t) for t in sec]))
        if arch:
            b.add("archbishop_elector", Block([(None, None, t) for t in arch]))
        prelates = [t for t in theo]
        if prelates:
            b.add("imperial_prelate", Block([(None, None, t) for t in prelates]))
        cities = [t for t in members if t != emperor and cs[t].gov_type == "republic"]
        if cities:
            b.add("free_city", Block([(None, None, t) for t in cities]))
        princes = [t for t in members if t != emperor and t not in prelates and t not in cities]
        if princes:
            b.add("imperial_prince", Block([(None, None, t) for t in princes]))
        laws = vanilla_io.get("laws")
        if isinstance(laws, Block):
            erel = cs[emperor].religion
            vals = [x for x in laws.values() if isinstance(x, str)]
            if erel != "catholic":
                vals = [x for x in vals if not x.startswith("hre_religion_")]
            b.add("laws", Block([(None, None, x) for x in vals]))
        for rt in vanilla_io.getall("ruler_term"):
            if isinstance(rt, Block) and not any(ch in self.removed_chars for ch in rt.getall("character")):
                b.add("ruler_term", rt)
        return b

    # -------------------------------------------------- other setup files
    def _misc_setup(self) -> None:
        w = self.w
        # 02_core: saints of removed countries lose their country link
        src = self.v.files.get("02_core.txt")
        if src is not None:
            self.write_block(("main_menu", "setup", "start", "02_core.txt"), self._scrub(src))
        # 11_art: drop removed artists
        src = self.v.files.get("11_art.txt")
        if src is not None:
            self.write_block(("main_menu", "setup", "start", "11_art.txt"), self._scrub(src))
        # 13_religion: seats of cardinal etc.
        src = self.v.files.get("13_religion.txt")
        if src is not None:
            mgr = Block()
            for bt, bb in src.block("building_manager").pairs():
                if not isinstance(bb, Block):
                    continue
                loc = bb.str("location")
                tag = bb.str("tag")
                if loc in w.covered:
                    owner = w.owner[loc]
                    if bt == "seat_of_cardinal" and w.countries[owner].religion != "catholic":
                        continue
                    bb = Block([(k, op, owner if k == "tag" else v) for k, op, v in bb.items])
                elif tag and tag not in self.alive_tags:
                    continue
                mgr.add(bt, bb)
            self.write_block(("main_menu", "setup", "start", "13_religion.txt"), Block([("building_manager", "=", mgr)]))
        # 16_wars: only wars/truces fully between surviving vanilla countries
        src = self.v.files.get("16_wars.txt")
        if src is not None:
            mgr = Block()
            for k, op, wb in src.block("war_manager").items:
                if isinstance(wb, Block):
                    tags = self._tags_in(wb)
                    if not all(self._tag_ok(t) for t in tags):
                        continue
                mgr.items.append((k, op, wb))
            self.write_block(("main_menu", "setup", "start", "16_wars.txt"), Block([("war_manager", "=", mgr)]))
        # 23_colonies
        src = self.v.files.get("23_colonies.txt")
        if src is not None:
            mgr = Block()
            for k, op, cb in src.block("colony_manager").items:
                if isinstance(cb, Block) and cb.str("tag") and not self._tag_ok(cb.str("tag")):
                    continue
                mgr.items.append((k, op, cb))
            self.write_block(("main_menu", "setup", "start", "23_colonies.txt"), Block([("colony_manager", "=", mgr)]))
        # 25 / 26: per-tag country blocks
        for name in ("25_area_preferences.txt", "26_ai_personalities.txt"):
            src = self.v.files.get(name)
            if src is None:
                continue
            out = Block()
            for k, op, v in src.items:
                if k == "countries" and isinstance(v, Block):
                    inner = v.get("countries")
                    target = inner if isinstance(inner, Block) else v
                    kept = Block([(t, o2, b2) for t, o2, b2 in target.items if t is None or t in self.alive_tags])
                    v = Block([("countries", "=", kept)]) if isinstance(inner, Block) else kept
                out.items.append((k, op, v))
            self.write_block(("main_menu", "setup", "start", name), out)
        # 27_armies: armies of surviving vanilla countries on their own land only
        src = self.v.files.get("27_armies.txt")
        if src is not None:
            mgr = Block()
            for k, op, ab in src.block("unit_manager").items:
                if isinstance(ab, Block):
                    tag = ab.str("country")
                    loc = ab.str("location")
                    if tag and not self._tag_ok(tag):
                        continue
                    if loc and loc in w.covered:
                        continue
                mgr.items.append((k, op, ab))
            self.write_block(("main_menu", "setup", "start", "27_armies.txt"), Block([("unit_manager", "=", mgr)]))

    def _tags_in(self, b: Block) -> set[str]:
        out: set[str] = set()
        for k, _op, v in b.items:
            if isinstance(v, Block):
                out |= self._tags_in(v)
            elif k in ("attacker", "defender", "country", "first", "second", "tag", "leader") and isinstance(v, str):
                if re.fullmatch(r"[A-Z][A-Z0-9]{2}", v):
                    out.add(v)
        return out

    def _scrub(self, b: Block) -> Block:
        """Remove references to removed characters/countries (artist, character,
        country lines) recursively."""
        out = Block()
        for k, op, v in b.items:
            if isinstance(v, Block):
                v = self._scrub(v)
            elif k in ("artist", "character") and isinstance(v, str) and v in self.removed_chars:
                continue
            elif k == "country" and isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z0-9]{2}", v) \
                    and v not in self.alive_tags:
                continue
            out.items.append((k, op, v))
        return out

    # ------------------------------------------------------------ scripts
    def _on_actions(self) -> None:
        w = self.w
        lines = ["# Generated by save2mod", "on_game_start = {", "\ton_actions = { save2mod_on_game_start }", "}", "",
                 "save2mod_on_game_start = {", "\teffect = {"]
        n = 0
        if w.options.transfer_control:
            for loc in sorted(w.loc_control):
                if loc not in w.covered:
                    continue
                val = w.loc_control[loc]
                lines.append(f"\t\tlocation:{loc} ?= {{ change_control = {{ value = {val:.3f} subtract = local_control }} }}")
                n += 1
        lines += ["\t}", "}", ""]
        with open(self.path("in_game", "common", "on_action", "zz_save2mod_on_actions.txt"), "w",
                  encoding="utf-8-sig", newline="\n") as fh:
            fh.write("\n".join(lines))
        self.log(f"Control: {n} locations set from CK3 county control at game start")

    # --------------------------------------------------------------- COAs
    def _coas(self) -> None:
        conv = CoaConverter(self.eu5, self.ck3, self.log)
        out = Block()
        for tag, c in sorted(self.w.countries.items()):
            if not c.new_tag or not c.locations:
                continue
            src = self.sv.coats_of_arms.get(c.coa_id or "")
            out.add(tag, conv.convert(src, c.color))
        self.write_block(("main_menu", "common", "coat_of_arms", "coat_of_arms", "zz_save2mod_coa.txt"), out, bom=True)
        n = conv.write_textures(self.root)
        if n:
            self.log(f"Flags: copied {n} CK3 patterns/emblems that EU5 doesn't have into the mod")
        if conv.missing:
            top = sorted(conv.missing.items(), key=lambda kv: -kv[1])[:20]
            self.w.report.append("FLAGS: textures found in neither game (left out): " +
                                 ", ".join(f"{k} x{v}" for k, v in top))

    # --------------------------------------------------------- localization
    def _localization(self) -> None:
        entries = dict(self.w.loc_strings)
        for lang in LANGS:
            write_yml(self.path("main_menu", "localization", lang, f"zz_save2mod_l_{lang}.yml"), lang, entries)

    # --------------------------------------------------------------- report
    def _report(self) -> None:
        w = self.w
        lines = [f"save2mod {__version__} conversion report - {datetime.now():%Y-%m-%d %H:%M}",
                 f"CK3 save: {self.sv.path} (date {self.sv.date})", ""]
        for k, v in w.stats.items():
            lines.append(f"{k}: {v}")
        lines.append("")
        lines.append("Countries (tag, CK3 title, name shown in EU5, ruler, locations, overlord; "
                     "name source in brackets):")
        for tag, c in sorted(w.countries.items(), key=lambda kv: -len(kv[1].locations)):
            ch = self.sv.characters.get(c.ruler) if c.ruler else None
            shown = w.loc_strings.get(tag) or self.eu5.loc.get(tag) or c.name
            ruler = ch.first_name if ch else ("(EU5 picks)" if not c.ruler else "?")
            lines.append(f"  {tag}{'*' if c.new_tag else ' '} {c.title:<28} {shown:<24} "
                         f"{ruler:<14} {len(c.locations):>4}  {c.overlord or ''}"
                         f"{'  [HRE emperor]' if c.emperor else ('  [HRE]' if c.hre_member else '')}"
                         f"{'  [exclave of ' + c.exclave_of + ']' if c.exclave_of else ''}"
                         f"  [name: {c.name_source or 'CK3'}]")
        lines.append("  (* = new tag)")
        lines.append("")
        lines += w.report
        with open(self.path("save2mod_report.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- helpers
def _minus_years(d: str, n: int) -> str:
    t = date_tuple(d) or (1337, 4, 1)
    return f"{t[0] - n}.{t[1]}.{t[2]}"


def _unique_color(c: tuple, taken: set) -> tuple:
    """Nudge a colour until no other country definition uses it (EU5 logs
    'same color' otherwise)."""
    r, g, b = c
    step = 0
    while (r, g, b) in taken and step < 200:
        step += 1
        d = (step + 1) // 2 * (1 if step % 2 else -1)
        ch = step % 3
        r2, g2, b2 = r, g, b
        if ch == 0:
            r2 = min(255, max(0, r + d))
        elif ch == 1:
            g2 = min(255, max(0, g + d))
        else:
            b2 = min(255, max(0, b + d))
        if (r2, g2, b2) not in taken:
            r, g, b = r2, g2, b2
            break
    taken.add((r, g, b))
    return (r, g, b)


def _darker(c):
    return tuple(max(0, int(x * 0.6)) for x in c)
