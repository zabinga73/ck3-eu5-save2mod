"""End-to-end run on the real EU5 text files + real CK3 title data with a
generated CK3 save. The map alignment step is replaced by a name/hierarchy
based mapping (the real bitmaps are not available in CI), everything after
that is the production code path. The output mod is then validated for
internal consistency."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SAVE2MOD_HOME", tempfile.mkdtemp())

from save2mod.ck3game import load_ck3_game          # noqa: E402
from save2mod.eu5game import load_eu5_game          # noqa: E402
from save2mod.ck3save import load_save              # noqa: E402
from save2mod.geomap import MapResult               # noqa: E402
from save2mod.mappings import norm                  # noqa: E402
from save2mod.vanilla import load_vanilla           # noqa: E402
from save2mod.convert import Converter, Options     # noqa: E402
from save2mod.writer import ModWriter               # noqa: E402
from save2mod.pdx import parse_file, Block          # noqa: E402

# Point these at the games' "game" folders (text files are enough) to run this test.
CK3_ROOT = os.environ.get("CK3_GAME", "")
EU5_ROOT = os.environ.get("EU5_GAME", "")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_test_save import make_save                # noqa: E402


def name_mapping(ck3, eu5) -> MapResult:
    bars: dict[str, list[str]] = {}
    for k, t in ck3.titles.items():
        if t.tier == 0 and t.province:
            bars.setdefault(norm(k[2:]), []).append(k)
    res = MapResult()
    for loc in eu5.land_locations():
        c = bars.get(norm(loc))
        if c and len(c) == 1:
            res.loc_to_barony[loc] = c[0]
            res.loc_share[loc] = 1.0
    # spread to the rest of each EU5 province
    for loc, bar in list(res.loc_to_barony.items()):
        prov = eu5.locations[loc].province
        for other in eu5.hierarchy_children.get(prov, []):
            if other in eu5.locations and eu5.locations[other].kind == "land" and other not in res.loc_to_barony:
                res.loc_to_barony[other] = bar
                res.loc_share[other] = 0.6
    res.rebuild_inverse()
    return res


RUN_MAP: list = []


def fake_adjacency(eu5) -> dict[str, set[str]]:
    """Stand-in for the borders read from locations.png (not available here):
    locations of a province touch each other, provinces of an area form a
    chain, areas of a region form a chain."""
    adj: dict[str, set[str]] = {}

    def link(a, b):
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    by_prov: dict[str, list[str]] = {}
    for k in eu5.land_locations():
        li = eu5.locations[k]
        by_prov.setdefault(li.province or k, []).append(k)
    by_area: dict[str, list[str]] = {}
    for prov, locs in by_prov.items():
        for a, b in zip(locs, locs[1:]):
            link(a, b)
        by_area.setdefault(eu5.locations[locs[0]].area or prov, []).append(locs[0])
    by_region: dict[str, list[str]] = {}
    for area, firsts in by_area.items():
        for a, b in zip(firsts, firsts[1:]):
            link(a, b)
        by_region.setdefault(eu5.locations[firsts[0]].region or area, []).append(firsts[0])
    for region, firsts in by_region.items():
        for a, b in zip(firsts, firsts[1:]):
            link(a, b)
    return adj


def run(out_dir: str, **opt):
    t0 = time.time()
    log = lambda s: print("  " + s)
    ck3 = load_ck3_game(CK3_ROOT, log)
    eu5 = load_eu5_game(EU5_ROOT, log)
    save_path = os.path.join(out_dir, "test.ck3")
    info = make_save(ck3, save_path)
    print("generated save:", info)
    sv = load_save(save_path, log)
    mp = name_mapping(ck3, eu5)
    RUN_MAP[:] = [mp]
    print("mapped locations:", len(mp.loc_to_barony))
    van = load_vanilla(eu5, log)
    RUN_MAP.append(van)
    world = Converter(sv, ck3, eu5, mp, van, Options(mod_name="CK3 Test Conversion", **opt), log,
                      adjacency=fake_adjacency(eu5)).run()
    root = ModWriter(world, eu5, ck3, sv, van, out_dir, log).write()
    print(f"conversion took {time.time() - t0:.1f}s")
    return eu5, ck3, sv, world, root


def validate(eu5, world, root) -> list[str]:
    errs: list[str] = []
    start = os.path.join(root, *eu5.setup_rel)
    parsed = {}
    for fn in sorted(os.listdir(start)):
        parsed[fn] = parse_file(os.path.join(start, fn))
    # 1. ownership unique & valid
    owned: dict[str, str] = {}
    tags = set()
    for tag, cb in parsed["10_countries.txt"].block("countries").block("countries").pairs():
        tags.add(tag)
        for k in ("own_control_core", "own_control_integrated", "own_control_conquered", "own_control_colony",
                  "own_core", "own_conquered"):
            for lst in cb.getall(k):
                for loc in lst.values():
                    if loc in owned:
                        errs.append(f"{loc} owned by {owned[loc]} and {tag}")
                    owned[loc] = tag
                    if loc not in eu5.locations:
                        errs.append(f"{tag} owns unknown location {loc}")
    # 2. tags defined
    ours = parse_file(os.path.join(root, "in_game", "setup", "countries", "zz_save2mod_countries.txt"))
    defined = set(eu5.country_defs) | set(ours.keys())
    for t in tags:
        if t not in defined:
            errs.append(f"country {t} has no definition")
    for t, d in ours.pairs():
        if d.get("culture_definition") not in eu5.cultures or d.get("religion_definition") not in eu5.religions:
            errs.append(f"definition {t}: bad culture/religion")
    # no historic or reserved tags reused/created
    for t in tags:
        if t in eu5.country_defs and eu5.country_defs[t].str("is_historic") == "yes" and t in world.countries:
            errs.append(f"historic tag {t} reused")
    # rulers old enough
    import datetime
    # 3. characters
    chars_order: list[str] = []
    db = parsed["05_characters.txt"].block("character_db")
    seen = set()
    vanilla_db = parse_file(eu5.setup_start("05_characters.txt")).block("character_db")
    vanilla_keys = set(vanilla_db.keys())
    out_keys = set(db.keys())
    for key, cb in db.pairs():
        for ref in ("father", "mother", "spouse"):
            r = cb.get(ref)
            if not isinstance(r, str):
                continue
            if key.startswith("ck3c_") and r not in seen:
                errs.append(f"character {key}: {ref} {r} not defined before")
            if r in vanilla_keys and r not in out_keys:
                errs.append(f"character {key}: {ref} {r} was removed but is still referenced")
        for f, table in (("culture", eu5.cultures), ("religion", eu5.religions)):
            v = cb.get(f)
            if isinstance(v, str) and v not in table:
                errs.append(f"character {key}: bad {f} {v}")
        seen.add(key)
    dyn = set(parsed["04_dynasties.txt"].block("dynasty_manager").keys())
    for key, cb in db.pairs():
        d = cb.get("dynasty")
        if isinstance(d, str) and d not in dyn:
            errs.append(f"character {key}: unknown dynasty {d}")
    for tag, cb in parsed["10_countries.txt"].block("countries").block("countries").pairs():
        gov = cb.block("government")
        for k in (("ruler", "consort", "heir") if tag in world.countries else ()):
            v = gov.get(k)
            if isinstance(v, str) and v != "random" and v not in seen:
                errs.append(f"{tag}: {k} {v} missing from character_db")
        for rt in (gov.getall("ruler_term") if tag in world.countries else []):
            for ch in rt.getall("character"):
                if ch not in seen:
                    errs.append(f"{tag}: ruler_term {ch} missing")
        for f, table in (("culture", eu5.cultures), ("religion", eu5.religions)):
            v = cb.get(f)
            if isinstance(v, str) and v not in table:
                errs.append(f"{tag}: bad {f} {v}")
        gv = cb.block("government").get("ruler")
        if tag in world.countries and gv is None:
            errs.append(f"{tag}: no ruler line")
        for inc in cb.getall("include"):
            if inc not in eu5.templates:
                errs.append(f"{tag}: unknown template {inc}")
    # 4. pops
    for loc, lb in parsed["06_pops.txt"].block("locations").pairs():
        for p in lb.getall("define_pop"):
            if p.get("culture") not in eu5.cultures:
                errs.append(f"pop in {loc}: bad culture {p.get('culture')}")
            if p.get("religion") not in eu5.religions:
                errs.append(f"pop in {loc}: bad religion {p.get('religion')}")
    # 5. buildings
    for bt, bb in parsed["07_cities_and_buildings.txt"].block("building_manager").pairs():
        if bt not in eu5.buildings:
            errs.append(f"unknown building {bt}")
        if bb.get("location") not in eu5.locations:
            errs.append(f"building {bt} in unknown location {bb.get('location')}")
        if bb.get("tag") not in defined:
            errs.append(f"building {bt} owned by undefined {bb.get('tag')}")
        if bb.get("tag") in world.removed_tags:
            errs.append(f"building {bt} owned by removed {bb.get('tag')}")
    # 6. diplomacy
    for k, v in parsed["12_diplomacy.txt"].block("diplomacy_manager").pairs():
        if isinstance(v, Block):
            for f in ("first", "second"):
                t = v.get(f)
                if isinstance(t, str) and t not in tags:
                    errs.append(f"diplomacy {k} references {t} which has no land")
    # 7. IOs
    for k, io in parsed["15_international_organizations.txt"].block("international_organization_manager").pairs():
        if isinstance(io, Block):
            for m in io.block("members").values():
                if m not in tags:
                    errs.append(f"IO {io.get('type')} member {m} has no land")
    # 8. localization & COA for new tags
    yml = open(os.path.join(root, "main_menu", "localization", "english", "zz_save2mod_l_english.yml"),
               encoding="utf-8-sig").read()
    coa = parse_file(os.path.join(root, "main_menu", "common", "coat_of_arms", "coat_of_arms", "zz_save2mod_coa.txt"))
    for t in ours.keys():
        if not re.search(rf"^ {t}:", yml, re.M):
            errs.append(f"no name for {t}")
        if t not in coa:
            errs.append(f"no COA for {t}")
    # 9. the package's own self-check (vanilla rules, vanilla's own quirks ignored)
    from save2mod.validate import check_mod
    problems, _stats = check_mod(root, eu5)
    errs += ["self-check: " + p for p in problems]
    # 10. every other output file parses
    for dp, _d, fs in os.walk(root):
        for f in fs:
            if f.endswith(".txt") and "report" not in f:
                parse_file(os.path.join(dp, f))
    return errs


@pytest.mark.skipif(not (os.path.isdir(CK3_ROOT) and os.path.isdir(EU5_ROOT)),
                    reason="set CK3_GAME and EU5_GAME to the games' game/ folders")
def test_end_to_end():
    with tempfile.TemporaryDirectory() as out:
        eu5, ck3, sv, world, root = run(out)
        errs = validate(eu5, world, root)
        print(open(os.path.join(root, "save2mod_report.txt"), encoding="utf-8").read()[:3000])
        for e in errs[:60]:
            print("ERR", e)
        assert not errs, f"{len(errs)} consistency errors"
        assert world.hre is not None
        # at most one CK3 fort per CK3 county
        forts = {"castle", "city_walls", "stockade"}
        per_cty: dict[str, int] = {}
        for loc, blds in world.ck3_buildings.items():
            n = sum(1 for b in blds if b in forts)
            if n:
                cty = ck3.county_of_barony(RUN_MAP[0].loc_to_barony[loc])
                per_cty[cty] = per_cty.get(cty, 0) + n
        assert per_cty, "the test save should produce some forts"
        assert max(per_cty.values()) == 1, [k for k, v in per_cty.items() if v > 1][:5]
        # realm list
        from save2mod.mappings import update_realm_table, read_table
        update_realm_table(world, eu5)
        rows = read_table("title_tags.csv")
        listed = {r["ck3_title"] for r in rows if r.get("last_conversion")}
        assert {c.title for c in world.countries.values()} <= listed
        assert any(r["ck3_title"] == "e_hre" and r["eu5_tag"] == "-" for r in rows)
        # subject tiers: kingdom default -> no duke subjects; with duchy tier the administrative
        # kingdom's governors still merge while feudal dukes become subjects
        assert not any(c.overlord and c.tier == 2 and not c.exclave_of for c in world.countries.values())
        mp, van = RUN_MAP[0], RUN_MAP[1]
        w2 = Converter(sv, ck3, eu5, mp, van, Options(subject_min_tier=2, split_exclaves=False),
                       lambda s: None).run()
        by_title = {c.title: c for c in w2.countries.values()}
        adm = by_title.get("k_france")
        assert adm is not None and not any(c.overlord == adm.tag for c in w2.countries.values())
        assert any(c.overlord and c.tier == 2 for c in w2.countries.values())
        w3 = Converter(sv, ck3, eu5, mp, van, Options(subject_min_tier=2, split_exclaves=False,
                                                      keep_admin_realms_whole=False), lambda s: None).run()
        adm3 = {c.title: c for c in w3.countries.values()}["k_france"]
        assert any(c.overlord == adm3.tag for c in w3.countries.values()), "toggle off: governors split off"
        ex = [c for c in world.countries.values() if c.exclave_of]
        assert ex, "the test save should produce some exclave vassals"
        for c in ex:
            assert c.overlord == c.exclave_of and c.locations and c.capital in c.locations


if __name__ == "__main__":
    keep = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
    os.makedirs(keep, exist_ok=True)
    eu5, ck3, sv, world, root = run(keep)
    errs = validate(eu5, world, root)
    print(open(os.path.join(root, "save2mod_report.txt"), encoding="utf-8").read()[:4000])
    print(f"{len(errs)} errors")
    for e in errs[:80]:
        print("ERR", e)
