"""CK3 coat of arms -> EU5 flag conversion."""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from save2mod.coa import CoaConverter          # noqa: E402
from save2mod.pdx import Block, dump, parse_text  # noqa: E402


def _touch(p: str, text: str = "x") -> None:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def _setup(tmp: str):
    eu5 = os.path.join(tmp, "eu5")
    ck3 = os.path.join(tmp, "ck3")
    g = os.path.join(eu5, "main_menu", "gfx", "coat_of_arms")
    _touch(os.path.join(g, "patterns", "pattern_solid.dds"))
    _touch(os.path.join(g, "colored_emblems", "ce_lion.dds"))
    _touch(os.path.join(g, "textured_emblems", "te_dummy.dds"))
    _touch(os.path.join(eu5, "main_menu", "common", "named_colors", "c.txt"),
           "colors = { red = rgb { 200 0 0 } white = rgb { 255 255 255 } yellow = rgb { 250 200 0 } }")
    c = os.path.join(ck3, "gfx", "coat_of_arms")
    _touch(os.path.join(c, "patterns", "pattern_checkers_01.dds"))
    _touch(os.path.join(c, "colored_emblems", "ce_bend.dds"))
    _touch(os.path.join(c, "colored_emblems", "ce_alerion.dds"))
    _touch(os.path.join(c, "colored_emblems", "50_coa_designer_emblems.txt"),
           'ce_bend.dds = { colors = 1 category = "ordinaries" }\nce_alerion.dds = { colors = 2 category = "x" }\n')
    _touch(os.path.join(ck3, "common", "named_colors", "c.txt"),
           "colors = { ck3_purple = rgb { 120 20 140 } ck3_gold = hsv { 0.12 0.8 0.9 } }")
    _touch(os.path.join(ck3, "common", "coat_of_arms", "coat_of_arms", "t.txt"),
           "@half = 0.5\nsub_tpl = { pattern = \"pattern_checkers_01.dds\" color1 = \"white\" color2 = ck3_purple "
           "colored_emblem = { texture = \"ce_lion.dds\" instance = { position = { @half @[ 1 / 4 ] } "
           "scale = { @half @half } } } }\n")
    return SimpleNamespace(root=os.path.join(eu5)), SimpleNamespace(root=ck3)


def test_flag_conversion():
    with tempfile.TemporaryDirectory() as tmp:
        eu5, ck3 = _setup(tmp)
        conv = CoaConverter(eu5, ck3, log=lambda s: None)
        src = parse_text('''
            pattern="pattern_solid.dds" color1=yellow color2=red color3=ck3_gold
            colored_emblem={ texture="ce_bend.dds" color1=red instance={ position={ 0.5 0.5 } scale={ 1.0 1.0 } } }
            colored_emblem={ texture="ce_alerion.dds" color1=white
                instance={ position={ 0.3 0.3 } scale={ 0.3 0.3 } }
                instance={ position={ 0.7 0.7 } scale={ -0.3 0.3 } } }
            colored_emblem={ texture="ce_unknown_thing.dds" }
            sub={ parent="sub_tpl" color1=red instance={ offset={ 0.5 0.0 } scale={ 0.5 0.5 } } }
        ''')
        out = conv.convert(src, (1, 2, 3))
        text = dump(Block([("X", "=", out)]))
        embl = out.getall("colored_emblem")
        assert [e.str("texture") for e in embl] == ["ce_bend.dds", "ce_alerion.dds"], text
        # the bend spans the flag; the alerions are narrowed (mirroring kept)
        assert embl[0].block("instance").block("scale").values() == ["1", "1"], text
        sc = [i.block("scale").values() for i in embl[1].getall("instance")]
        assert sc == [["0.225", "0.3"], ["-0.225", "0.3"]], sc
        # CK3-only colours become rgb, unknown textures are left out and reported
        assert "rgb" in text and conv.missing == {"ce_unknown_thing.dds": 1}
        # sub-coat: template resolved, own colour wins, variables/expressions evaluated
        sub = out.block("sub")
        assert sub.str("pattern") == "pattern_checkers_01.dds" and sub.str("color1") == "red", text
        inst = sub.block("colored_emblem").block("instance")
        assert inst.block("position").values() == ["0.5", "0.25"], text
        assert inst.block("scale").values() == ["0.375", "0.5"], text
        assert sub.block("instance").block("offset").values() == ["0.5", "0"], text
        # textures EU5 lacks are copied with their colour counts
        mod = os.path.join(tmp, "mod")
        assert conv.write_textures(mod) == 3
        ce = os.path.join(mod, "main_menu", "gfx", "coat_of_arms", "colored_emblems")
        assert sorted(f for f in os.listdir(ce) if f.endswith(".dds")) == ["ce_alerion.dds", "ce_bend.dds"]
        meta = open(os.path.join(ce, "zz_save2mod_from_ck3.txt"), encoding="utf-8-sig").read()
        assert "ce_bend.dds = { colors = 1 }" in meta and "category" not in meta
        assert os.path.exists(os.path.join(mod, "main_menu", "gfx", "coat_of_arms", "patterns",
                                           "pattern_checkers_01.dds"))
