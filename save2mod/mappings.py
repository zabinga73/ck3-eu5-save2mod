"""Culture, religion, building and title-tag mapping tables.

Tables are CSV files in the package's ``data`` folder. A user copy placed in
the working "tables" folder (see :func:`tables_dir`) overrides the shipped one,
which is how the GUI's edits persist.
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass, field

PKG_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def home_dir() -> str:
    """Settings, edited tables and caches: $SAVE2MOD_HOME or ~/.save2mod.
    A folder left by the tool's earlier name (~/.ck3toeu5) is moved over once."""
    env = os.environ.get("SAVE2MOD_HOME")
    if env:
        return env
    base = os.path.join(os.path.expanduser("~"), ".save2mod")
    old = os.path.join(os.path.expanduser("~"), ".ck3toeu5")
    if not os.path.exists(base) and os.path.isdir(old):
        try:
            os.rename(old, base)
        except OSError:
            pass
    return base


def tables_dir() -> str:
    d = os.path.join(home_dir(), "tables")
    os.makedirs(d, exist_ok=True)
    return d


def table_path(name: str, prefer_user: bool = True) -> str:
    user = os.path.join(tables_dir(), name)
    if prefer_user and os.path.exists(user):
        return user
    return os.path.join(PKG_DATA, name)


def read_table(name: str) -> list[dict[str, str]]:
    path = table_path(name)
    rows: list[dict[str, str]] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8-sig", newline="") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    for row in csv.DictReader(lines):
        rows.append({k.strip(): (v or "").strip() for k, v in row.items() if k})
    return rows


def write_table(name: str, header: list[str], rows: list[dict[str, str]], comment: str = "") -> str:
    path = os.path.join(tables_dir(), name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        if comment:
            for ln in comment.splitlines():
                fh.write(f"# {ln}\n")
        w = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ============================================================== cultures
@dataclass
class CultureRule:
    default: str
    regional: set[str] = field(default_factory=set)       # explicit cultures
    regional_groups: set[str] = field(default_factory=set)
    source: str = "table"


class CultureMapper:
    def __init__(self, ck3, eu5, log=print):
        self.ck3 = ck3
        self.eu5 = eu5
        self.log = log
        self.rules: dict[str, CultureRule] = {}
        self.problems: list[str] = []
        self._load()

    def _valid(self, c: str) -> bool:
        return c in self.eu5.cultures

    def _load(self) -> None:
        for r in read_table("culture_map.csv"):
            k, v = r.get("ck3", ""), r.get("eu5", "")
            if not k or not v:
                continue
            if not self._valid(v):
                self.problems.append(f"culture_map: EU5 culture '{v}' (for {k}) does not exist")
                continue
            rule = CultureRule(default=v)
            for tok in (r.get("regional") or "").split():
                if tok.startswith("group:"):
                    rule.regional_groups.add(tok[6:])
                elif self._valid(tok):
                    rule.regional.add(tok)
            self.rules[k] = rule
        # automatic matches for everything else in CK3
        ek: dict[str, str] = {}
        for k in self.eu5.cultures:
            ek.setdefault(norm(k), k)
            if k.endswith("_culture"):
                ek.setdefault(norm(k[:-8]), k)
        en: dict[str, str] = {}
        for k in self.eu5.cultures:
            n = self.eu5.loc.get(k)
            if n:
                en.setdefault(norm(n), k)
        for c in self.ck3.culture_heritage:
            if c in self.rules:
                continue
            m = ek.get(norm(c)) or en.get(norm(self.ck3.loc.get(c, c)))
            if m:
                self.rules[c] = CultureRule(default=m, source="auto")
        # heritage fallbacks: first mapped culture of the heritage
        for c, h in self.ck3.culture_heritage.items():
            if h not in self.rules and c in self.rules:
                r0 = self.rules[c]
                self.rules[h] = CultureRule(default=r0.default, source="heritage")
        missing = [c for c in self.ck3.culture_heritage if c not in self.rules]
        for c in missing:
            h = self.ck3.culture_heritage.get(c)
            if h in self.rules:
                self.rules[c] = CultureRule(default=self.rules[h].default, source="heritage")
            else:
                self.problems.append(f"no EU5 culture for CK3 culture '{c}'")

    # save-level resolution --------------------------------------------------
    def rule_for_save_culture(self, save, cid: str | None, _depth: int = 0) -> CultureRule | None:
        if cid is None or _depth > 6:
            return None
        cu = save.cultures.get(cid)
        if cu is None:
            return None
        for key in (cu.template, cu.name):
            if key and key in self.rules:
                return self.rules[key]
        for p in cu.parents:
            r = self.rule_for_save_culture(save, p, _depth + 1)
            if r is not None:
                return r
        if cu.heritage and cu.heritage in self.rules:
            return self.rules[cu.heritage]
        return None

    def resolve(self, rule: CultureRule | None, vanilla_culture: str | None) -> str | None:
        if rule is None:
            return None
        if vanilla_culture:
            if vanilla_culture in rule.regional:
                return vanilla_culture
            if rule.regional_groups and set(self.eu5.culture_groups.get(vanilla_culture, [])) & rule.regional_groups:
                if "jewish_group" not in self.eu5.culture_groups.get(vanilla_culture, []) or \
                        "jewish_group" in rule.regional_groups:
                    return vanilla_culture
        return rule.default


# ============================================================== religions
class ReligionMapper:
    def __init__(self, ck3, eu5, log=print):
        self.ck3 = ck3
        self.eu5 = eu5
        self.map: dict[str, str] = {}
        self.notes: dict[str, str] = {}
        self.problems: list[str] = []
        for r in read_table("religion_map.csv"):
            k, v = r.get("ck3", ""), r.get("eu5", "")
            if not k or not v:
                continue
            if v not in eu5.religions:
                self.problems.append(f"religion_map: EU5 religion '{v}' (for {k}) does not exist")
                continue
            self.map[k] = v
            if r.get("note"):
                self.notes[k] = r["note"]
        # automatic key/name match for anything not listed
        by_norm = {norm(k): k for k in eu5.religions}
        by_name = {norm(eu5.loc.get(k, "")): k for k in eu5.religions if eu5.loc.get(k)}
        for f, rel in ck3.faith_religion.items():
            if f in self.map or rel in self.map:
                continue
            m = by_norm.get(norm(f)) or by_name.get(norm(ck3.loc.get(f, f)))
            if m:
                self.map[f] = m
        # CK3 1.20 rites: only a rite whose name matches an EU5 religion gets its
        # own entry; the others fall back to their faith's row
        for rite in ck3.rite_faith:
            if rite not in self.map:
                m = by_norm.get(norm(rite)) or by_name.get(norm(ck3.loc.get(rite, rite)))
                if m:
                    self.map[rite] = m

    def for_faith_key(self, faith: str | None, religion: str | None = None) -> str | None:
        if faith and faith in self.map:
            return self.map[faith]
        rel = religion or (self.ck3.faith_religion.get(faith) if faith else None)
        if rel and rel in self.map:
            return self.map[rel]
        return None

    def for_save_faith(self, save, fid: str | None) -> str | None:
        if fid is None:
            return None
        f = save.faiths.get(fid)
        if f is None:
            return None
        rel_key = f.religion_tag or self.ck3.faith_religion.get(f.template or f.tag or "")
        for key in (f.rite, f.tag, f.template):
            if key and key in self.map:
                return self.map[key]
        out = self.for_faith_key(f.template or f.tag, rel_key)
        if out:
            return out
        fam = self.ck3.religion_family.get(rel_key or "")
        return {"rf_abrahamic": "catholic", "rf_eastern": "hindu", "rf_pagan": "shamanism",
                "rf_sinitic": "sanjiao"}.get(fam or "", None)


# ============================================================== buildings
@dataclass
class BuildingRule:
    ck3: str                      # family key (no level suffix), full key, or glob like *_mines
    eu5: str                      # used in towns/cities/megalopolises ("" = nothing)
    eu5_rural: str                # used in rural settlements ("" = nothing)
    level_div: float = 2.0        # EU5 level = ceil(ck3_level / level_div)
    max_level: int = 3
    min_ck3_level: int = 1
    note: str = ""


_LEVEL = re.compile(r"^(.*?)(?:_0?(\d+))?$")


def split_building(key: str) -> tuple[str, int]:
    m = _LEVEL.match(key)
    fam, lvl = m.group(1), m.group(2)
    return fam, int(lvl) if lvl else 1


RANK_RURAL = "rural_settlement"


class BuildingMapper:
    def __init__(self, eu5):
        import fnmatch
        self._fn = fnmatch
        self.eu5 = eu5
        self.rules: dict[str, BuildingRule] = {}
        self.globs: list[BuildingRule] = []
        self.problems: list[str] = []
        for r in read_table("building_map.csv"):
            k = r.get("ck3", "")
            if not k:
                continue
            e = r.get("eu5", "")
            er = r.get("eu5_rural", "")
            for col, val in (("eu5", e), ("eu5_rural", er)):
                if val and val not in eu5.buildings:
                    self.problems.append(f"building_map: EU5 building '{val}' ({col} for {k}) does not exist")
            e = e if e in eu5.buildings else ""
            er = er if er in eu5.buildings else ""
            try:
                div = float(r.get("level_div") or 2)
                mx = int(float(r.get("max_level") or 3))
                mn = int(float(r.get("min_ck3_level") or 1))
            except ValueError:
                div, mx, mn = 2.0, 3, 1
            rule = BuildingRule(ck3=k, eu5=e, eu5_rural=er, level_div=div, max_level=mx,
                                min_ck3_level=mn, note=r.get("note", ""))
            if any(ch in k for ch in "*?["):
                self.globs.append(rule)
            else:
                self.rules[k] = rule
        # a buildings table saved from the GUI before 0.1.0 still has the old, looser fort rules
        if table_path("building_map.csv") != table_path("building_map.csv", prefer_user=False):
            cw, wt = self.rules.get("curtain_walls"), self.rules.get("watchtowers")
            if (cw is not None and cw.min_ck3_level < 5) or (wt is not None and (wt.eu5 or wt.eu5_rural)):
                msg = ("your saved buildings table has the pre-0.1.0 fort rules (castles from CK3 level 3, "
                       "watchtowers as stockades); press 'Reset to defaults' on the buildings table in the "
                       "Mappings tab for the stricter ones")
                self.problems.append(msg)

    def rule_for(self, ck3_building: str) -> BuildingRule | None:
        fam, _lvl = split_building(ck3_building)
        r = self.rules.get(ck3_building) or self.rules.get(fam)
        if r is None:
            for g in self.globs:
                if self._fn.fnmatch(fam, g.ck3) or self._fn.fnmatch(ck3_building, g.ck3):
                    return g
        return r

    def is_fort(self, ck3_building: str) -> bool:
        """True when the rule turns this CK3 building into an ordinary EU5
        fort (castle, city walls, stockade…); unique walls don't count."""
        rule = self.rule_for(ck3_building)
        if rule is None:
            return False
        for t in (rule.eu5, rule.eu5_rural):
            bt = self.eu5.buildings.get(t) if t else None
            if bt is not None and bt.category == "defense_category" and not bt.unique:
                return True
        return False

    def fort_strength(self, ck3_building: str) -> tuple[int, int]:
        """Sort key: (EU5 fort weight, CK3 level)."""
        rule = self.rule_for(ck3_building)
        _fam, lvl = split_building(ck3_building)
        weight = {"castle": 3, "city_walls": 2, "stockade": 1}.get(rule.eu5 if rule else "", 0)
        return weight, lvl

    def convert(self, ck3_building: str, rank: str) -> tuple[str, int] | None:
        """Return (eu5_building, level) for a CK3 building placed in an EU5
        location of the given rank, or None when nothing should be placed."""
        import math
        rule = self.rule_for(ck3_building)
        if rule is None:
            return None
        _fam, lvl = split_building(ck3_building)
        if lvl < rule.min_ck3_level:
            return None
        target = rule.eu5_rural if rank == RANK_RURAL else rule.eu5
        if not target:
            return None
        bt = self.eu5.buildings.get(target)
        if bt is not None and not bt.allowed_in_rank(rank):
            return None
        level = max(1, min(rule.max_level, math.ceil(lvl / max(rule.level_div, 0.01))))
        return target, level


# ============================================================== realm list
REALM_HEADER = ["ck3_title", "eu5_tag", "name", "adjective", "last_conversion", "note"]
REALM_COMMENT = """Realms & tags. After every conversion each realm it made is listed here.
eu5_tag: blank = automatic (matched by English name); an EU5 tag forces that tag; '-' forces a brand-new tag.
name / adjective: blank = automatic; text renames the country in EU5 (works for reused tags too).
last_conversion: what the last conversion did with this title (refreshed on every run; edits there are ignored)."""


def update_realm_table(world, eu5) -> str:
    """Rewrite the user's title_tags.csv: rows the user filled in are kept,
    and every realm of this conversion is listed with what happened to it."""
    rows = read_table("title_tags.csv")
    keep = [dict(r) for r in rows if r.get("ck3_title") and (r.get("eu5_tag") or r.get("name") or r.get("adjective")
                                                             or r.get("note"))]
    by_title: dict[str, dict[str, str]] = {}
    for r in keep:
        r["last_conversion"] = ""
        by_title.setdefault(r["ck3_title"], r)
    now: list[str] = []
    for c in sorted(world.countries.values(), key=lambda c: (-len(c.locations), c.tag)):
        shown = world.loc_strings.get(c.tag) or eu5.loc.get(c.tag) or c.name
        info = (f"{c.tag}{'' if c.new_tag else ' (EU5 tag)'} '{shown}' - name from {c.name_source or 'CK3'}; "
                f"{len(c.locations)} locations" + (f"; vassal of {c.overlord}" if c.overlord else ""))
        r = by_title.get(c.title)
        if r is None:
            r = {"ck3_title": c.title, "eu5_tag": "", "name": "", "adjective": "", "note": ""}
            by_title[c.title] = r
            keep.append(r)
        if c.title not in now:
            now.append(c.title)
            r["last_conversion"] = info
    order = {t: i for i, t in enumerate(now)}
    keep.sort(key=lambda r: (order.get(r["ck3_title"], len(order)), r["ck3_title"]))
    return write_table("title_tags.csv", REALM_HEADER, keep, comment=REALM_COMMENT)

