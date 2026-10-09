"""Build a plausible CK3 save (zipped, like a real non-ironman save) from the
CK3 game's own title hierarchy. Used for end-to-end tests without a real save.

Structure mirrors CK3 1.x saves: meta_data, date, played_character,
landed_titles, living, provinces, county_manager, culture_manager, religion,
dynasties, coat_of_arms.
"""
from __future__ import annotations

import io
import random
import zipfile

CULTURE_BY_KINGDOM = {
    "k_france": "french", "k_england": "english", "k_scotland": "scottish", "k_ireland": "irish",
    "k_castille": "castilian", "k_aragon": "catalan", "k_portugal": "portuguese", "k_leon": "asturleonese",
    "k_navarra": "basque", "k_germany": "german", "k_bavaria": "bavarian", "k_saxony": "saxon",
    "k_frisia": "frisian", "k_bohemia": "czech", "k_poland": "polish", "k_hungary": "hungarian",
    "k_italy": "italian", "k_sicily": "sicilian", "k_denmark": "danish", "k_norway": "norwegian",
    "k_sweden": "swedish", "k_serbia": "serbian", "k_croatia": "croatian", "k_bulgaria": "bulgarian",
    "k_thessalonika": "greek", "k_epirus": "greek", "k_hellas": "greek", "k_nikaea": "greek",
    "k_egypt": "egyptian", "k_jerusalem": "levantine", "k_syria": "levantine", "k_persia": "persian",
    "k_georgia": "georgian", "k_armenia": "armenian", "k_rus": "russian", "k_novgorod": "ilmenian",
    "k_lithuania": "lithuanian", "k_finland": "finnish", "k_wales": "welsh", "k_brittany": "breton",
    "k_burgundy": "french", "k_aquitaine": "occitan", "k_lotharingia": "dutch", "k_venice": "italian",
}
HRE_KINGDOM = "k_bavaria"
ADMIN_KINGDOM = "k_france"      # king and dukes get CK3's administrative government
HRE_VASSAL_KINGDOMS = ("k_bohemia", "k_italy", "k_burgundy", "k_lotharingia", "k_saxony")
FAITH_BY_CULTURE_FAMILY = {
    "egyptian": "ashari", "levantine": "ashari", "persian": "imami", "georgian": "orthodox",
    "armenian": "armenian_apostolic", "greek": "orthodox", "serbian": "orthodox", "bulgarian": "orthodox",
    "russian": "orthodox", "ilmenian": "slavic_pagan", "lithuanian": "baltic_pagan", "finnish": "finnish_pagan",
}


def _blk(d, ind=1):
    t = "\t" * ind
    out = []
    for k, v in d:
        if isinstance(v, list) and v and isinstance(v[0], tuple):
            out.append(f"{t}{k}={{\n{_blk(v, ind + 1)}{t}}}\n")
        elif isinstance(v, list):
            out.append(f"{t}{k}={{ {' '.join(str(x) for x in v)} }}\n")
        else:
            out.append(f"{t}{k}={v}\n")
    return "".join(out)


def make_save(ck3, path: str, *, seed: int = 1, kingdoms: list[str] | None = None, date="1180.3.1") -> dict:
    rnd = random.Random(seed)
    titles = ck3.titles
    kingdoms = kingdoms or sorted(k for k, t in titles.items() if t.tier == 3 and t.children)
    tid = 0
    title_ids: dict[str, int] = {}
    t_entries: list[tuple[int, list]] = []
    chars: dict[int, list] = {}
    char_meta: dict[int, dict] = {}
    next_char = [1000]
    counties_data = []
    provinces = []
    houses = []
    cultures: dict[str, int] = {}
    faiths: dict[str, int] = {}

    def cult(key):
        if key not in cultures:
            cultures[key] = len(cultures)
        return cultures[key]

    def faith(key):
        if key not in faiths:
            faiths[key] = len(faiths)
        return faiths[key]

    def new_title(key, holder, liege_tid=None):
        nonlocal tid
        i = tid
        tid += 1
        title_ids[key] = i
        e = [("key", f'"{key}"'), ("holder", holder)]
        if liege_tid is not None:
            e.append(("de_facto_liege", liege_tid))
        e.append(("coat_of_arms_id", 5000 + i))
        t_entries.append((i, e))
        return i

    def new_char(culture, fth, house, female=False, age=35, skills=None):
        cid = next_char[0]
        next_char[0] += 1
        y = int(date.split(".")[0]) - age
        names_m = ["Robert", "Henri", "Otto", "Konrad", "Alfonso", "Sancho", "Pietro", "Stefan", "Bela", "Knut"]
        names_f = ["Adela", "Matilda", "Urraca", "Agnes", "Sophia", "Irene", "Constance", "Beatrix"]
        fn = rnd.choice(names_f if female else names_m)
        sk = skills or [rnd.randint(2, 20) for _ in range(6)]
        chars[cid] = [("first_name", f'"{fn}"'), ("birth", f"{y}.{rnd.randint(1, 12)}.{rnd.randint(1, 28)}")]
        if female:
            chars[cid].append(("female", "yes"))
        chars[cid] += [("culture", culture), ("faith", fth), ("dynasty_house", house), ("skill", sk)]
        char_meta[cid] = {"female": female, "family": [], "landed": None, "children": []}
        return cid

    def new_house(name):
        hid = 7000 + len(houses)
        houses.append((hid, name))
        return hid

    def county_bars(c):
        return [b for b in titles[c].children if titles[b].tier == 0 and titles[b].province]

    player = None
    hre_emperor = None
    admin_chars: set = set()
    for kn in kingdoms:
        ck = CULTURE_BY_KINGDOM.get(kn)
        if ck is None:
            # pick any culture of the right flavour deterministically
            ck = rnd.choice(sorted(ck3.culture_heritage))
        cfam = FAITH_BY_CULTURE_FAMILY.get(ck, "catholic")
        c_id, f_id = cult(ck), faith(cfam)
        duchies = [d for d in titles[kn].children if titles[d].tier == 2]
        counties = [c for d in duchies for c in titles[d].children if titles[c].tier == 1 and county_bars(c)]
        if not counties:
            continue
        house = new_house(f"dynn_{kn[2:]}")
        king = new_char(c_id, f_id, house, age=rnd.randint(25, 60))
        queen = new_char(c_id, f_id, new_house(f"dynn_{kn[2:]}_q"), female=True, age=30)
        heir = new_char(c_id, f_id, house, age=12)
        chars[king].append(("family_data", [("primary_spouse", queen), ("spouse", queen), ("child", [heir])]))
        chars[queen].append(("family_data", [("primary_spouse", king), ("spouse", king), ("child", [heir])]))
        gov = rnd.choice(["feudal_government"] * 6 + ["clan_government", "republic_government"])
        if kn == ADMIN_KINGDOM:
            gov = "administrative_government"
        laws = rnd.choice(["male_preference_law", "male_only_law", "equal_law"])
        succ = rnd.choice(["single_heir_succession_law", "partition_succession_law", "feudal_elective_succession_law"])
        k_tid = new_title(kn, king)
        if kn == HRE_KINGDOM:
            hre_emperor = king
            new_title("e_hre", king)
        domain = [k_tid]
        player = player or king
        for di, d in enumerate(duchies):
            dcs = [c for c in titles[d].children if titles[c].tier == 1 and county_bars(c)]
            if not dcs:
                continue
            if di == 0:
                duke = king
                d_tid = new_title(d, king, None)
                domain.append(d_tid)
            else:
                duke = new_char(c_id, f_id, new_house(f"dynn_{d[2:]}"))
                d_tid = new_title(d, duke, k_tid)
                char_meta[duke]["landed"] = [d_tid]
                if kn == ADMIN_KINGDOM:
                    admin_chars.add(duke)
            for ci, c in enumerate(dcs):
                if ci == 0 or (duke == king and ci < 2):
                    holder = duke
                    lt = d_tid if duke != king else k_tid
                    c_tid = new_title(c, holder, None if holder == king else d_tid)
                    if holder == king:
                        domain.append(c_tid)
                    else:
                        char_meta[duke]["landed"].append(c_tid)
                else:
                    count = new_char(c_id, f_id, new_house(f"dynn_{c[2:]}"))
                    c_tid = new_title(c, count, d_tid if duke != king else k_tid)
                    char_meta[count]["landed"] = [c_tid]
                dev = round(rnd.uniform(3, 45), 2)
                ctrl = round(rnd.uniform(40, 100), 1)
                counties_data.append((c, [("development", dev), ("county_control", ctrl), ("culture", c_id),
                                          ("faith", f_id)]))
                for bi, b in enumerate(county_bars(c)):
                    pid = titles[b].province
                    hold = "castle_holding" if bi == 0 else rnd.choice(["city_holding", "church_holding", "castle_holding"])
                    blds = rnd.sample(["barracks_03", "curtain_walls_04", "curtain_walls_01", "cereal_fields_02",
                                       "market_villages_02", "temple_02", "guild_halls_01", "hunting_grounds_01",
                                       "workshops_03", "logging_camps_05", "allaq_mines_02", "weird_unknown_01",
                                       "curtain_walls_06", "walls_05", "palisades_04", "watchtowers_06"], 3)
                    provinces.append((pid, [("holding", [("type", f'"{hold}"'),
                                                          ("buildings", "{ " + " ".join(f'{{ type="{x}" }}' for x in blds) + " { } }")])]))
        char_meta[king]["landed"] = domain
        chars[king].append(("landed_data", [("domain", domain), ("realm_capital", domain[-1]),
                                            ("succession", [heir, queen]),
                                            ("government", f'"{gov}"'), ("laws", f'{{ "{succ}" "{laws}" }}')]))
    # HRE: make kingdoms of Bohemia/Italy vassals of the emperor
    if hre_emperor is not None:
        emp_tid = title_ids["e_hre"]
        for kn in HRE_VASSAL_KINGDOMS:
            if kn in title_ids:
                for i, e in t_entries:
                    if i == title_ids[kn]:
                        e.append(("de_facto_liege", emp_tid))
        # and a direct-vassal count
        for i, e in t_entries:
            if i == title_ids.get(HRE_KINGDOM):
                e.append(("de_facto_liege", emp_tid))
    for cid, meta in char_meta.items():
        if meta["landed"] and not any(k == "landed_data" for k, _ in chars[cid]):
            ld = [("domain", meta["landed"])]
            if cid in admin_chars:
                ld.append(("government", '"administrative_government"'))
            chars[cid].append(("landed_data", ld))

    # ---------------- text
    parts = []
    parts.append('meta_data={\n\tsave_game_version=7\n\tversion="1.19.0.6"\n\tmeta_date=%s\n\tironman=no\n}\n' % date)
    parts.append(f"date={date}\n")
    parts.append(f"played_character={{\n\tcharacter={player}\n}}\n")
    lt = "landed_titles={\n\tlanded_titles={\n"
    for i, e in t_entries:
        lt += f"{i}={{\n" + _blk([x for x in e if x], 1) + "}\n"
    lt += "\t}\n}\n"
    parts.append(lt)
    liv = "living={\n"
    for cid, e in chars.items():
        liv += f"\t{cid}={{\n" + _blk(e, 2) + "\t}\n"
    liv += "}\n"
    parts.append(liv)
    pv = "provinces={\n"
    for pid, e in provinces:
        pv += f"\t{pid}={{\n" + _blk(e, 2) + "\t}\n"
    parts.append(pv + "}\n")
    cm = "county_manager={\n\tcounties={\n"
    for c, e in counties_data:
        cm += f"\t\t{c}={{\n" + _blk(e, 3) + "\t\t}\n"
    parts.append(cm + "\t}\n}\n")
    cu = "culture_manager={\n\tcultures={\n"
    for k, i in cultures.items():
        cu += f'\t\t{i}={{\n\t\t\tculture_template="{k}"\n\t\t\tname="{k}"\n\t\t\theritage="{ck3.culture_heritage.get(k, "")}"\n\t\t}}\n'
    # a hybrid culture without a template
    cu += f'\t\t{len(cultures)}={{\n\t\t\tname="Franco-Test"\n\t\t\tparents={{ 0 }}\n\t\t}}\n'
    parts.append(cu + "\t}\n}\n")
    rl = "religion={\n\treligions={\n\t\t0={\n\t\t\ttag=\"christianity_religion\"\n\t\t}\n\t}\n\tfaiths={\n"
    for k, i in faiths.items():
        rl += f'\t\t{i}={{\n\t\t\ttemplate="{k}"\n\t\t\ttag="{k}"\n\t\t\treligion=0\n\t\t}}\n'
    parts.append(rl + "\t}\n}\n")
    dy = "dynasties={\n\tdynasty_house={\n"
    for hid, name in houses:
        dy += f'\t\t{hid}={{\n\t\t\tname="{name}"\n\t\t\tdynasty={hid}\n\t\t}}\n'
    dy += "\t}\n\tdynasties={\n"
    for hid, name in houses:
        dy += f'\t\t{hid}={{\n\t\t\tkey="{name}"\n\t\t}}\n'
    parts.append(dy + "\t}\n}\n")
    coa = "coat_of_arms={\n\tcoat_of_arms_manager_database={\n"
    for i, _e in t_entries:
        coa += (f'\t\t{5000 + i}={{\n\t\t\tpattern="pattern_solid.dds"\n\t\t\tcolor1=red\n\t\t\tcolor2=white\n'
                f'\t\t\tcolored_emblem={{\n\t\t\t\tcolor1=white\n\t\t\t\ttexture="ce_lion.dds"\n'
                f'\t\t\t\tinstance={{ position={{ 0.5 0.5 }} scale={{ 0.8 0.8 }} }}\n\t\t\t}}\n\t\t}}\n')
    parts.append(coa + "\t}\n}\n")
    gamestate = "".join(parts).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("gamestate", gamestate)
    with open(path, "wb") as fh:
        fh.write(b"SAV0103aaaaaaaa00000000\n")
        fh.write(buf.getvalue())
    return {"titles": len(t_entries), "characters": len(chars), "counties": len(counties_data),
            "emperor": hre_emperor, "player": player}
