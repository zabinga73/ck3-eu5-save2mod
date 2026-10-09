"""Self-check of a generated mod's setup against the rules vanilla EU5 follows.

Every check is also run on the vanilla setup, and anything vanilla itself does
is not reported, so what's left is what the conversion introduced.

    python -m save2mod check <mod folder>
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

from .pdx import Block, Tagged, parse_file

OWN_KEYS = ("own_control_core", "own_control_integrated", "own_control_conquered", "own_control_colony",
            "own_core", "own_conquered")
CHAR_REF_KEYS = ("ruler", "consort", "heir", "active_regent", "character", "father", "mother", "spouse",
                 "artist", "regent")
TAG_RE = re.compile(r"[A-Z][A-Z0-9]{2}")


@dataclass
class Setup:
    files: dict[str, Block] = field(default_factory=dict)
    defs: set[str] = field(default_factory=set)


def _effective_setup(eu5_root: str, mod_root: str | None) -> Setup:
    s = Setup()
    names: dict[str, str] = {}
    for f in glob.glob(os.path.join(eu5_root, "main_menu", "setup", "start", "*.txt")):
        names[os.path.basename(f)] = f
    if mod_root:
        for f in glob.glob(os.path.join(mod_root, "main_menu", "setup", "start", "*.txt")):
            names[os.path.basename(f)] = f
    for n, f in sorted(names.items()):
        try:
            s.files[n] = parse_file(f)
        except Exception as e:          # noqa: BLE001
            s.files[n] = Block()
            s.files["__parse_error__" + n] = Block([("error", "=", str(e))])
    defs: dict[str, str] = {}
    for f in glob.glob(os.path.join(eu5_root, "in_game", "setup", "countries", "*.txt")):
        defs[os.path.basename(f)] = f
    if mod_root:
        for f in glob.glob(os.path.join(mod_root, "in_game", "setup", "countries", "*.txt")):
            defs[os.path.basename(f)] = f
    for f in defs.values():
        try:
            s.defs |= {k for k, _o, v in parse_file(f).items if k and isinstance(v, Block)}
        except Exception:               # noqa: BLE001
            pass
    return s


def _vals(b) -> list[str]:
    return [x for x in b.values() if isinstance(x, str)] if isinstance(b, Block) else []


def _check(s: Setup, eu5) -> list[str]:
    out: list[str] = []
    for n in s.files:
        if n.startswith("__parse_error__"):
            out.append(f"{n[15:]}: does not parse ({s.files[n].str('error')})")
    f10 = s.files.get("10_countries.txt", Block())
    countries = f10.path("countries", "countries")
    countries = countries if isinstance(countries, Block) else Block()
    tags = set(countries.keys())
    db = s.files.get("05_characters.txt", Block()).block("character_db")
    chars = set(db.keys())
    dyns = set(s.files.get("04_dynasties.txt", Block()).block("dynasty_manager").keys())
    locs = eu5.locations
    special = {"DUMMY", "MER", "PIR"}

    # --- definitions vs setup
    for t in sorted(s.defs - tags - special):
        out.append(f"country {t} is defined but missing from 10_countries")
    for t in sorted(tags - s.defs):
        out.append(f"10_countries: {t} has no country definition")

    # --- countries
    owner: dict[str, str] = {}
    for t, cb in countries.pairs():
        if not isinstance(cb, Block):
            continue
        owned = [x for k in OWN_KEYS for lst in cb.getall(k) for x in _vals(lst)]
        for l in owned:
            if l in owner:
                out.append(f"10_countries: {l} owned by both {owner[l]} and {t}")
            owner[l] = t
            li = locs.get(l)
            if li is None:
                out.append(f"10_countries: {t} owns unknown location {l}")
            elif li.kind != "land":
                out.append(f"10_countries: {t} owns non-land location {l} ({li.kind})")
        typ = cb.str("type") or "location"
        cap = cb.str("capital")
        if cap is None and typ != "pop":
            out.append(f"10_countries: {t} has no capital")
        if cap is not None:
            if cap not in locs:
                out.append(f"10_countries: {t} capital {cap} is not a location")
            elif owned and cap not in owned:
                out.append(f"10_countries: {t} capital {cap} is not one of its locations")
        for inc in cb.getall("include"):
            if isinstance(inc, str) and eu5.templates and inc not in eu5.templates:
                out.append(f"10_countries: {t} includes unknown template {inc}")
        for d in cb.getall("dynasty"):
            if isinstance(d, str) and d not in dyns:
                out.append(f"10_countries: {t} dynasty {d} not in 04_dynasties")
        for k, table in (("culture", eu5.cultures), ("religion", eu5.religions)):
            v = cb.get(k)
            if isinstance(v, str) and v not in table:
                out.append(f"10_countries: {t} {k} {v} unknown")
        r = cb.get("country_rank")
        if isinstance(r, str) and eu5.country_ranks and r not in eu5.country_ranks:
            out.append(f"10_countries: {t} country_rank {r} unknown")
        gov = cb.get("government")
        if isinstance(gov, Block):
            gt = gov.str("type")
            if gt and eu5.government_types and gt not in eu5.government_types:
                out.append(f"10_countries: {t} government type {gt} unknown")
            hs = gov.str("heir_selection")
            if hs and eu5.heir_selections and hs not in eu5.heir_selections:
                out.append(f"10_countries: {t} heir_selection {hs} unknown")
            for k in ("ruler", "consort", "heir", "active_regent"):
                v = gov.get(k)
                if isinstance(v, str) and v != "random" and v not in chars:
                    out.append(f"10_countries: {t} {k} {v} not in 05_characters")
            v = gov.get("inherit_ruler_terms")
            if isinstance(v, str) and v not in tags:
                out.append(f"10_countries: {t} inherit_ruler_terms {v} not a country")
            for rt in gov.getall("ruler_term"):
                if isinstance(rt, Block):
                    for ch in rt.getall("character"):
                        if isinstance(ch, str) and ch not in chars:
                            out.append(f"10_countries: {t} ruler_term {ch} not in 05_characters")

    # --- characters
    seen: set[str] = set()
    for k, cb in db.pairs():
        if not isinstance(cb, Block):
            continue
        t = cb.str("tag")
        if not t:
            out.append(f"05_characters: {k} has no tag")
        elif t not in tags:
            out.append(f"05_characters: {k} tag {t} is not a country in 10_countries")
        for ref in ("father", "mother", "spouse"):
            r = cb.get(ref)
            if isinstance(r, str) and r not in chars:
                out.append(f"05_characters: {k} {ref} {r} does not exist")
        d = cb.get("dynasty")
        if isinstance(d, str) and d not in dyns:
            out.append(f"05_characters: {k} dynasty {d} not in 04_dynasties")
        for f, table in (("culture", eu5.cultures), ("religion", eu5.religions)):
            v = cb.get(f)
            if isinstance(v, str) and v not in table:
                out.append(f"05_characters: {k} {f} {v} unknown")
        for f in ("birth",):
            v = cb.get(f)
            if isinstance(v, str) and v not in locs:
                out.append(f"05_characters: {k} {f} {v} is not a location")
        seen.add(k)

    # --- pops
    for l, lb in s.files.get("06_pops.txt", Block()).block("locations").pairs():
        if l not in locs:
            out.append(f"06_pops: unknown location {l}")
        if not isinstance(lb, Block):
            continue
        for p in lb.getall("define_pop"):
            if not isinstance(p, Block):
                continue
            if p.get("culture") not in eu5.cultures:
                out.append(f"06_pops: {l} pop culture {p.get('culture')} unknown")
            if p.get("religion") not in eu5.religions:
                out.append(f"06_pops: {l} pop religion {p.get('religion')} unknown")

    # --- buildings
    for fn in ("07_cities_and_buildings.txt", "13_religion.txt"):
        for bt, bb in s.files.get(fn, Block()).block("building_manager").pairs():
            if not isinstance(bb, Block):
                continue
            if eu5.buildings and bt not in eu5.buildings:
                out.append(f"{fn[:2]}_buildings: unknown building {bt}")
            if bb.str("location") not in locs:
                out.append(f"{fn[:2]}_buildings: {bt} in unknown location {bb.str('location')}")
            tg = bb.str("tag")
            if tg and tg not in tags:
                out.append(f"{fn[:2]}_buildings: {bt} owned by {tg}, not a country")

    # --- generic dangling references in every setup file
    defined = s.defs | tags
    for fn, root in s.files.items():
        if fn.startswith("__"):
            continue
        for path, k, v in _walk(root):
            if not isinstance(v, str):
                continue
            if (fn != "10_countries.txt" or len(path) > 2) and not (fn == "05_characters.txt" and k == "tag"):
                if TAG_RE.fullmatch(v) and v in defined and v not in tags:
                    out.append(f"{fn}: {'/'.join(p for p in path[-2:] if p)}{'/' if path else ''}{k} = {v} "
                               f"refers to a country missing from 10_countries")
            if k in CHAR_REF_KEYS and fn != "05_characters.txt" and not TAG_RE.fullmatch(v) \
                    and v not in ("random", "yes", "no") and "_" in v and v not in chars:
                out.append(f"{fn}: {k} = {v} refers to a character missing from 05_characters")
    return out


def _walk(b: Block, path: tuple = ()):
    for k, _op, v in b.items:
        if isinstance(v, Block):
            yield from _walk(v, path + ((k or ""),))
        elif isinstance(v, Tagged):
            continue
        else:
            yield path, k, v


_BASELINE: dict[str, set[str]] = {}


def check_mod(mod_root: str, eu5) -> tuple[list[str], dict[str, int]]:
    """Problems in the mod's setup that vanilla doesn't have, plus some counts."""
    base = _BASELINE.get(eu5.root)
    if base is None:
        base = set(_check(_effective_setup(eu5.root, None), eu5))
        _BASELINE[eu5.root] = base
    s = _effective_setup(eu5.root, mod_root)
    found = [p for p in dict.fromkeys(_check(s, eu5)) if p not in base]
    countries = s.files.get("10_countries.txt", Block()).path("countries", "countries")
    stats = {
        "country_definitions": len(s.defs),
        "setup_countries": len(countries.keys()) if isinstance(countries, Block) else 0,
        "characters": len(s.files.get("05_characters.txt", Block()).block("character_db").keys()),
    }
    return found, stats


def summarize(problems: list[str], limit: int = 40) -> list[str]:
    """Group problems by their message shape for the log."""
    groups: dict[str, list[str]] = {}
    for p in problems:
        shape = re.sub(r"\b[A-Z][A-Z0-9]{2}\b", "TAG", p)
        shape = re.sub(r"= \S+", "= X", shape)
        shape = re.sub(r"(\w+: )\S+", r"\1…", shape, count=1)
        groups.setdefault(shape, []).append(p)
    lines = []
    for shape, ps in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"{len(ps):>6} × {ps[0]}")
        if len(lines) >= limit:
            break
    return lines
