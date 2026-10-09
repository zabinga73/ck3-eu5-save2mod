"""Approximate the skills CK3 shows on screen. The save stores only base
skills; CK3 adds bonuses at runtime. This adds the flat, unconditional ones
that can be read from the game files and the save:

traits, a ruler's living primary spouse (+1 to each skill), holy sites held by the
character's faith (and the holy-site county's own holder bonus), timed
character modifiers, owned artifacts, culture traditions, faith doctrines,
dynasty legacies, special buildings in the character's own baronies and the
current lifestyle focus. Court positions, court types and anything scaled or
behind a condition are left out.
"""
from __future__ import annotations

import glob
import os
import time

from .pdx import Block, parse_file

SKILLS = ("diplomacy", "martial", "stewardship", "intrigue", "learning")
Vec = tuple[float, float, float, float, float]


def _vec(b) -> Vec | None:
    """Flat skill values directly inside a modifier block."""
    if not isinstance(b, Block):
        return None
    out = []
    for s in SKILLS:
        x = b.get(s)
        try:
            out.append(float(x) if isinstance(x, str) else 0.0)
        except ValueError:          # @values, script values
            out.append(0.0)
    return tuple(out) if any(out) else None    # type: ignore[return-value]


def _files(root: str, *parts: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "common", *parts, "**", "*.txt"), recursive=True))


def _add(a: list[float], v: Vec | None, n: float = 1.0) -> None:
    if v:
        for i, x in enumerate(v):
            a[i] += x * n


class SkillModel:
    def __init__(self, ck3, save, log=print):
        t0 = time.time()
        self.ck3, self.sv = ck3, save
        root = ck3.root
        self.modifiers: dict[str, Vec] = {}
        for f in _files(root, "modifiers"):
            for k, v in parse_file(f).pairs():
                vec = _vec(v)
                if vec:
                    self.modifiers[k] = vec
        # blocks with a character_modifier: traditions, doctrines, dynasty legacies, buildings
        self.traditions = self._char_mods(_files(root, "culture", "traditions"))
        self.doctrines = self._char_mods(_files(root, "religion", "doctrine_types"))
        self.legacies = self._char_mods(_files(root, "dynasty_perks"))
        self.buildings = self._char_mods(_files(root, "buildings"), ("character_modifier",
                                                                     "county_holder_character_modifier"))
        self.focuses: dict[str, Vec] = {}
        for f in _files(root, "focuses"):
            for k, v in parse_file(f).pairs():
                vec = _vec(v.get("modifier")) if isinstance(v, Block) else None
                if vec:
                    self.focuses[k] = vec
        # holy site type -> (county, faith-wide bonus, county holder bonus)
        self.holy_sites: dict[str, tuple[str | None, Vec | None, Vec | None]] = {}
        for f in _files(root, "religion", "holy_site_types"):
            for k, v in parse_file(f).pairs():
                if isinstance(v, Block):
                    self.holy_sites[k] = (v.str("county"), _vec(v.get("faith_character_modifier")),
                                          _vec(v.get("county_holder_character_modifier")))
        # baronies held by each character (for building bonuses)
        self.baronies_of: dict[str, list[str]] = {}
        for t in save.titles.values():
            if t.tier == 0 and t.holder:
                self.baronies_of.setdefault(t.holder, []).append(t.key)
        self._held_sites: dict[str, list[str]] = {}
        log(f"CK3 skill bonuses: read in {time.time() - t0:.1f}s")

    @staticmethod
    def _char_mods(files: list[str], keys=("character_modifier",)) -> dict[str, Vec]:
        out: dict[str, Vec] = {}

        def walk(b: Block, depth: int) -> None:
            for k, v in b.pairs():
                if not isinstance(v, Block):
                    continue
                tot = [0.0] * 5
                for mk in keys:
                    for m in v.getall(mk):
                        _add(tot, _vec(m))
                if any(tot):
                    out[k] = tuple(tot)          # type: ignore[assignment]
                elif depth < 2:                  # doctrines are nested in their group
                    walk(v, depth + 1)
        for f in files:
            walk(parse_file(f), 0)
        return out

    # ------------------------------------------------------------------
    def _faith_key(self, cid: str | None) -> str | None:
        ch = self.sv.characters.get(cid) if cid else None
        f = self.sv.faiths.get(ch.faith) if ch is not None and ch.faith else None
        return (f.tag or f.template) if f is not None else None

    def bonus(self, ch) -> list[float]:
        """Skill bonuses (dip mar ste int lea) on top of the character's base skills."""
        sv = self.sv
        out = [0.0] * 5
        for t in ch.traits:
            name = sv.trait_names[int(t)] if t.isdigit() and int(t) < len(sv.trait_names) else t
            _add(out, self.ck3.trait_skills.get(name))
        raw = ch.raw if ch.raw is not None else Block()
        # the spouse sits on the council of a ruler; only the ruler gets the bonus
        if ch.primary_spouse and ch.primary_spouse in sv.characters and raw.block("landed_data").get("domain"):
            _add(out, (1.0, 1.0, 1.0, 1.0, 1.0))
        ad = raw.block("alive_data")
        for m in ad.getall("modifier"):
            _add(out, self.modifiers.get(m.str("modifier") if isinstance(m, Block) else m))
        inv = ad.block("inventory").get("artifacts")
        for aid in (inv.values() if isinstance(inv, Block) else []):
            for m in sv.artifact_modifiers.get(aid, []):
                _add(out, self.modifiers.get(m))
        focus = ad.block("focus").str("type")
        _add(out, self.focuses.get(focus or ""))
        cu = sv.cultures.get(ch.culture or "")
        for tr in (cu.traditions if cu else []):
            _add(out, self.traditions.get(tr))
        faith = sv.faiths.get(ch.faith or "")
        if faith is not None:
            for d in faith.doctrines:
                _add(out, self.doctrines.get(d))
            my_faith = faith.tag or faith.template
            for hid in faith.holy_sites:
                site = self.holy_sites.get(sv.holy_site_types.get(hid, ""))
                if site and site[0]:
                    t = sv.title_by_key.get(site[0])
                    if t is not None and t.holder and self._faith_key(t.holder) == my_faith:
                        _add(out, site[1])
        for _cty, _fv, hv in self.holy_sites.values():
            if hv and _cty:
                t = sv.title_by_key.get(_cty)
                if t is not None and t.holder == ch.id:
                    _add(out, hv)
        h = sv.houses.get(ch.house or "")
        for p in (sv.dynasty_perks.get(h.dynasty, []) if h and h.dynasty else []):
            _add(out, self.legacies.get(p))
        for bar in self.baronies_of.get(ch.id, []):
            td = self.ck3.titles.get(bar)
            b = sv.baronies.get(str(td.province)) if td and td.province is not None else None
            for bk in (b.buildings if b else []):
                _add(out, self.buildings.get(bk))
        return out
