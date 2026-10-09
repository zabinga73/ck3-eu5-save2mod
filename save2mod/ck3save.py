"""Read a CK3 save (.ck3) into a :class:`CK3Save` model.

Only the sections the converter needs are parsed. The gamestate is split into
top-level sections by scanning for identifiers at column 0 (the layout CK3
writes), and each needed section is parsed on its own.

Supported: plain text saves and zip-compressed text saves (the default for
non-ironman games). Binary/ironman saves must first be "melted" to text, e.g.
with the free ``rakaly`` CLI (``rakaly melt save.ck3``); if ``rakaly`` is on
PATH it is used automatically.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable

from .pdx import Block, parse_text

Log = Callable[[str], None]

TIER_OF_PREFIX = {"b": 0, "c": 1, "d": 2, "k": 3, "e": 4, "h": 5}
TIER_NAMES = ["barony", "county", "duchy", "kingdom", "empire", "hegemony"]


class SaveError(Exception):
    pass


# ------------------------------------------------------------------ container
def _extract_gamestate(path: str, log: Log) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if not data:
        raise SaveError("save file is empty")
    zip_at = data.find(b"PK\x03\x04")
    if zip_at != -1:
        try:
            zf = zipfile.ZipFile(io.BytesIO(data[zip_at:]))
            names = zf.namelist()
            name = "gamestate" if "gamestate" in names else names[0]
            gs = zf.read(name)
            log(f"Save is compressed; extracted '{name}' ({len(gs)/1e6:.1f} MB)")
            return gs
        except zipfile.BadZipFile:
            pass
    return data


def _looks_binary(gs: bytes) -> bool:
    sample = gs[:200_000]
    # Text saves are overwhelmingly "key=value" with newlines
    return sample.count(b"=") < 50 or sample.count(b"\n") < 20


def _melt_with_rakaly(path: str, log: Log) -> bytes | None:
    exe = shutil.which("rakaly")
    if not exe:
        return None
    log("Binary (ironman) save detected; melting with rakaly ...")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "melted.ck3")
        try:
            subprocess.run([exe, "melt", "--unknown-key", "stringify", "--to-stdout", path],
                           check=True, stdout=open(out, "wb"), stderr=subprocess.PIPE, timeout=900)
        except Exception as e:  # pragma: no cover - external tool
            log(f"rakaly failed: {e}")
            return None
        with open(out, "rb") as f:
            return f.read()


def load_gamestate_bytes(path: str, log: Log = print) -> bytes:
    gs = _extract_gamestate(path, log)
    # strip header line "SAV0103....\n"
    if gs.startswith(b"SAV"):
        nl = gs.find(b"\n")
        gs = gs[nl + 1:]
    if _looks_binary(gs):
        melted = _melt_with_rakaly(path, log)
        if melted is None:
            raise SaveError(
                "This looks like an ironman/binary save. Either disable ironman, or melt it "
                "to text first with rakaly (https://github.com/rakaly/cli: 'rakaly melt <save>') "
                "and pick the melted file.")
        gs = melted
        if gs.startswith(b"SAV"):
            gs = gs[gs.find(b"\n") + 1:]
    return gs


_TOP = re.compile(rb"^([A-Za-z_][A-Za-z_0-9]*)[ \t]*=", re.M)


def index_sections(gs: bytes) -> dict[str, list[tuple[int, int]]]:
    """Map top-level key -> list of (start, end) byte spans (the value part is
    included; spans cover ``key=...`` up to the next top-level key)."""
    starts = [(m.group(1).decode("ascii"), m.start()) for m in _TOP.finditer(gs)]
    out: dict[str, list[tuple[int, int]]] = {}
    for i, (k, s) in enumerate(starts):
        e = starts[i + 1][1] if i + 1 < len(starts) else len(gs)
        out.setdefault(k, []).append((s, e))
    return out


def _decode(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- model
@dataclass
class Title:
    id: str
    key: str
    tier: int
    holder: str | None = None
    de_facto_liege: str | None = None     # title id
    de_jure_liege: str | None = None      # title id
    capital: str | None = None            # title id (county) or province id
    name: str | None = None               # custom name (player renamed / dynamic)
    adj: str | None = None
    shown_name: str | None = None         # the name CK3 displays (title_name_data)
    shown_adj: str | None = None
    coa_id: str | None = None
    color: tuple[int, int, int] | None = None
    raw: Block | None = None

    @property
    def tier_name(self) -> str:
        return TIER_NAMES[self.tier] if 0 <= self.tier < len(TIER_NAMES) else "?"


@dataclass
class Character:
    id: str
    first_name: str = ""
    nickname: str | None = None
    birth: str | None = None
    female: bool = False
    culture: str | None = None      # culture id (save)
    faith: str | None = None        # faith id (save); rite id in CK3 1.20+
    house: str | None = None        # dynasty_house id
    skills: list[float] = field(default_factory=list)   # dip mar ste int lea pro
    traits: list[str] = field(default_factory=list)     # trait indices (save)
    spouses: list[str] = field(default_factory=list)
    primary_spouse: str | None = None
    children: list[str] = field(default_factory=list)
    father: str | None = None
    mother: str | None = None
    domain: list[str] = field(default_factory=list)     # title ids held
    primary_title: str | None = None
    government: str | None = None
    heir: str | None = None
    alive: bool = True
    gold: float | None = None
    prestige: float | None = None
    piety: float | None = None
    raw: Block | None = None


@dataclass
class County:
    key: str
    development: float | None = None
    control: float | None = None
    culture: str | None = None      # save culture id
    faith: str | None = None        # save faith id; rite id in CK3 1.20+
    raw: Block | None = None


@dataclass
class Barony:
    province: str
    holding: str | None = None           # e.g. castle_holding
    buildings: list[str] = field(default_factory=list)   # CK3 building keys w/ level e.g. barracks_03
    culture: str | None = None
    faith: str | None = None
    raw: Block | None = None


@dataclass
class Culture:
    id: str
    template: str | None
    name: str | None
    heritage: str | None = None
    language: str | None = None
    parents: list[str] = field(default_factory=list)


@dataclass
class Faith:
    id: str
    tag: str | None
    template: str | None
    religion_id: str | None
    religion_tag: str | None = None
    name: str | None = None
    rite: str | None = None         # CK3 1.20+: rite key; the record is then keyed by rite id


@dataclass
class House:
    id: str
    key: str | None
    name: str | None
    dynasty: str | None
    prefix: str | None = None
    coa_id: str | None = None


@dataclass
class CK3Save:
    path: str
    date: str = "1066.9.15"
    version: str | None = None
    player_character: str | None = None
    titles: dict[str, Title] = field(default_factory=dict)          # by id
    title_by_key: dict[str, Title] = field(default_factory=dict)
    characters: dict[str, Character] = field(default_factory=dict)  # living only
    counties: dict[str, County] = field(default_factory=dict)       # by county key
    baronies: dict[str, Barony] = field(default_factory=dict)       # by province id
    cultures: dict[str, Culture] = field(default_factory=dict)
    faiths: dict[str, Faith] = field(default_factory=dict)
    houses: dict[str, House] = field(default_factory=dict)
    dynasty_names: dict[str, str] = field(default_factory=dict)     # dynasty id -> name/key
    coats_of_arms: dict[str, Block] = field(default_factory=dict)
    trait_names: list[str] = field(default_factory=list)            # save trait lookup, if present
    notes: list[str] = field(default_factory=list)

    # helpers ---------------------------------------------------------------
    def holder_of(self, title_id: str | None) -> Character | None:
        if not title_id or title_id not in self.titles:
            return None
        h = self.titles[title_id].holder
        return self.characters.get(h) if h else None


# -------------------------------------------------------------------- loading
NEEDED = ("meta_data", "date", "played_character", "currently_played_characters",
          "landed_titles", "living", "provinces", "county_manager", "culture_manager",
          "religion", "faiths", "rites", "dynasties", "coat_of_arms", "traits_lookup")


def _parse_section(gs: bytes, spans: list[tuple[int, int]]) -> Block:
    """Parse all occurrences of a top-level section and return the value
    Block of the first (or a merged Block for repeated keys)."""
    root = Block()
    for s, e in spans:
        b = parse_text(_decode(gs[s:e]))
        root.items.extend(b.items)
    return root


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ids(v: Any) -> list[str]:
    if isinstance(v, Block):
        return [x for x in v.values() if isinstance(x, str)]
    if isinstance(v, str):
        return [v]
    return []


def _first_id(v) -> str | None:
    if isinstance(v, str):
        return v
    if isinstance(v, Block):
        vals = [x for x in v.values() if isinstance(x, str)]
        return vals[0] if vals else None
    return None


def _first_str(b: Block, *keys: str) -> str | None:
    for k in keys:
        v = b.get(k)
        if isinstance(v, str):
            return v
    return None


def load_save(path: str, log: Log = print, *, want_coas: bool = True) -> CK3Save:
    gs = load_gamestate_bytes(path, log)
    sec = index_sections(gs)
    log(f"Found {len(sec)} top-level sections")
    sv = CK3Save(path=path)

    def section(name: str) -> Block | None:
        if name not in sec:
            return None
        return _parse_section(gs, sec[name])

    # ---- meta / date
    top_date = section("date")
    if top_date is not None:
        sv.date = top_date.str("date") or sv.date
    meta = section("meta_data")
    if meta is not None:
        m = meta.block("meta_data")
        sv.version = m.str("version")
        if top_date is None:
            sv.date = m.str("meta_date") or sv.date
    pc = section("played_character")
    if pc is not None:
        p = pc.block("played_character")
        sv.player_character = p.str("character")
    log(f"Save date {sv.date}, version {sv.version or '?'}")

    # ---- traits lookup (newer saves store an index -> name table)
    tl = section("traits_lookup")
    if tl is not None:
        sv.trait_names = [str(x) for x in tl.block("traits_lookup").values()]

    # ---- landed titles
    lt = section("landed_titles")
    if lt is None:
        raise SaveError("save has no landed_titles section - is this a CK3 save?")
    titles_block = lt.block("landed_titles")
    inner = titles_block.get("landed_titles")
    if isinstance(inner, Block):
        titles_block = inner
    for tid, tb in titles_block.pairs():
        if not isinstance(tb, Block):
            continue
        key = tb.str("key")
        if not key:
            continue
        prefix = key.split("_", 1)[0]
        tier = TIER_OF_PREFIX.get(prefix, -1)
        if prefix == "x":
            # dynamic title (mercenaries, holy orders, custom). custom realms carry a tier
            t = tb.str("tier") or ""
            tier = {"barony": 0, "county": 1, "duchy": 2, "kingdom": 3, "empire": 4, "hegemony": 5}.get(t, -1)
        color = None
        c = tb.get("color")
        if c is not None:
            vals = c.block.values() if hasattr(c, "block") else (c.values() if isinstance(c, Block) else [])
            try:
                if len(vals) >= 3:
                    f = [float(x) for x in vals[:3]]
                    if max(f) <= 1.0:
                        f = [x * 255 for x in f]
                    color = tuple(int(round(x)) for x in f)  # type: ignore[assignment]
            except ValueError:
                pass
        title = Title(
            id=tid, key=key, tier=tier,
            holder=tb.str("holder"),
            de_facto_liege=tb.str("de_facto_liege") or tb.str("liege"),
            de_jure_liege=tb.str("de_jure_liege"),
            capital=tb.str("capital"),
            name=tb.str("name"), adj=tb.str("adj") or tb.str("adjective"),
            coa_id=tb.str("coat_of_arms_id"),
            color=color, raw=tb,
            shown_name=tb.path("title_name_data", "name"), shown_adj=tb.path("title_name_data", "adj"),
        )
        sv.titles[tid] = title
        sv.title_by_key.setdefault(key, title)
    log(f"Titles: {len(sv.titles)}")

    # ---- living characters (we keep all; only a fraction gets used)
    liv = section("living")
    if liv is None:
        raise SaveError("save has no 'living' section")
    for cid, cb in liv.block("living").pairs():
        if not isinstance(cb, Block):
            continue
        ch = Character(id=cid, raw=cb)
        ch.first_name = cb.str("first_name") or ""
        ch.nickname = cb.str("nickname") or cb.str("nickname_text")
        ch.birth = cb.str("birth")
        ch.female = cb.str("female") == "yes"
        ch.culture = cb.str("culture")
        ch.faith = cb.str("faith") or cb.str("rite")
        ch.house = cb.str("dynasty_house") or cb.str("house")
        sk = cb.get("skill")
        if isinstance(sk, Block):
            ch.skills = [(_num(x) or 0.0) for x in sk.values()]
        tr = cb.get("traits")
        if isinstance(tr, Block):
            ch.traits = [str(x) for x in tr.values()]
        fam = cb.block("family_data")
        ch.primary_spouse = fam.str("primary_spouse")
        ch.spouses = _ids(fam.get("spouse"))
        if ch.primary_spouse and ch.primary_spouse not in ch.spouses:
            ch.spouses.insert(0, ch.primary_spouse)
        ch.children = _ids(fam.get("child"))
        ch.father = fam.str("real_father") or cb.str("father")
        ch.mother = cb.str("mother")
        ld = cb.block("landed_data")
        ch.domain = _ids(ld.get("domain"))
        ch.government = ld.str("government") or cb.str("government")
        ch.primary_title = ld.str("primary_title")
        ad = cb.block("alive_data")
        ch.gold = _num(ad.get("gold")) if not isinstance(ad.get("gold"), Block) else _num(ad.block("gold").get("value"))
        pr = ad.get("prestige")
        ch.prestige = _num(pr.get("currency")) if isinstance(pr, Block) else _num(pr)
        pi = ad.get("piety")
        ch.piety = _num(pi.get("currency")) if isinstance(pi, Block) else _num(pi)
        # CK3 1.19: landed_data.succession = line of succession, first = primary
        # heir. (alive_data.heir holds *title* ids the character is heir to.)
        ch.heir = _first_id(ld.get("succession"))
        sv.characters[cid] = ch
    log(f"Living characters: {len(sv.characters)}")

    # parents: CK3 stores children on the parents; invert so children know them
    for ch in sv.characters.values():
        for kid in ch.children:
            k = sv.characters.get(kid)
            if k is None:
                continue
            if ch.female:
                k.mother = k.mother or ch.id
            else:
                k.father = k.father or ch.id

    # ---- provinces (baronies): holding + buildings
    prov = section("provinces")
    if prov is not None:
        for pid, pb in prov.block("provinces").pairs():
            if not isinstance(pb, Block):
                continue
            bar = Barony(province=pid, raw=pb)
            hold = pb.get("holding")
            if isinstance(hold, Block):
                bar.holding = hold.str("type")
                blds = hold.get("buildings")
                if isinstance(blds, Block):
                    for _k, _op, bv in blds.items:
                        if isinstance(bv, Block):
                            t = bv.str("type")
                            if t:
                                bar.buildings.append(t)
                        elif isinstance(bv, str) and bv:
                            bar.buildings.append(bv)
                sb = hold.get("special_building") or hold.get("special_building_type")
                if isinstance(sb, str):
                    bar.buildings.append(sb)
                elif isinstance(sb, Block) and sb.str("type"):
                    bar.buildings.append(sb.str("type"))
                for dup in hold.getall("building"):
                    if isinstance(dup, Block) and dup.str("type"):
                        bar.buildings.append(dup.str("type"))
            bar.culture = pb.str("culture")
            bar.faith = pb.str("faith") or pb.str("rite") or pb.str("religion")
            sv.baronies[pid] = bar
    log(f"Provinces with data: {len(sv.baronies)}")

    # ---- county manager
    cm = section("county_manager")
    if cm is not None:
        counties = cm.path("county_manager", "counties")
        if not isinstance(counties, Block):
            counties = cm.block("county_manager")
        for ckey, cb in counties.pairs():
            if not isinstance(cb, Block):
                continue
            key = ckey
            if not key.startswith("c_") and ckey in sv.titles:
                key = sv.titles[ckey].key
            cty = County(key=key, raw=cb)
            cty.development = _num(cb.get("development")) if not isinstance(cb.get("development"), Block) \
                else _num(cb.block("development").get("value"))
            for ck in ("county_control", "control"):
                v = cb.get(ck)
                if v is not None:
                    cty.control = _num(v) if not isinstance(v, Block) else _num(v.get("value"))
                    break
            cty.culture = cb.str("culture")
            cty.faith = cb.str("faith") or cb.str("rite") or cb.str("religion")
            sv.counties[key] = cty
    # county data may also live on the title itself
    for t in sv.titles.values():
        if t.tier != 1 or t.raw is None:
            continue
        cty = sv.counties.get(t.key)
        if cty is None:
            cty = County(key=t.key)
            sv.counties[t.key] = cty
        r = t.raw
        if cty.development is None:
            cty.development = _num(r.get("development")) if isinstance(r.get("development"), str) else None
        if cty.control is None:
            for ck in ("county_control", "control"):
                if isinstance(r.get(ck), str):
                    cty.control = _num(r.get(ck))
                    break
        cty.culture = cty.culture or r.str("culture")
        cty.faith = cty.faith or r.str("faith") or r.str("rite")
    have_dev = sum(1 for c in sv.counties.values() if c.development is not None)
    log(f"Counties: {len(sv.counties)} ({have_dev} with development)")
    if have_dev == 0:
        sv.notes.append("No county development found in save; development will fall back to EU5 values.")

    # ---- cultures
    cu = section("culture_manager")
    if cu is not None:
        cults = cu.path("culture_manager", "cultures")
        if isinstance(cults, Block):
            for cid, cb in cults.pairs():
                if not isinstance(cb, Block):
                    continue
                sv.cultures[cid] = Culture(
                    id=cid, template=cb.str("culture_template") or cb.str("template"),
                    name=cb.str("name"), heritage=cb.str("heritage"), language=cb.str("language"),
                    parents=_ids(cb.get("parents")))
    log(f"Cultures: {len(sv.cultures)}")

    # ---- religion / faiths
    rel = section("religion")
    if rel is not None:
        r = rel.block("religion")
        rel_tags: dict[str, str] = {}
        rels = r.get("religions")
        if isinstance(rels, Block):
            for rid, rb in rels.pairs():
                if isinstance(rb, Block):
                    rel_tags[rid] = rb.str("tag") or rb.str("template") or ""
        fs = r.get("faiths")
        if isinstance(fs, Block):
            for fid, fb in fs.pairs():
                if not isinstance(fb, Block):
                    continue
                f = Faith(id=fid, tag=fb.str("tag"), template=fb.str("template"),
                          religion_id=fb.str("religion"), name=fb.str("name"))
                if f.religion_id:
                    f.religion_tag = rel_tags.get(f.religion_id)
                sv.faiths[fid] = f
        # CK3 1.20: religion -> faith -> rite, each in its own section; characters
        # and counties point at a rite, so the records are keyed by rite id
        fa, ri = section("faiths"), section("rites")
        fdb = fa.path("faiths", "database") if fa is not None else None
        rdb = ri.path("rites", "database") if ri is not None else None
        if isinstance(fdb, Block) and isinstance(rdb, Block):
            faith_info: dict[str, tuple[str | None, str | None, str | None]] = {}
            for fid, fb in fdb.pairs():
                if isinstance(fb, Block):
                    faith_info[fid] = (fb.str("faith_type") or fb.str("tag"), fb.str("religion"), fb.str("name"))
            for rid, rb in rdb.pairs():
                if not isinstance(rb, Block):
                    continue
                ftype, relid, fname = faith_info.get(rb.str("faith") or "", (None, None, None))
                f = Faith(id=rid, tag=ftype, template=None, religion_id=relid,
                          name=rb.path("data", "name") or fname, rite=rb.str("rite_type"))
                f.religion_tag = rel_tags.get(relid or "")
                sv.faiths[rid] = f
    log(f"Faiths: {len(sv.faiths)}")

    # ---- dynasties / houses
    dy = section("dynasties")
    if dy is not None:
        d = dy.block("dynasties")
        dh = d.get("dynasty_house")
        if isinstance(dh, Block):
            for hid, hb in dh.pairs():
                if isinstance(hb, Block):
                    sv.houses[hid] = House(id=hid, key=hb.str("key"), name=hb.str("name") or hb.str("localized_name"),
                                           dynasty=hb.str("dynasty"), prefix=hb.str("prefix"),
                                           coa_id=hb.str("coat_of_arms_id"))
        dd = d.get("dynasties")
        if isinstance(dd, Block):
            for did, db in dd.pairs():
                if isinstance(db, Block):
                    nm = db.str("name") or db.str("localized_name") or db.str("key")
                    if nm:
                        sv.dynasty_names[did] = nm
    log(f"Houses: {len(sv.houses)}")

    # ---- coats of arms (optional; large)
    if want_coas and "coat_of_arms" in sec:
        coa = section("coat_of_arms")
        db = coa.path("coat_of_arms", "coat_of_arms_manager_database")
        if isinstance(db, Block):
            for cid, cb in db.pairs():
                if isinstance(cb, Block):
                    sv.coats_of_arms[cid] = cb
        log(f"Coats of arms: {len(sv.coats_of_arms)}")

    # ---- derive primary titles / heirs from titles
    _derive_primary_titles(sv)
    _derive_heirs(sv)
    return sv


def _derive_primary_titles(sv: CK3Save) -> None:
    # vassal titles point (de facto) at the liege's primary title: count votes
    votes: dict[str, int] = {}
    for t in sv.titles.values():
        if t.de_facto_liege:
            votes[t.de_facto_liege] = votes.get(t.de_facto_liege, 0) + 1
    held: dict[str, list[Title]] = {}
    for t in sv.titles.values():
        if t.holder and t.tier >= 0:
            held.setdefault(t.holder, []).append(t)
    for cid, ch in sv.characters.items():
        ts = held.get(cid, [])
        if not ts:
            continue
        if ch.primary_title and ch.primary_title in sv.titles:
            continue
        ts_sorted = sorted(ts, key=lambda t: (t.tier, votes.get(t.id, 0), -int(t.id) if t.id.isdigit() else 0), reverse=True)
        ch.primary_title = ts_sorted[0].id
        if not ch.domain:
            ch.domain = [t.id for t in ts]


def _derive_heirs(sv: CK3Save) -> None:
    for t in sv.titles.values():
        if t.raw is None or not t.holder:
            continue
        h = t.raw.get("heir")
        hid = None
        if isinstance(h, Block):
            v = h.values()
            hid = v[0] if v else None
        elif isinstance(h, str):
            hid = h
        if hid:
            ch = sv.characters.get(t.holder)
            if ch is not None and ch.primary_title == t.id and not ch.heir:
                ch.heir = hid


def summarize_structure(path: str, out: Callable[[str], None] = print, sample_entries: int = 2) -> None:
    """Print the top-level layout of a save and parsed samples of the parts the
    converter reads. Paste the output in a bug report."""
    from .pdx import dump
    gs = load_gamestate_bytes(path, out)
    sec = index_sections(gs)
    out("Top-level sections (key: occurrences, MB):")
    for k, spans in sorted(sec.items(), key=lambda kv: kv[1][0][0]):
        size = sum(e - s for s, e in spans) / 1e6
        out(f"  {k}: {len(spans)}x, {size:.2f} MB")

    def show(title: str, blk, n: int = sample_entries, pick=None, max_lines: int = 45):
        out(f"\n== {title}")
        if not isinstance(blk, Block):
            out("   (missing)")
            return
        shown = 0
        for k, v in blk.pairs():
            if pick is not None and not pick(k, v):
                continue
            text = dump(Block([(k, "=", v)])).splitlines()
            if len(text) > max_lines:
                text = text[:max_lines] + [f"   ... ({len(text) - max_lines} more lines)"]
            for ln in text:
                out("   " + ln[:180])
            shown += 1
            if shown >= n:
                break
        if shown == 0:
            out("   (no matching entries)")

    def sect(name):
        return _parse_section(gs, sec[name]).get(name) if name in sec else None

    for name in ("meta_data", "played_character"):
        b = sect(name)
        show(name, Block([(name, "=", b)]) if isinstance(b, Block) else None, 1, max_lines=25)
    lt = sect("landed_titles")
    inner = lt.get("landed_titles") if isinstance(lt, Block) else None
    for tier in ("e_", "k_", "d_", "c_", "b_"):
        show(f"landed_titles: first {tier}* title", inner, 1,
             pick=lambda k, v, t=tier: isinstance(v, Block) and str(v.get("key", "")).startswith(t))
    liv = sect("living")
    show("living: a landed character", liv, 1, pick=lambda k, v: isinstance(v, Block) and "landed_data" in v,
         max_lines=80)
    show("living: an unlanded character", liv, 1, pick=lambda k, v: isinstance(v, Block) and "landed_data" not in v)
    del liv
    show("provinces: holdings with buildings", sect("provinces"), sample_entries,
         pick=lambda k, v: isinstance(v, Block) and isinstance(v.get("holding"), Block)
         and "type" in v.get("holding"))
    cm = sect("county_manager")
    show("county_manager.counties", cm.get("counties") if isinstance(cm, Block) else None)
    cu = sect("culture_manager")
    cults = cu.get("cultures") if isinstance(cu, Block) else None
    show("culture_manager: templated culture", cults, 1, pick=lambda k, v: isinstance(v, Block) and "culture_template" in v,
         max_lines=15)
    show("culture_manager: hybrid/divergent culture", cults, 1,
         pick=lambda k, v: isinstance(v, Block) and "culture_template" not in v, max_lines=25)
    rel = sect("religion")
    show("religion.religions", rel.get("religions") if isinstance(rel, Block) else None, 1, max_lines=20)
    if isinstance(rel, Block) and rel.get("faiths") is not None:
        show("religion.faiths", rel.get("faiths"), sample_entries, max_lines=30)
    else:                                   # CK3 1.20+: own sections
        for name in ("faiths", "rites"):
            s = sect(name)
            show(f"{name}.database", s.get("database") if isinstance(s, Block) else None, 1, max_lines=30)
    dy = sect("dynasties")
    show("dynasties.dynasty_house", dy.get("dynasty_house") if isinstance(dy, Block) else None, sample_entries)
    show("dynasties.dynasties", dy.get("dynasties") if isinstance(dy, Block) else None, sample_entries)
