"""CK3 coats of arms -> EU5 country flags.

Both games use the same Jomini coat-of-arms format, but:
* many CK3 patterns and emblems don't exist in EU5 - those texture files are
  copied from the CK3 install into the mod (the flag used to lose them);
* CK3 draws on a square canvas, EU5 flags are 3:2 - charges (lions, eagles,
  towers...) are narrowed (x 0.75) so they aren't stretched, while field divisions and
  ordinaries (bends, crosses, fesses...) still span the flag;
* sub-coats (quarterings, impalements) are converted too, and 'parent'
  references to CK3 coat-of-arms templates are resolved.
"""
from __future__ import annotations

import glob
import os
import re
import shutil

from .pdx import Block, QStr, Tagged, parse_file

KINDS = ("patterns", "colored_emblems", "textured_emblems")
ASPECT = 0.75                 # charges narrowed for EU5's 3:2 flag (full correction would be 2/3)
ORDINARY = re.compile(
    r"(bend|cross|saltire|fess|pale|chevron|bordure|border|chief|bar_|bars|stripe|quarter|pile|gyron|per_|"
    r"band|tierce|orle|pall|canton|base|flaunch|field|chequy|checker|lozengy|paly|barry|bendy|frame|"
    r"cantons|triangle|diagonal|horizontal|vertical|split|half)", re.I)


def _listdir(p: str) -> dict[str, str] | None:
    """lower-case file name -> real file name; None when the folder is missing."""
    try:
        return {f.lower(): f for f in os.listdir(p)}
    except OSError:
        return None


def _num(tok, vars_: dict[str, str]) -> float | None:
    if isinstance(tok, str) and tok.startswith("@") and not tok.startswith("@["):
        tok = vars_.get(tok, tok)
    if isinstance(tok, str) and tok.startswith("@[") and tok.endswith("]"):
        expr = tok[2:-1]
        for k, v in sorted(vars_.items(), key=lambda kv: -len(kv[0])):
            expr = expr.replace(k[1:], str(v)).replace("@" + k[1:], str(v))
        if re.fullmatch(r"[0-9.+\-*/() ]+", expr):
            try:
                return float(eval(expr, {"__builtins__": {}}, {}))   # noqa: S307 - digits/operators only
            except Exception:                                          # noqa: BLE001
                return None
        return None
    try:
        return float(tok)
    except (TypeError, ValueError):
        return None


def _nums(b, vars_: dict[str, str]) -> list[float]:
    """Numbers of a list block, joining CK3's '@[ a / b ]' expressions that the
    tokenizer splits into pieces."""
    if not isinstance(b, Block):
        return []
    toks = [v for v in b.values()]
    out: list[float] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if isinstance(t, str) and t.startswith("@[") and not t.endswith("]"):
            parts = [t]
            i += 1
            while i < len(toks) and not (isinstance(toks[i], str) and toks[i].endswith("]")):
                parts.append(str(toks[i]))
                i += 1
            if i < len(toks):
                parts.append(str(toks[i]))
            t = " ".join(parts).replace("@[ ", "@[")
        n = _num(t, vars_)
        if n is not None:
            out.append(n)
        i += 1
    return out


def _fmt(x: float) -> str:
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _nb(vals: list[float]) -> Block:
    return Block([(None, None, _fmt(v)) for v in vals])


class CoaConverter:
    def __init__(self, eu5, ck3, log=print):
        self.log = log
        eu5_gfx = os.path.join(eu5.root, "main_menu", "gfx", "coat_of_arms")
        self.ck3_gfx = os.path.join(ck3.root, "gfx", "coat_of_arms")
        self.have = {k: _listdir(os.path.join(eu5_gfx, k)) for k in KINDS}
        self.ck3_have = {k: _listdir(os.path.join(self.ck3_gfx, k)) for k in KINDS}
        self.named = _eu5_named_colors(eu5)
        self.ck3_colors = _ck3_named_colors(ck3)
        self.to_copy: dict[str, dict[str, str]] = {k: {} for k in KINDS}
        self.missing: dict[str, int] = {}
        self.ck3_root = ck3.root
        self._templates: dict[str, tuple[Block, dict[str, str]]] | None = None

    # ------------------------------------------------------------ textures
    def texture(self, kind: str, name: str | None) -> str | None:
        if not name:
            return None
        low = name.lower()
        have = self.have[kind]
        if have is None or low in have:
            return name                                     # EU5 has it (or we can't tell)
        real = (self.ck3_have[kind] or {}).get(low)
        if real:
            self.to_copy[kind][low] = real
            return real
        self.missing[name] = self.missing.get(name, 0) + 1
        return None

    # ------------------------------------------------------------- colours
    def color(self, val):
        if isinstance(val, str):
            if val.startswith("color") and val[5:].isdigit():
                return val
            if not self.named or val in self.named:
                return QStr(val)
            rgb = self.ck3_colors.get(val)
            if rgb:
                return Tagged("rgb", Block([(None, None, str(x)) for x in rgb]))
            return QStr("white")
        return val

    # ---------------------------------------------------------- templates
    def template(self, name: str) -> tuple[Block, dict[str, str]] | None:
        if self._templates is None:
            self._templates = {}
            for f in sorted(glob.glob(os.path.join(self.ck3_root, "common", "coat_of_arms", "coat_of_arms",
                                                   "*.txt"))):
                try:
                    b = parse_file(f)
                except Exception:                           # noqa: BLE001
                    continue
                vars_ = {k: v for k, _o, v in b.items if isinstance(k, str) and k.startswith("@")
                         and isinstance(v, str)}
                for k, _o, v in b.items:
                    if isinstance(k, str) and not k.startswith("@") and isinstance(v, Block):
                        self._templates[k] = (v, vars_)
        return self._templates.get(name)

    # ------------------------------------------------------------ convert
    def convert(self, src: Block | None, fallback_rgb: tuple[int, int, int]) -> Block:
        if src is None:
            r, g, bl = fallback_rgb
            return Block([("pattern", "=", QStr("pattern_solid.dds")),
                          ("color1", "=", Tagged("rgb", Block([(None, None, str(r)), (None, None, str(g)),
                                                                 (None, None, str(bl))]))),
                          ("color2", "=", QStr("white"))])
        b = self._body(src, {}, 0)
        if "pattern" not in b:
            b.items.insert(0, ("pattern", "=", QStr("pattern_solid.dds")))
        if "color1" not in b:
            b.add("color1", QStr("red"))
        return b

    def _body(self, src: Block, vars_: dict[str, str], depth: int) -> Block:
        b = Block()
        parent = src.str("parent")
        if parent and depth < 4:
            t = self.template(parent)
            if t is not None:
                base = self._body(t[0], t[1], depth + 1)
                own = {k for k, _o, _v in src.items if k in ("pattern", "color1", "color2", "color3", "color4",
                                                               "color5")}
                b.items.extend(it for it in base.items if it[0] not in own)
        for k, op, v in src.items:
            if k == "pattern" and isinstance(v, str):
                tex = self.texture("patterns", v)
                b.add("pattern", QStr(tex or "pattern_solid.dds"))
            elif k in ("color1", "color2", "color3", "color4", "color5"):
                b.add(k, self.color(v))
            elif k in ("colored_emblem", "textured_emblem") and isinstance(v, Block):
                eb = self._emblem(k, v, vars_)
                if eb is not None:
                    b.add(k, eb)
            elif k == "sub" and isinstance(v, Block) and depth < 4:
                sb = self._sub(v, vars_, depth)
                if sb is not None:
                    b.add("sub", sb)
        return b

    def _sub(self, v: Block, vars_: dict[str, str], depth: int) -> Block | None:
        inner = self._body(v, vars_, depth + 1)
        if not any(k in ("pattern", "colored_emblem", "textured_emblem", "sub") for k, _o, _v in inner.items):
            return None
        for inst in v.getall("instance"):
            if isinstance(inst, Block):
                ib = Block()
                for kk, oo, vv in inst.items:
                    if kk in ("offset", "scale") and isinstance(vv, Block):
                        ib.add(kk, _nb(_nums(vv, vars_)))
                    else:
                        ib.items.append((kk, oo, vv))
                inner.add("instance", ib)
        return inner

    def _emblem(self, kind: str, v: Block, vars_: dict[str, str]) -> Block | None:
        tex = self.texture(kind + "s", v.str("texture"))
        if tex is None:
            return None
        ordinary = bool(ORDINARY.search(tex)) and kind == "colored_emblem"
        eb = Block()
        insts: list[Block] = []
        for kk, oo, vv in v.items:
            if kk in ("color1", "color2", "color3"):
                eb.add(kk, self.color(vv))
            elif kk == "texture":
                eb.add(kk, QStr(tex))
            elif kk == "instance" and isinstance(vv, Block):
                insts.append(vv)
            elif kk == "mask" and isinstance(vv, Block):
                eb.add(kk, vv)
            elif kk not in ("instance",):
                eb.items.append((kk, oo, vv))
        if not insts:
            insts = [Block()]
        for inst in insts:
            eb.add("instance", self._instance(inst, ordinary, vars_))
        return eb

    def _instance(self, inst: Block, ordinary: bool, vars_: dict[str, str]) -> Block:
        out = Block()
        scale = _nums(inst.get("scale"), vars_) or [1.0, 1.0]
        if len(scale) == 1:
            scale = [scale[0], scale[0]]
        # narrow charges so they keep their proportions on the wider flag;
        # full-size ordinaries (bends, crosses...) keep spanning it
        if not (ordinary and abs(scale[0]) >= 0.9):
            scale = [scale[0] * ASPECT, scale[1]]
        has_pos = False
        for kk, oo, vv in inst.items:
            if kk == "scale":
                continue
            if kk == "position" and isinstance(vv, Block):
                out.add("position", _nb(_nums(vv, vars_) or [0.5, 0.5]))
                has_pos = True
            elif kk == "rotation":
                n = _num(vv, vars_)
                out.add("rotation", _fmt(n) if n is not None else vv)
            else:
                out.items.append((kk, oo, vv))
        if not has_pos:
            out.items.insert(0, ("position", "=", _nb([0.5, 0.5])))
        out.add("scale", _nb(scale))
        return out

    # --------------------------------------------------------------- write
    def write_textures(self, mod_root: str) -> int:
        """Copy the CK3 textures the flags use into the mod, with their
        colour-count metadata."""
        n = 0
        for kind in KINDS:
            files = self.to_copy[kind]
            if not files:
                continue
            dst = os.path.join(mod_root, "main_menu", "gfx", "coat_of_arms", kind)
            os.makedirs(dst, exist_ok=True)
            for real in sorted(files.values()):
                try:
                    shutil.copyfile(os.path.join(self.ck3_gfx, kind, real), os.path.join(dst, real))
                    n += 1
                except OSError:
                    continue
            meta = self._metadata(kind, set(files))
            if meta:
                with open(os.path.join(dst, "zz_save2mod_from_ck3.txt"), "w", encoding="utf-8-sig",
                          newline="\n") as fh:
                    fh.write("# CK3 textures copied by save2mod for converted flags\n" + "\n".join(meta) + "\n")
        return n

    def _metadata(self, kind: str, wanted: set[str]) -> list[str]:
        """'name.dds = { colors = N }' lines for the copied textures, from
        CK3's own texture list files."""
        lines: list[str] = []
        for f in sorted(glob.glob(os.path.join(self.ck3_gfx, kind, "*.txt"))):
            try:
                b = parse_file(f)
            except Exception:                               # noqa: BLE001
                continue
            for k, _o, v in b.items:
                if isinstance(k, str) and k.lower() in wanted and isinstance(v, Block):
                    colors = v.get("colors")
                    if isinstance(colors, str):
                        lines.append(f'{k} = {{ colors = {colors} }}')
                        wanted = wanted - {k.lower()}
        return lines


# ------------------------------------------------------------ named colours
def _eu5_named_colors(eu5) -> set[str]:
    names: set[str] = set()
    for f in glob.glob(os.path.join(eu5.root, "main_menu", "common", "named_colors", "*.txt")):
        try:
            b = parse_file(f)
        except Exception:                                   # noqa: BLE001
            continue
        for k, v in b.pairs():
            if k == "colors" and isinstance(v, Block):
                names |= set(v.keys())
    return names


def _ck3_named_colors(ck3) -> dict[str, tuple[int, int, int]]:
    import colorsys
    out: dict[str, tuple[int, int, int]] = {}
    for f in glob.glob(os.path.join(ck3.root, "common", "named_colors", "*.txt")):
        try:
            b = parse_file(f)
        except Exception:                                   # noqa: BLE001
            continue
        cols = b.get("colors")
        if not isinstance(cols, Block):
            continue
        for k, v in cols.pairs():
            tag, blk = (v.tag, v.block) if isinstance(v, Tagged) else ("rgb", v)
            if not isinstance(blk, Block):
                continue
            try:
                nums = [float(x) for x in blk.values()[:3]]
            except (TypeError, ValueError):
                continue
            if len(nums) < 3:
                continue
            if tag == "rgb":
                if all(x <= 1.0 for x in nums):
                    nums = [x * 255 for x in nums]
                out[k] = tuple(int(round(x)) for x in nums)   # type: ignore[assignment]
            elif tag in ("hsv", "hsv360"):
                h, s, val = nums
                if tag == "hsv360":
                    h, s, val = h / 360, s / 100, val / 100
                r, g, bb = colorsys.hsv_to_rgb(h, s, val)
                out[k] = (int(r * 255), int(g * 255), int(bb * 255))
    return out
