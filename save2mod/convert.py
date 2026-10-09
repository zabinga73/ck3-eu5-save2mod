"""Turn a parsed CK3 save into an EU5 world description (countries, owners,
characters, pops, buildings, development, control)."""
from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass, field

from .ck3save import CK3Save, Character
from .adjacency import components
from .geomap import MapResult
from .loc import resolve
from .skills import SkillModel
from .mappings import (BuildingMapper, CultureMapper, ReligionMapper, norm, read_table)
from .pdx import Block
from .vanilla import VanillaSetup, rank_of, date_tuple

TIER_RANK = {0: "rank_county", 1: "rank_county", 2: "rank_duchy", 3: "rank_kingdom", 4: "rank_empire", 5: "rank_empire"}


@dataclass
class Options:
    keep_vanilla_buildings: bool = True
    all_pops_take_ck3: bool = True
    transfer_control: bool = True
    dev_multiplier: float = 1.0
    dev_offset: float = 0.0
    reuse_tags: bool = True
    rebuild_hre: bool = True
    hre_direct_counts_independent: bool = True
    subject_min_tier: int = 3          # kingdom (duchy over-shattered Byzantium and China)
    keep_admin_realms_whole: bool = True   # administrative/celestial realms don't split their governors off
    split_exclaves: bool = True        # detached parts of a country become vassals
    fill_enclaves: bool = True         # unmapped pockets surrounded by CK3 land join it
    enclave_max_size: int = 40         # largest pocket (EU5 locations) that is filled
    exclave_sea_hop: int = 2           # sea/lake/wasteland locations a crossing may pass and still be "connected"
    subject_type: str = "vassal"
    building_placement: str = "all"    # "all" covered locations | "main" (largest overlap)
    convert_characters: bool = True
    set_heirs: bool = False            # EU5 normally derives heirs itself; vanilla rarely sets them
    min_ruler_age: int = 16            # vanilla 1337 has no child rulers; younger CK3 rulers are aged up
    ck3_title_names: bool = False      # use the save's displayed title names (historical names, renames)
    own_names_for_tag_rules: bool = True   # switch off EU5's tag-specific naming (MAM -> Mamluks) for reused tags
    strip_tag_government: bool = False     # a reused MAM loses vanilla's Mamluk reform, laws and regnal numbers
    skill_bonuses: bool = False        # approximate CK3's on-screen skills (traits, spouse, holy sites...) for ADM/DIP/MIL
    mod_name: str = "CK3 Conversion"
    game_version: str = ""             # for .metadata; "" = auto-detect


@dataclass
class Country:
    tag: str
    ruler: str                       # CK3 character id
    title: str                       # CK3 primary title key (for naming)
    tier: int
    new_tag: bool = True
    locations: list[str] = field(default_factory=list)
    capital: str | None = None
    overlord: str | None = None      # tag
    culture: str | None = None
    religion: str | None = None
    rank: str = "rank_county"
    gov_type: str = "monarchy"
    heir_selection: str | None = None
    name: str = ""
    adj: str = ""
    color: tuple[int, int, int] = (128, 128, 128)
    includes: list[str] = field(default_factory=list)
    tech: int | None = None
    vanilla_block: Block | None = None
    hre_member: bool = False
    emperor: bool = False
    heir: str | None = None
    consort: str | None = None
    coa_id: str | None = None
    dynasty_house: str | None = None
    ruler_key: str | None = None
    exclave_of: str | None = None    # tag of the country this detached piece was split from
    name_source: str = ""            # where the displayed name comes from (for the report)


@dataclass
class EU5Character:
    key: str
    ck3_id: str
    first_name: str
    female: bool
    birth: str
    culture: str
    religion: str
    adm: int
    dip: int
    mil: int
    tag: str
    dynasty: str | None
    birth_loc: str | None
    spouse: str | None = None      # EU5 key
    estate: str = "nobles_estate"


@dataclass
class World:
    options: Options
    countries: dict[str, Country] = field(default_factory=dict)      # tag -> country
    owner: dict[str, str] = field(default_factory=dict)              # covered location -> tag
    loc_culture: dict[str, str] = field(default_factory=dict)        # covered location -> culture
    loc_religion: dict[str, str] = field(default_factory=dict)
    loc_dev: dict[str, float] = field(default_factory=dict)
    loc_control: dict[str, float] = field(default_factory=dict)      # 0..1
    ck3_buildings: dict[str, dict[str, int]] = field(default_factory=dict)   # loc -> {eu5 building: level}
    characters: dict[str, EU5Character] = field(default_factory=dict)
    dynasties: dict[str, dict] = field(default_factory=dict)          # key -> {name, home}
    removed_tags: set[str] = field(default_factory=set)
    reused_tags: set[str] = field(default_factory=set)
    loc_strings: dict[str, str] = field(default_factory=dict)         # localization key -> text
    hre: dict | None = None
    report: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    covered: set[str] = field(default_factory=set)


# ------------------------------------------------------------------ helpers
def _date_add_years(d: str, years: float) -> str:
    t = date_tuple(d) or (1337, 4, 1)
    y = int(math.floor(years))
    return f"{t[0] + y}.{t[1]}.{t[2]}"


def _age_on(birth: str | None, date: str) -> float | None:
    b = date_tuple(birth)
    d = date_tuple(date)
    if not b or not d:
        return None
    return (d[0] - b[0]) + (d[1] - b[1]) / 12.0 + (d[2] - b[2]) / 365.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _darker(c: tuple[int, int, int]) -> tuple[int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in c))
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, l * 0.6), s)
    return int(r * 255), int(g * 255), int(b * 255)


def _lighter(c: tuple[int, int, int]) -> tuple[int, int, int]:
    h, l, s = colorsys.rgb_to_hls(*(x / 255 for x in c))
    r, g, b = colorsys.hls_to_rgb(h, min(0.85, l + (1 - l) * 0.35), s)
    return int(r * 255), int(g * 255), int(b * 255)


def _clean_loc(s: str) -> str:
    s = re.sub(r"\$[^$]*\$", "", s)
    return s.strip() or s


def _pretty(key: str) -> str:
    k = re.sub(r"^(dynn|dynnp|name)_", "", key)
    return k.replace("_", " ").strip().title() if k else key


RESERVED_TAGS = {"YES", "NOT", "AND", "NOR", "XOR", "ANY", "ALL", "NUL", "CON", "PRN", "AUX", "COM", "LPT",
                 "NAN", "INF", "NEW", "OLD", "TAG", "REB", "PIR", "MER", "NAT"}


class TagFactory:
    def __init__(self, used: set[str]):
        self.used = set(used) | RESERVED_TAGS
        self._i = 0
        self._prefixes = "ZYXQWVJ"

    def new(self, hint: str = "") -> str:
        letters = re.sub(r"[^A-Z]", "", hint.upper())
        # try something readable from the name first: Z + 2 letters of the name
        for p in self._prefixes:
            if len(letters) >= 2:
                cand = p + letters[0] + letters[-1]
                if cand not in self.used:
                    self.used.add(cand)
                    return cand
        while True:
            p = self._prefixes[(self._i // 676) % len(self._prefixes)]
            n = self._i % 676
            cand = p + chr(65 + n // 26) + chr(65 + n % 26)
            self._i += 1
            if cand not in self.used:
                self.used.add(cand)
                return cand


# ============================================================== converter
class Converter:
    def __init__(self, save: CK3Save, ck3, eu5, mapres: MapResult, vanilla: VanillaSetup,
                 options: Options | None = None, log=print, adjacency: dict[str, set[str]] | None = None):
        self.adj = adjacency
        self.sv = save
        self.ck3 = ck3
        self.eu5 = eu5
        self.map = mapres
        self.v = vanilla
        self.o = options or Options()
        self.log = log
        self.cm = CultureMapper(ck3, eu5, log)
        self.rm = ReligionMapper(ck3, eu5, log)
        self.bm = BuildingMapper(eu5)
        self.w = World(options=self.o)
        # realm list: typed names/adjectives override the country's name in EU5
        self.name_over: dict[str, tuple[str, str]] = {}
        for r in read_table("title_tags.csv"):
            if r.get("ck3_title") and (r.get("name") or r.get("adjective")):
                self.name_over[r["ck3_title"]] = (r.get("name", ""), r.get("adjective", ""))
        if self.o.ck3_title_names:
            self._use_displayed_names()
        for p in self.cm.problems + self.rm.problems + self.bm.problems:
            self.w.report.append("TABLE: " + p)
            if "Reset to defaults" in p:
                log("NOTE: " + p)

    def _use_displayed_names(self) -> None:
        """A title whose displayed name in the save differs from CK3's default
        for it (a historical name like West Francia, or a rename) is named by
        it, like a renamed title; the others keep their default name."""
        n = 0
        for t in self.sv.titles.values():
            if t.name or not t.shown_name:
                continue
            default = _clean_loc(resolve(self.ck3.loc, t.key) or "")
            if norm(t.shown_name) != norm(default):
                t.name = t.shown_name
                t.adj = t.adj or t.shown_adj
                n += 1
        self.log(f"Titles named as CK3 shows them: {n}")

    # --------------------------------------------------------------- run
    def run(self) -> World:
        if self.o.fill_enclaves:
            self._fill_enclaves()
        self._assign_realms()
        self._assign_tags()
        self._drop_landless()
        self._location_data()
        self._country_details()
        if self.o.convert_characters:
            self._characters()
        if self.o.split_exclaves:
            self._split_exclaves()
        self._hre()
        self._vanilla_survivors()
        self._check_religion_dates()
        self.w.stats.update({
            "countries": len(self.w.countries),
            "new_tags": sum(1 for c in self.w.countries.values() if c.new_tag),
            "reused_tags": len(self.w.reused_tags),
            "covered_locations": len(self.w.covered),
            "vanilla_countries_made_landless": len(self.w.removed_tags),
            "characters": len(self.w.characters),
            "exclave_vassals": sum(1 for c in self.w.countries.values() if c.exclave_of),
        })
        return self.w

    # ------------------------------------------------------ realm structure
    def _char_liege(self, cid: str) -> str | None:
        """Liege of a landed character: holder of the de facto liege title of
        the character's primary title (skipping titles the character holds)."""
        if cid in self._liege_cache:
            return self._liege_cache[cid]
        ch = self.sv.characters.get(cid)
        res = None
        t = self.sv.titles.get(ch.primary_title) if ch is not None and ch.primary_title else None
        seen: set[str] = set()
        while t is not None and t.id not in seen:
            seen.add(t.id)
            nxt = self.sv.titles.get(t.de_facto_liege) if t.de_facto_liege else None
            if nxt is None:
                break
            if nxt.holder and nxt.holder != cid:
                res = nxt.holder
                break
            t = nxt
        self._liege_cache[cid] = res
        return res

    def _holder_chain(self, county_key: str) -> list[str]:
        t = self.sv.title_by_key.get(county_key)
        if t is None or not t.holder:
            return []
        chain = [t.holder]
        # the county's own liege link wins for its holder when the holder has
        # no primary-title liege information
        while True:
            nxt = self._char_liege(chain[-1])
            if nxt is None and len(chain) == 1 and t.de_facto_liege:
                lt = self.sv.titles.get(t.de_facto_liege)
                nxt = lt.holder if lt is not None and lt.holder != chain[-1] else None
            if nxt is None or nxt in chain:
                break
            chain.append(nxt)
        return chain

    def _tier_of_char(self, cid: str) -> int:
        ch = self.sv.characters.get(cid)
        if ch is None or not ch.primary_title:
            return 1
        t = self.sv.titles.get(ch.primary_title)
        return t.tier if t else 1

    def _assign_realms(self) -> None:
        sv, o = self.sv, self.o
        # HRE
        self.emperor = None
        hre = sv.title_by_key.get("e_hre")
        if o.rebuild_hre and hre is not None and hre.holder and hre.holder in sv.characters:
            self.emperor = hre.holder
            self.log(f"Holy Roman Empire found (emperor: {sv.characters[hre.holder].first_name}); rebuilding it")
        self.liege: dict[str, str | None] = {}
        self._liege_cache: dict[str, str | None] = {}
        county_chain: dict[str, list[str]] = {}
        for loc, bar in self.map.loc_to_barony.items():
            cty = self.ck3.county_of_barony(bar)
            if not cty or cty in county_chain:
                continue
            chain = self._holder_chain(cty)
            if not chain:
                continue
            county_chain[cty] = chain
            for i, h in enumerate(chain):
                parent = chain[i + 1] if i + 1 < len(chain) else None
                if self.liege.get(h) is None:
                    self.liege[h] = parent

        def is_country(h: str) -> bool:
            p = self.liege.get(h)
            if p is None:
                return True
            if self.emperor and p == self.emperor and o.hre_direct_counts_independent:
                return True
            # governors of an administrative realm (Byzantine themes, AUH China)
            # stay part of it, whatever their tier
            if o.keep_admin_realms_whole and self._is_admin(h) and self._is_admin(p):
                return False
            return self._tier_of_char(h) >= o.subject_min_tier

        self.is_country = is_country
        self.county_owner: dict[str, str] = {}
        for cty, chain in county_chain.items():
            for h in chain:
                if is_country(h):
                    self.county_owner[cty] = h
                    break
        # overlord (character) for each country
        self.overlord_char: dict[str, str | None] = {}
        for h in set(self.county_owner.values()):
            p = self.liege.get(h)
            if self.emperor and p == self.emperor:
                self.overlord_char[h] = None       # HRE member, independent
                continue
            while p is not None and not is_country(p):
                p = self.liege.get(p)
            self.overlord_char[h] = p
        # countries needed also for overlords that hold no counties themselves
        for h, p in list(self.overlord_char.items()):
            while p is not None and p not in self.overlord_char:
                pp = self.liege.get(p)
                while pp is not None and not is_country(pp):
                    pp = self.liege.get(pp)
                self.overlord_char[p] = None if (self.emperor and pp == self.emperor) else pp
                p = pp
        self.log(f"CK3 realms -> {len(self.overlord_char)} EU5 countries "
                 f"({sum(1 for v in self.overlord_char.values() if v is None)} independent)")

    # ------------------------------------------------------------- tags
    def _naming_title(self, cid: str) -> str | None:
        ch = self.sv.characters.get(cid)
        if ch is None:
            return None
        if cid == self.emperor:
            held = [t for t in self.sv.titles.values() if t.holder == cid and t.key != "e_hre" and t.tier >= 1]
            if held:
                # highest title; among equals the one holding his capital, then
                # the one holding most of his own counties
                cap = self._capital_chain(ch)
                own = [t.key for t in held if t.tier == 1]
                inside = {t.key: sum(1 for k in own if t.key in self._chain(k)) for t in held}
                return max(held, key=lambda t: (t.tier, t.key in cap, inside[t.key], t.id)).id
        return ch.primary_title

    def _capital_chain(self, ch: Character) -> set[str]:
        """De jure titles (county upwards) above the character's realm capital."""
        rc = ch.raw.path("landed_data", "realm_capital") if ch.raw is not None else None
        t = self.sv.titles.get(rc) if isinstance(rc, str) else None
        key = t.key if t is not None else (self.ck3.barony_of_province.get(int(rc))
                                           if isinstance(rc, str) and rc.isdigit() else None)
        return self._chain(key) if key else set()

    def _chain(self, key: str) -> set[str]:
        """The title and its de jure lieges."""
        out: set[str] = set()
        gt = self.ck3.titles.get(key)
        while gt is not None and gt.key not in out:
            out.add(gt.key)
            gt = self.ck3.titles.get(gt.parent) if gt.parent else None
        return out

    def _assign_tags(self) -> None:
        sv, eu5 = self.sv, self.eu5
        # locations per country character (via counties)
        self.char_locs: dict[str, list[str]] = {}
        for loc, bar in self.map.loc_to_barony.items():
            cty = self.ck3.county_of_barony(bar)
            h = self.county_owner.get(cty or "")
            if h:
                self.char_locs.setdefault(h, []).append(loc)
        # manual + name candidates
        manual = {r["ck3_title"]: r.get("eu5_tag", "") for r in read_table("title_tags.csv")
                  if r.get("ck3_title") and r.get("eu5_tag")}
        by_name: dict[str, list[str]] = {}
        for tag in eu5.country_defs:
            n = eu5.loc.get(tag)
            if n:
                by_name.setdefault(norm(n), []).append(tag)
        used: set[str] = set()
        mapped = set(self.map.loc_to_barony)
        chars = sorted(self.overlord_char, key=lambda c: (-len(self.char_locs.get(c, [])), c))
        tf = TagFactory(set(eu5.country_defs))
        self.tf = tf
        self.tag_of_char: dict[str, str] = {}
        for cid in chars:
            tid = self._naming_title(cid)
            title = sv.titles.get(tid) if tid else None
            tkey = title.key if title else f"char_{cid}"
            tag = None
            if self.o.reuse_tags and title is not None:
                cands: list[str] = []
                m = manual.get(tkey)
                if m == "-":
                    cands = []
                elif m and m in eu5.country_defs:
                    cands = [m]
                else:
                    names = {resolve(self.ck3.loc, tkey) or "", title.name or ""}
                    for n in names:
                        if n:
                            cands += by_name.get(norm(n), [])
                cands = [c for c in dict.fromkeys(cands) if c not in used and
                         (eu5.country_defs.get(c) is None or eu5.country_defs[c].str("is_historic") != "yes")]
                mine = set(self.char_locs.get(cid, []))

                def overlap(tg):
                    vc = self.v.countries.get(tg)
                    return len(mine & set(vc.owned)) if vc else 0

                def outside(tg):
                    vc = self.v.countries.get(tg)
                    return [l for l in vc.owned if l not in mapped] if vc else []
                # a namesake far away from the vanilla country's land that CK3
                # doesn't cover would split that country in two: keep it vanilla
                if not m:
                    cands = [c for c in cands if overlap(c) > 0 or not outside(c)]
                if cands:
                    tag = max(cands, key=lambda tg: (overlap(tg), tg in self.v.countries))
            if tag is None:
                nm = (title.name if title and title.name else self.ck3.loc.get(tkey, tkey.split("_", 1)[-1]))
                tag = tf.new(nm)
                new = True
            else:
                new = False
                self.w.reused_tags.add(tag)
            used.add(tag)
            tf.used.add(tag)
            self.tag_of_char[cid] = tag
            c = Country(tag=tag, ruler=cid, title=tkey, tier=title.tier if title else 1, new_tag=new)
            c.locations = sorted(self.char_locs.get(cid, []))
            c.coa_id = title.coa_id if title else None
            if title is not None:
                c.color = title.color or self._game_title_color(tkey)
            self.w.countries[tag] = c
        for cid, tag in self.tag_of_char.items():
            p = self.overlord_char.get(cid)
            self.w.countries[tag].overlord = self.tag_of_char.get(p) if p else None
        self.log(f"Tags: {len(self.w.reused_tags)} reused EU5 tags, "
                 f"{len(self.w.countries) - len(self.w.reused_tags)} new")

    def _drop_landless(self) -> None:
        """Countries whose ruler holds no converted land (e.g. an overlord whose
        own counties all lie outside EU5's matched area) are dropped; their
        subjects move up to the next overlord that exists."""
        w = self.w
        dead = {t for t, c in w.countries.items() if not c.locations}
        if not dead:
            return
        for t, c in w.countries.items():
            o = c.overlord
            while o in dead:
                o = w.countries[o].overlord
            c.overlord = o
        for t in dead:
            c = w.countries.pop(t)
            w.reused_tags.discard(t)
            self.tag_of_char.pop(c.ruler, None)
        if self.emperor and self.emperor not in self.tag_of_char:
            self.log("HRE emperor holds no converted land; the HRE is not rebuilt")
            self.emperor = None
        self.log(f"Dropped {len(dead)} realms without converted land")

    def _game_title_color(self, key: str) -> tuple[int, int, int]:
        t = self.ck3.titles.get(key)
        if t and t.color:
            return t.color
        h = (hash(key) % 360) / 360.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.8)
        return int(r * 255), int(g * 255), int(b * 255)

    # ------------------------------------------------------- per location
    def _county_culture_faith(self, cty: str, bar: str):
        c = self.sv.counties.get(cty)
        cul = c.culture if c else None
        fai = c.faith if c else None
        if (cul is None or fai is None):
            t = self.ck3.titles.get(bar)
            prov = str(t.province) if t and t.province is not None else None
            # capital barony of the county carries culture/faith in some versions
            cap_bar = None
            ct = self.ck3.titles.get(cty)
            if ct and ct.children:
                cap_t = self.ck3.titles.get(ct.children[0])
                cap_bar = str(cap_t.province) if cap_t and cap_t.province is not None else None
            for p in (cap_bar, prov):
                b = self.sv.baronies.get(p) if p else None
                if b:
                    cul = cul or b.culture
                    fai = fai or b.faith
        if cul is None or fai is None:
            st = self.sv.title_by_key.get(cty)
            h = self.sv.characters.get(st.holder) if st and st.holder else None
            if h:
                cul = cul or h.culture
                fai = fai or h.faith
        return cul, fai

    def _location_data(self) -> None:
        sv, o, w = self.sv, self.o, self.w
        loc_owner_tag: dict[str, str] = {}
        for tag, c in w.countries.items():
            for loc in c.locations:
                loc_owner_tag[loc] = tag
        w.owner = loc_owner_tag
        w.covered = set(loc_owner_tag)
        unmapped_cult: set[str] = set()
        unmapped_faith: set[str] = set()
        skipped_buildings: dict[str, int] = {}
        fort_cands: dict[str, dict[str, list[str]]] = {}     # county -> barony -> CK3 fort buildings
        # main location per barony for "main" placement
        main_of_bar: dict[str, str] = {}
        for bar, locs in self.map.barony_to_locs.items():
            main_of_bar[bar] = max(locs, key=lambda l: self.map.loc_share.get(l, 0))
        for loc in w.covered:
            bar = self.map.loc_to_barony[loc]
            cty = self.ck3.county_of_barony(bar) or ""
            cul_id, fai_id = self._county_culture_faith(cty, bar)
            vcul, vrel = self.v.majority_culture(loc)
            rule = self.cm.rule_for_save_culture(sv, cul_id)
            cul = self.cm.resolve(rule, vcul) if rule else None
            if cul is None:
                if cul_id:
                    unmapped_cult.add(cul_id)
                cul = vcul or self.eu5.locations[loc].culture
            rel = self.rm.for_save_faith(sv, fai_id)
            if rel is None:
                if fai_id:
                    unmapped_faith.add(fai_id)
                rel = vrel or self.eu5.locations[loc].religion
            if cul:
                w.loc_culture[loc] = cul
            if rel:
                w.loc_religion[loc] = rel
            cd = sv.counties.get(cty)
            if cd is not None and cd.development is not None:
                w.loc_dev[loc] = round(cd.development * o.dev_multiplier + o.dev_offset, 2)
            if cd is not None and cd.control is not None:
                w.loc_control[loc] = round(_clamp(cd.control / 100.0, 0.0, 1.0), 3)
            # buildings
            if o.building_placement == "main" and main_of_bar.get(bar) != loc:
                continue
            t = self.ck3.titles.get(bar)
            b = sv.baronies.get(str(t.province)) if t and t.province is not None else None
            if b is None:
                continue
            rank = rank_of(self.v, loc)
            out: dict[str, int] = {}
            for bk in b.buildings:
                if self.bm.is_fort(bk):
                    # forts are placed once per county below
                    cand = fort_cands.setdefault(cty, {})
                    if bk not in cand.get(bar, ()):
                        cand.setdefault(bar, []).append(bk)
                    continue
                conv = self.bm.convert(bk, rank)
                if conv is None:
                    if self.bm.rule_for(bk) is None:
                        skipped_buildings[bk] = skipped_buildings.get(bk, 0) + 1
                    continue
                name, lvl = conv
                if not self._potential_ok(name, loc):
                    continue
                out[name] = max(out.get(name, 0), lvl)
            if out:
                w.ck3_buildings[loc] = out
        n_forts = self._place_forts(fort_cands, main_of_bar)
        if fort_cands:
            self.log(f"Forts: {n_forts} placed (at most one per CK3 county, from "
                     f"{sum(len(v) for v in fort_cands.values())} fortified baronies)")
        for cid in sorted(unmapped_cult):
            cu = sv.cultures.get(cid)
            w.report.append(f"CULTURE: no EU5 match for CK3 culture {cid} "
                            f"({cu.template or cu.name if cu else '?'}); kept vanilla culture there")
        for fid in sorted(unmapped_faith):
            f = sv.faiths.get(fid)
            w.report.append(f"RELIGION: no EU5 match for CK3 faith {fid} ({(f.rite or f.tag) if f else '?'}); kept vanilla religion")
        if skipped_buildings:
            top = sorted(skipped_buildings.items(), key=lambda kv: -kv[1])[:40]
            w.report.append("BUILDINGS without a mapping (skipped): " +
                            ", ".join(f"{k} x{v}" for k, v in top))

    def _place_forts(self, cands: dict[str, dict[str, list[str]]], main_of_bar: dict[str, str]) -> int:
        """One fort per CK3 county at most: the strongest fortification that
        clears its rule's minimum CK3 level, in the main EU5 location of the
        barony that has it."""
        w = self.w
        n = 0
        for cty in sorted(cands):
            options: list[tuple[tuple[int, int], str, str]] = []
            for bar, blds in cands[cty].items():
                for bk in blds:
                    options.append((self.bm.fort_strength(bk), bar, bk))
            options.sort(key=lambda t: (t[0], t[1]), reverse=True)
            for _st, bar, bk in options:
                locs = [l for l in self.map.barony_to_locs.get(bar, []) if l in w.covered]
                if not locs:
                    continue
                loc = main_of_bar.get(bar) if main_of_bar.get(bar) in locs else \
                    max(locs, key=lambda l: self.map.loc_share.get(l, 0))
                conv = self.bm.convert(bk, rank_of(self.v, loc))
                if conv is None or not self._potential_ok(conv[0], loc):
                    continue
                name, lvl = conv
                cur = w.ck3_buildings.setdefault(loc, {})
                cur[name] = max(cur.get(name, 0), lvl)
                n += 1
                break
        return n

    def _potential_ok(self, building: str, loc: str) -> bool:
        bt = self.eu5.buildings.get(building)
        if bt is None or bt.location_potential is None:
            return True
        return _eval_potential(bt.location_potential, self.eu5.locations[loc], self.eu5)

    # -------------------------------------------------------- countries
    def _country_details(self) -> None:
        sv, w, eu5 = self.sv, self.w, self.eu5
        for tag, c in w.countries.items():
            ch = sv.characters.get(c.ruler)
            title = sv.title_by_key.get(c.title)
            # culture & religion: ruler's, else the most common among its locations
            rule = self.cm.rule_for_save_culture(sv, ch.culture if ch else None)
            locs = c.locations
            cap = self._capital(c, ch)
            c.capital = cap
            vcul = self.v.majority_culture(cap)[0] if cap else None
            c.culture = (self.cm.resolve(rule, vcul) if rule else None) or self._most_common(w.loc_culture, locs)
            c.religion = self.rm.for_save_faith(sv, ch.faith if ch else None) or self._most_common(w.loc_religion, locs)
            c.culture = c.culture or (eu5.locations[cap].culture if cap else None) or "french"
            c.religion = c.religion or (eu5.locations[cap].religion if cap else None) or "catholic"
            c.rank = TIER_RANK.get(c.tier, "rank_county")
            if c.rank not in eu5.country_ranks and eu5.country_ranks:
                c.rank = eu5.country_ranks[-1]
            c.gov_type = self._gov_type(ch)
            c.heir_selection = self._heir_selection(ch, c.gov_type)
            if ch is not None and ch.female and c.heir_selection == "salic_law":
                c.heir_selection = "cognatic_primogeniture"
            # names
            nm = (title.name if title and title.name else None) or resolve(self.ck3.loc, c.title) \
                or _pretty(c.title.split("_", 1)[-1])
            adj = (title.adj if title and title.adj else None) or resolve(self.ck3.loc, c.title + "_adj") or nm
            nm, adj = _clean_loc(nm), _clean_loc(adj)
            c.name, c.adj = nm, adj
            if not c.new_tag and title is not None and title.name:
                w.loc_strings[tag] = title.name          # player-renamed title keeps its name
                if title.adj:
                    w.loc_strings[tag + "_ADJ"] = title.adj
                c.name_source = f"the save's name for {c.title}"
            elif not c.new_tag:
                c.name_source = f"EU5's own name for {tag}"
            if c.new_tag:
                w.loc_strings[tag] = nm
                w.loc_strings[tag + "_ADJ"] = adj
                c.name_source = f"CK3 title {c.title}"
            self._apply_name_override(c)
            # templates / tech
            c.vanilla_block = self.v.countries[tag].block if tag in self.v.countries else None
            geo_donor = self._geo_donor(locs)
            coastal = any(l in eu5.coastal for l in locs)
            c.includes, c.tech = self._templates(c, geo_donor, coastal)
            c.dynasty_house = ch.house if ch else None

    def _apply_name_override(self, c: Country) -> None:
        ov = self.name_over.get(c.title)
        if not ov:
            return
        name, adj = ov
        if name:
            c.name = name
            self.w.loc_strings[c.tag] = name
            c.name_source = "your realm list"
        if adj:
            c.adj = adj
            self.w.loc_strings[c.tag + "_ADJ"] = adj

    def shown_name(self, c: Country) -> str:
        """The name EU5 will display for a country."""
        return self.w.loc_strings.get(c.tag) or self.eu5.loc.get(c.tag) or c.name

    def _most_common(self, d: dict[str, str], locs: list[str]) -> str | None:
        cnt: dict[str, int] = {}
        for l in locs:
            v = d.get(l)
            if v:
                cnt[v] = cnt.get(v, 0) + 1
        return max(cnt, key=cnt.get) if cnt else None

    def _capital(self, c: Country, ch: Character | None) -> str | None:
        if not c.locations:
            return None
        mine = set(c.locations)
        cands: list[str] = []
        if ch is not None and ch.raw is not None:
            ld = ch.raw.block("landed_data")
            rc = ld.str("realm_capital")
            if rc:
                # realm_capital is a county title id or a province id depending on version
                t = self.sv.titles.get(rc)
                bars: list[str] = []
                if t is not None and t.tier == 0:
                    bars = [t.key]                      # 1.19: capital barony title
                elif t is not None:
                    ct = self.ck3.titles.get(t.key)
                    bars = [b for b in (ct.children if ct else []) if b.startswith("b_")]
                else:
                    b = self.ck3.barony_of_province.get(int(rc)) if rc.isdigit() else None
                    bars = [b] if b else []
                for b in bars:
                    cands += [l for l in self.map.barony_to_locs.get(b, []) if l in mine]
                    if cands:
                        break
        if not cands:
            # the ruler's primary title capital county
            pt = self.sv.titles.get(ch.primary_title) if ch and ch.primary_title else None
            cap_cty = None
            if pt is not None:
                gt = self.ck3.titles.get(pt.key)
                cap_cty = gt.capital if gt else None
            if cap_cty and cap_cty in self.ck3.titles:
                for b in self.ck3.titles[cap_cty].children:
                    cands += [l for l in self.map.barony_to_locs.get(b, []) if l in mine]
        if cands:
            ranked = sorted(cands, key=lambda l: (self._rank_score(l), self.map.loc_share.get(l, 0)), reverse=True)
            return ranked[0]
        return max(c.locations, key=lambda l: (self._rank_score(l), self.w.loc_dev.get(l, 0)))

    def _rank_score(self, loc: str) -> int:
        return {"megalopolis": 3, "city": 2, "town": 1}.get(rank_of(self.v, loc), 0)

    @staticmethod
    def _gov_str(ch: Character | None) -> str:
        g = (ch.government or "") if ch else ""
        if not g and ch is not None and ch.raw is not None:
            g = ch.raw.path("landed_data", "government") or ""
        return str(g)

    def _is_admin(self, cid: str | None) -> bool:
        g = self._gov_str(self.sv.characters.get(cid) if cid else None)
        return "administrative" in g or "celestial" in g or "steppe_admin" in g

    def _gov_type(self, ch: Character | None) -> str:
        g = self._gov_str(ch)
        if "republic" in g:
            t = "republic"
        elif "theocra" in g or "holy_order" in g or "ecclesiastical" in g:
            t = "theocracy"
        elif "tribal" in g:
            t = "tribe"
        elif "nomad" in g or "herder" in g or "steppe" in g:
            t = "steppe_horde"
        else:
            t = "monarchy"
        return t if t in self.eu5.government_types or not self.eu5.government_types else "monarchy"

    def _heir_selection(self, ch: Character | None, gov: str) -> str | None:
        if gov != "monarchy" or ch is None or ch.raw is None:
            return None
        laws: list[str] = []

        def walk(b):
            for k, _op, v in b.items:
                if isinstance(v, Block):
                    walk(v)
                elif isinstance(v, str) and v.endswith("_law"):
                    laws.append(v)
        walk(ch.raw.block("landed_data"))
        ls = " ".join(laws)
        hs = None
        if "partition" in ls:
            hs = "partition_inheritance"
        elif "tanistry" in ls:
            hs = "tanistry_elective"
        elif "elective" in ls or "elector" in ls:
            hs = "elective_succession"
        elif "primogeniture" in ls or "single_heir" in ls:
            if "male_only" in ls:
                hs = "salic_law"
            elif "equal_law" in ls or "female" in ls:
                hs = "absolute_cognatic_primogeniture"
            else:
                hs = "cognatic_primogeniture"
        if hs and hs not in self.eu5.heir_selections:
            return None
        return hs

    def _geo_donor(self, locs: list[str]) -> str | None:
        cnt: dict[str, int] = {}
        for l in locs:
            t = self.v.owner_of.get(l)
            if t:
                cnt[t] = cnt.get(t, 0) + 1
        return max(cnt, key=cnt.get) if cnt else None

    def _templates(self, c: Country, donor: str | None, coastal: bool) -> tuple[list[str], int | None]:
        eu5 = self.eu5
        grp = eu5.religion_group.get(c.religion or "", "")
        expl: list[str] = []
        tech = None
        base = None
        if donor and donor in self.v.countries:
            dv = self.v.countries[donor]
            expl = [i for i in dv.includes if i.startswith("expl_")]
            tech = dv.tech
            drel = eu5.country_defs.get(donor)
            drel_key = drel.str("religion_definition") if drel is not None else None
            if drel_key and eu5.religion_group.get(drel_key) == grp and eu5.religion_group.get(drel_key):
                gov_inc = [i for i in dv.includes if not i.startswith("expl_")]
                gov_tpl = gov_inc[0] if gov_inc else None
                if gov_tpl and self._template_gov(gov_tpl) == c.gov_type:
                    base = gov_tpl
        if base is None:
            base = self._rule_template(c, grp, coastal)
        inc = [base] + [e for e in expl if e != base]
        inc = [i for i in inc if i in eu5.templates]
        return inc, tech

    def _template_gov(self, tpl: str) -> str:
        if any(x in tpl for x in ("republic", "free_city", "hansa")):
            return "republic"
        if any(x in tpl for x in ("bishopric", "theocracy", "abbey", "military_order")):
            return "theocracy"
        if "horde" in tpl:
            return "steppe_horde"
        if "tribe" in tpl or "tribal" in tpl:
            return "tribe"
        return "monarchy"

    def _rule_template(self, c: Country, grp: str, coastal: bool) -> str:
        nc = "" if coastal else "_no_coast"
        rel = c.religion or ""
        gov = c.gov_type
        T = self.eu5.templates

        def pick(*names):
            for n in names:
                if n in T:
                    return n
            return "catholic_monarchy"
        if gov == "steppe_horde":
            return pick("eurasian_horde" if grp == "muslim" else "eurasian_horde_no_muslim", "eurasian_horde")
        if grp == "christian":
            if rel == "catholic" or rel in ("catharism", "waldensian", "lollardy"):
                return {"republic": pick("catholic_republic" + nc, "catholic_republic"),
                        "theocracy": pick("catholic_bishopric" + nc, "catholic_bishopric"),
                        "tribe": pick("gaelic_tribe" + nc, "gaelic_tribe")}.get(gov, pick("catholic_monarchy" + nc, "catholic_monarchy"))
            return {"republic": pick("catholic_republic" + nc),
                    "theocracy": pick("catholic_bishopric" + nc),
                    "tribe": pick("eurasian_orthodox_tribe")}.get(gov, pick("eastern_european_monarchy" + nc, "eastern_european_monarchy"))
        if grp == "muslim":
            return {"republic": pick("muslim_republic"), "tribe": pick("muslim_tribe" + nc, "muslim_tribe")}.get(
                gov, pick("muslim_monarchy" + nc, "muslim_monarchy"))
        if grp == "dharmic":
            if rel == "jain":
                return pick("indian_hindu_monarchy_jain" + nc, "indian_hindu_monarchy_jain")
            return pick("indian_hindu_monarchy" + nc, "indian_hindu_monarchy")
        if grp == "buddhist":
            if rel == "shinto":
                return pick("japanese_clan")
            if rel in ("theravada",):
                return pick("south_east_asia_monarchy" + nc, "south_east_asia_monarchy")
            if rel in ("sanjiao",):
                return pick("chinese_monarchy", "east_asia_monarchy")
            return pick("east_asia_monarchy" + nc, "east_asia_monarchy")
        if grp.startswith("folk_african"):
            return pick("subsaharan_tribe") if gov == "tribe" else pick("subsaharan_monarchy" + nc, "subsaharan_monarchy")
        if grp.startswith("folk_asian") or grp.startswith("folk_se_asian"):
            return pick("asia_tribe") if gov == "tribe" else pick("south_east_asia_monarchy" + nc, "south_east_asia_monarchy")
        if grp.startswith("folk_european"):
            return pick("eurasian_tribe") if gov == "tribe" else pick("lithuanian_monarchy", "eastern_european_monarchy" + nc)
        return pick("eastern_european_monarchy" + nc, "eastern_european_monarchy")

    # ------------------------------------------------------- characters
    def _characters(self) -> None:
        sv, w, eu5 = self.sv, self.w, self.eu5
        start = eu5.start_date
        ruler_of: dict[str, str] = {c.ruler: tag for tag, c in w.countries.items()}

        aged_up: list[str] = []
        raw: dict[str, tuple[float, float, float]] = {}     # key -> CK3-weighted (adm, dip, mil)
        skill_model = SkillModel(self.ck3, sv, self.log) if self.o.skill_bonuses else None

        def make(cid: str, tag: str, estate: str = "nobles_estate", min_age: float = 0.0) -> str | None:
            ch = sv.characters.get(cid)
            if ch is None:
                return None
            key = f"ck3c_{cid}"
            if key in w.characters:
                return key if w.characters[key].tag == tag else None
            country = w.countries[tag]
            age = _age_on(ch.birth, sv.date)
            if age is None:
                age = 30.0
            if age < min_age:
                aged_up.append(f"{tag} ({age:.0f}->{min_age:.0f})")
                age = float(min_age)
            birth = _date_add_years(start, -age)
            rule = self.cm.rule_for_save_culture(sv, ch.culture)
            cul = self.cm.resolve(rule, None) if rule else None
            rel = self.rm.for_save_faith(sv, ch.faith)
            sk = (ch.skills + [0.0] * 6)[:6]         # dip mar ste int lea pro (prowess unused)
            if skill_model is not None:
                sk = [max(0.0, a + b) for a, b in zip(sk[:5], skill_model.bonus(ch))] + sk[5:]
            dip = 0.85 * sk[0] + 0.15 * sk[3]
            mil = 0.85 * sk[1] + 0.15 * sk[3]
            adm = 0.45 * sk[2] + 0.45 * sk[4] + 0.10 * sk[3]
            raw[key] = (adm, dip, mil)
            fname = ch.first_name or "Unknown"
            fkey = self._name_key(fname)
            dyn = self._dynasty(ch, country.capital)
            if dyn is None:
                dyn = self._fallback_dynasty(country)
            w.characters[key] = EU5Character(
                key=key, ck3_id=cid, first_name=fkey, female=ch.female, birth=birth,
                culture=cul or country.culture or "french", religion=rel or country.religion or "catholic",
                adm=0, dip=0, mil=0, tag=tag, dynasty=dyn, birth_loc=country.capital,
                estate=estate)
            return key

        for tag, c in w.countries.items():
            ch = sv.characters.get(c.ruler)
            if ch is None:
                continue
            est = "clergy_estate" if c.gov_type == "theocracy" and "clergy_estate" in eu5.estates else "nobles_estate"
            rk = make(c.ruler, tag, est, min_age=float(self.o.min_ruler_age))
            # consort
            sp = ch.primary_spouse or (ch.spouses[0] if ch.spouses else None)
            if sp and sp in sv.characters and sp not in ruler_of:
                ck = make(sp, tag)
                if ck and rk:
                    c.consort = ck
                    w.characters[ck].spouse = rk
            # heir
            hid = ch.heir if ch.heir in sv.characters else self._guess_heir(ch, c)
            if hid and hid not in ruler_of and hid != sp:
                hk = make(hid, tag)
                if hk:
                    c.heir = hk
            c.ruler_key = rk
        # one multiplier for the whole save: the best weighted CK3 value becomes 100
        top = max((v for t in raw.values() for v in t), default=0.0)
        scale = 100.0 / top if top > 0 else 1.0
        for key, (adm, dip, mil) in raw.items():
            c = w.characters[key]
            c.adm, c.dip, c.mil = (int(_clamp(round(x * scale), 0, 100)) for x in (adm, dip, mil))
        if raw:
            w.report.append(f"STATS: ADM = 45% stewardship + 45% learning + 10% intrigue, DIP = 85% diplomacy + "
                            f"15% intrigue, MIL = 85% martial + 15% intrigue (CK3 base skills"
                            f"{' + bonuses as CK3 shows them' if self.o.skill_bonuses else ''}), times {scale:.2f} "
                            f"so the best value in this save is 100")
        if aged_up:
            w.report.append(f"RULERS aged up to {self.o.min_ruler_age} (EU5 starts have no child rulers): "
                            + ", ".join(aged_up))

    def _guess_heir(self, ch: Character, c: Country) -> str | None:
        kids = [self.sv.characters[k] for k in ch.children if k in self.sv.characters]
        if not kids:
            return None
        male_only = c.heir_selection == "salic_law"

        def key(k: Character):
            b = date_tuple(k.birth) or (9999, 1, 1)
            return (0 if not k.female else (1 if not male_only else 9), b)
        kids.sort(key=key)
        if male_only and kids[0].female:
            return None
        return kids[0].id

    def _name_key(self, name: str) -> str:
        # CK3 first names are often localization keys ("A_sa", or All Under
        # Heaven's "Wanqing_4E07_9877"); resolve them to text first
        text = resolve(self.ck3.loc, name) or name
        if text == name:
            text = re.sub(r"(_[0-9A-Fa-f]{4})+$", "", name).replace("_", " ").strip() or name
        # keep EU5's own name keys when they exist (name_philip ...)
        cand = "name_" + norm(text)
        if cand in self.eu5.loc:
            return cand
        k = "ck3n_" + (re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_")) or "x")
        if not re.search(r"[a-z]", k[5:]):
            k = "ck3n_" + str(abs(hash(name)) % 10 ** 8)
        self.w.loc_strings.setdefault(k, text)
        return k

    def _fallback_dynasty(self, country: Country) -> str:
        key = f"ck3d_x_{country.tag.lower()}"
        if key not in self.w.dynasties:
            text = f"{country.adj or country.name}"
            self.w.dynasties[key] = {"name": text, "home": country.capital}
            self.w.loc_strings[key] = text
        return key

    def _dynasty(self, ch: Character, home: str | None) -> str | None:
        hid = ch.house
        if not hid:
            return None
        key = f"ck3d_{hid}"
        if key not in self.w.dynasties:
            h = self.sv.houses.get(hid)
            raw = (h.name or h.key) if h else None
            if raw is None and h and h.dynasty:
                raw = self.sv.dynasty_names.get(h.dynasty)
            text = self.ck3.loc.get(raw, None) if raw else None
            if text is None and raw:
                text = raw if (" " in raw or not raw.islower()) and not raw.startswith(("dynn_", "house_")) else _pretty(raw)
            text = text or f"House {hid}"
            if h and h.prefix:
                pre = self.ck3.loc.get(h.prefix, "")
                if pre and not text.lower().startswith(pre.lower()):
                    text = f"{pre}{'' if pre.endswith(chr(39)) else ' '}{text}".strip()
            self.w.dynasties[key] = {"name": text, "home": home or self._any_home()}
            self.w.loc_strings[key] = text
        return key

    def _any_home(self) -> str | None:
        for c in self.w.countries.values():
            if c.capital:
                return c.capital
        return None

    # ------------------------------------------------------ enclave fill
    def _fill_enclaves(self) -> None:
        """EU5 land the map alignment left out (CK3 mountains in the Alps,
        slivers along borders) that is surrounded by converted land joins the
        CK3 barony it borders most, instead of staying a vanilla leftover."""
        if self.adj is None:
            return
        eu5, mp = self.eu5, self.map

        def is_land(l: str) -> bool:
            li = eu5.locations.get(l)
            return li is not None and li.kind == "land"
        todo = {l for l in eu5.land_locations() if l not in mp.loc_to_barony}
        seen: set[str] = set()
        filled = 0
        pockets = 0
        for start in sorted(todo):
            if start in seen:
                continue
            comp, stack, border = [], [start], set()
            seen.add(start)
            while stack:
                l = stack.pop()
                comp.append(l)
                for nb in self.adj.get(l, ()):
                    if not is_land(nb):
                        continue
                    if nb in todo:
                        if nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
                    else:
                        border.add(nb)
            if not border or len(comp) > self.o.enclave_max_size:
                continue
            # enclosed = every land neighbour outside the pocket is mapped (true by construction);
            # grow inwards from the edge so each location takes its neighbours' barony
            pockets += 1
            left = set(comp)
            while left:
                progress = []
                for l in sorted(left):
                    cnt: dict[str, int] = {}
                    for nb in self.adj.get(l, ()):
                        b = mp.loc_to_barony.get(nb)
                        if b:
                            cnt[b] = cnt.get(b, 0) + 1
                    if cnt:
                        progress.append((l, max(cnt, key=lambda k: (cnt[k], k))))
                if not progress:
                    break
                for l, b in progress:
                    mp.loc_to_barony[l] = b
                    mp.loc_share[l] = 0.0
                    left.discard(l)
                    filled += 1
        if filled:
            mp.rebuild_inverse()
            self.log(f"Filled {filled} unmapped EU5 locations in {pockets} pockets surrounded by CK3 land")
            self.w.report.append(f"MAP: {filled} locations in {pockets} pockets surrounded by CK3 land took the "
                                 f"barony they border most (turn off 'fill pockets' to keep them vanilla)")

    # ---------------------------------------------------------- exclaves
    def _split_exclaves(self) -> None:
        """A part of a country that isn't connected to the part holding its
        capital becomes a vassal of that country with an EU5-generated ruler.
        Connected = through the country's own or its subjects' land, an EU5
        strait, or a crossing of at most ``exclave_sea_hop`` sea/lake/
        wasteland locations."""
        if self.adj is None:
            return
        w, eu5 = self.w, self.eu5

        def is_land(l: str) -> bool:
            li = eu5.locations.get(l)
            return li is not None and li.kind == "land"
        children: dict[str, list[str]] = {}
        for t, c in w.countries.items():
            if c.overlord:
                children.setdefault(c.overlord, []).append(t)

        def realm_land(tag: str) -> set[str]:
            out: set[str] = set()
            stack, seen = [tag], set()
            while stack:
                t = stack.pop()
                if t in seen or t not in w.countries:
                    continue
                seen.add(t)
                out.update(w.countries[t].locations)
                stack += children.get(t, [])
            return out
        made: list[Country] = []
        for tag in sorted(w.countries):
            c = w.countries[tag]
            if len(c.locations) < 2 or not c.capital:
                continue
            groups = components(c.locations, self.adj, realm_land(tag), is_land, self.o.exclave_sea_hop)
            if len(groups) < 2:
                continue
            main = next((g for g in groups if c.capital in g), None)
            if main is None:
                continue
            for g in sorted((g for g in groups if g is not main), key=lambda g: (-len(g), min(g))):
                nc = self._exclave_country(c, sorted(g))
                children.setdefault(tag, []).append(nc.tag)
                made.append(nc)
        if made:
            self.log(f"Exclaves: {len(made)} detached pieces became vassals")
            by_parent: dict[str, list[Country]] = {}
            for nc in made:
                by_parent.setdefault(nc.exclave_of or "", []).append(nc)
            for p, ncs in sorted(by_parent.items()):
                self.w.report.append(
                    f"EXCLAVES of {p} ({w.countries[p].name}) -> vassals: " +
                    "; ".join(f"{n.tag} {n.name} [{', '.join(n.locations[:6])}{' …' if len(n.locations) > 6 else ''}]"
                              for n in ncs))

    def _exclave_country(self, parent: Country, locs: list[str]) -> Country:
        w, eu5 = self.w, self.eu5
        counties: list[str] = []
        for l in locs:
            bar = self.map.loc_to_barony.get(l)
            cty = self.ck3.county_of_barony(bar) if bar else None
            if cty:
                counties.append(cty)
        taken = {c.name.lower() for c in w.countries.values() if c.exclave_of == parent.tag}
        tkey = self._exclave_title(counties, parent, taken)
        st = self.sv.title_by_key.get(tkey)
        nm = (st.name if st and st.name else None) or resolve(self.ck3.loc, tkey) or _pretty(tkey.split("_", 1)[-1])
        adj = (st.adj if st and st.adj else None) or resolve(self.ck3.loc, tkey + "_adj") or nm
        nm, adj = _clean_loc(nm), _clean_loc(adj)
        td = self.ck3.titles.get(tkey)
        tier = td.tier if td else 1
        tag = self.tf.new(nm)
        nc = Country(tag=tag, ruler="", title=tkey, tier=tier, new_tag=True)
        nc.locations = locs
        nc.overlord = parent.tag
        nc.exclave_of = parent.tag
        nc.capital = max(locs, key=lambda l: (self._rank_score(l), w.loc_dev.get(l, 0.0), l))
        nc.culture = self._most_common(w.loc_culture, locs) or parent.culture
        nc.religion = self._most_common(w.loc_religion, locs) or parent.religion
        order = ["rank_county", "rank_duchy", "rank_kingdom", "rank_empire"]
        r = TIER_RANK.get(tier, "rank_county")
        if r in order and parent.rank in order and order.index(r) >= order.index(parent.rank):
            r = order[max(0, order.index(parent.rank) - 1)]
        nc.rank = r if (r in eu5.country_ranks or not eu5.country_ranks) else parent.rank
        nc.gov_type = parent.gov_type
        nc.heir_selection = parent.heir_selection
        nc.name, nc.adj = nm, adj
        nc.name_source = f"CK3 title {tkey} (detached part of {parent.tag})"
        nc.color = _lighter(parent.color)
        nc.coa_id = st.coa_id if st else None
        nc.includes, nc.tech = self._templates(nc, self._geo_donor(locs), any(l in eu5.coastal for l in locs))
        if nc.tech is None:
            nc.tech = parent.tech
        w.loc_strings[tag] = nm
        w.loc_strings[tag + "_ADJ"] = adj
        w.countries[tag] = nc
        self._apply_name_override(nc)
        gone = set(locs)
        parent.locations = [l for l in parent.locations if l not in gone]
        for l in locs:
            w.owner[l] = tag
        return nc

    def _exclave_title(self, counties: list[str], parent: Country, taken: set[str] | None = None) -> str:
        """Name a detached piece after its county, or after the duchy/kingdom
        it mostly fills."""
        if not counties:
            return parent.title
        cnt: dict[str, int] = {}
        for k in counties:
            cnt[k] = cnt.get(k, 0) + 1
        largest = max(cnt, key=lambda k: (cnt[k], k))
        uniq = sorted(cnt)
        cand = largest
        if len(uniq) > 1:
            def chain(k: str) -> list[str]:
                out, t = [], self.ck3.titles.get(k)
                while t is not None and t.key not in out:
                    out.append(t.key)
                    t = self.ck3.titles.get(t.parent) if t.parent else None
                return out
            chains = [chain(k) for k in uniq]
            common = set(chains[0]).intersection(*chains[1:])
            lca = next((k for k in chains[0] if k in common), None)
            if lca and self.ck3.titles[lca].tier <= 3:
                total = len(self._counties_under(lca))
                if total and len(uniq) >= 0.5 * total:
                    cand = lca
        # don't repeat the parent's own name or a sibling piece's name
        avoid = set(taken or ()) | {(parent.name or "").lower()}

        def shown(k: str) -> str:
            st = self.sv.title_by_key.get(k)
            return ((st.name if st and st.name else None) or resolve(self.ck3.loc, k) or k).lower()
        if shown(cand) in avoid:
            for k in sorted(cnt, key=lambda k: (-cnt[k], k)):
                if shown(k) not in avoid:
                    return k
        return cand

    def _counties_under(self, key: str) -> list[str]:
        t = self.ck3.titles.get(key)
        if t is None:
            return []
        if t.tier == 1:
            return [key]
        out: list[str] = []
        for ch in t.children:
            if not ch.startswith("b_"):
                out += self._counties_under(ch)
        return out

    # -------------------------------------------------------------- HRE
    def _hre(self) -> None:
        if not self.emperor or self.emperor not in self.tag_of_char:
            return
        etag = self.tag_of_char[self.emperor]
        members = [etag] + [self.tag_of_char[c] for c, p in self.overlord_char.items()
                            if c != self.emperor and self.liege.get(c) == self.emperor and c in self.tag_of_char]
        for t in members:
            self.w.countries[t].hre_member = True
        self.w.countries[etag].emperor = True
        locs: list[str] = []
        member_set = set(members)

        def under(tag):
            c = self.w.countries[tag]
            while c.overlord:
                if c.overlord in member_set:
                    return True
                c = self.w.countries[c.overlord]
            return False
        for tag, c in self.w.countries.items():
            if tag in member_set or under(tag):
                locs += c.locations
        self.w.hre = {"emperor": etag, "members": members, "locations": sorted(set(locs))}
        self.log(f"HRE: emperor {etag}, {len(members)} members")

    def _check_religion_dates(self) -> None:
        start = date_tuple(self.eu5.start_date) or (1337, 4, 1)
        used: dict[str, int] = {}
        for r in self.w.loc_religion.values():
            used[r] = used.get(r, 0) + 1
        for c in self.w.countries.values():
            if c.religion:
                used.setdefault(c.religion, 0)
        for r, n in sorted(used.items()):
            rb = self.eu5.religions.get(r)
            en = date_tuple(rb.str("enable")) if rb is not None else None
            if en and en > start:
                self.w.report.append(f"NOTE: religion '{r}' is historically enabled only from {rb.str('enable')} "
                                     f"in EU5 but is used in {n} locations / by some countries")

    # --------------------------------------------------- vanilla leftovers
    def _vanilla_survivors(self) -> None:
        """Vanilla countries are never deleted: EU5 expects every defined
        country in the setup (vanilla lists all 2337, landless ones included,
        each with a capital). A vanilla country whose land is all CK3 land now
        stays as a landless country (like vanilla's releasables), keeping its
        capital and characters; it takes no part in diplomacy, IOs or wars.
        'pop' tribes are left exactly as in vanilla."""
        covered = self.w.covered
        for tag, vc in self.v.countries.items():
            if tag in self.w.countries:
                continue          # reused by a CK3 realm
            if vc.block.str("type") == "pop":
                continue
            if vc.owned and all(l in covered for l in vc.owned):
                self.w.removed_tags.add(tag)
        self.log(f"Vanilla countries left without land (all of it is CK3 land now): {len(self.w.removed_tags)}")

# ------------------------------------------------------ potential triggers
def _eval_potential(b: Block, loc, eu5) -> bool:
    """Evaluate the simple parts of a building's location_potential. Unknown
    conditions count as satisfied (so an OR containing one is satisfied)."""
    def ev(block: Block, mode: str) -> bool:
        results: list[bool] = []
        unknown = False
        for k, op, v in block.items:
            if k is None:
                continue
            r: bool | None
            if k in ("OR", "AND", "NOT", "NOR", "NAND") and isinstance(v, Block):
                inner = ev(v, "OR" if k in ("OR", "NOR") else "AND")
                r = (not inner) if k in ("NOT", "NOR", "NAND") else inner
            elif k == "raw_material" and isinstance(v, str):
                want = v.split(":", 1)[-1]
                r = (loc.raw_material == want) if op in ("=", "==") else (loc.raw_material != want)
            elif k == "is_coastal" and isinstance(v, str):
                r = (loc.key in eu5.coastal) == (v == "yes")
            elif k in ("topography", "vegetation", "climate") and isinstance(v, str):
                want = v.split(":", 1)[-1]
                r = getattr(loc, k) == want
            else:
                r = None
            if r is None:
                unknown = True
            else:
                results.append(r)
        if mode == "OR":
            return unknown or any(results) or not results
        return all(results)
    try:
        return ev(b, "AND")
    except Exception:
        return True
