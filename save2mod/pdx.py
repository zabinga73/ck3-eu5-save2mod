"""Paradox script (Clausewitz/Jomini) reader and writer.

Handles game files (comments, operators like ``?=`` and ``>=``, tagged blocks
such as ``rgb { 1 2 3 }``) and CK3 save text (no comments, ``key=value``).

The parse result is a :class:`Block`: an ordered list of entries. An entry is
either ``(key, op, value)`` or a bare value ``(None, None, value)``. A value is
a ``str``, a nested :class:`Block`, or a :class:`Tagged` (``rgb { ... }``).
Quoted strings keep their quotes stripped but are wrapped in :class:`QStr` so
the writer can put them back.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Iterator

__all__ = [
    "Block", "Tagged", "QStr", "parse_text", "parse_file", "read_text",
    "dump", "fmt_value", "ParseError",
]


class ParseError(Exception):
    pass


class QStr(str):
    """A string that was quoted in the source."""
    __slots__ = ()


class Tagged:
    """``tag { ... }`` values such as ``rgb { 10 20 30 }`` or ``hsv { }``."""
    __slots__ = ("tag", "block")

    def __init__(self, tag: str, block: "Block"):
        self.tag = tag
        self.block = block

    def __repr__(self) -> str:
        return f"Tagged({self.tag!r}, {self.block!r})"


class Block:
    __slots__ = ("items", "_idx")

    def __init__(self, items: list | None = None):
        self.items: list[tuple] = items if items is not None else []
        self._idx: dict | None = None

    # ------------------------------------------------------------ building
    def add(self, key: str | None, value: Any, op: str | None = "=") -> None:
        self.items.append((key, op if key is not None else None, value))
        self._idx = None

    def append(self, value: Any) -> None:
        self.items.append((None, None, value))
        self._idx = None

    # ------------------------------------------------------------ access
    def _index(self) -> dict:
        if self._idx is None:
            idx: dict = {}
            for i, (k, _op, _v) in enumerate(self.items):
                if k is not None and k not in idx:
                    idx[k] = i
            self._idx = idx
        return self._idx

    def get(self, key: str, default: Any = None) -> Any:
        i = self._index().get(key)
        return default if i is None else self.items[i][2]

    def get_last(self, key: str, default: Any = None) -> Any:
        for k, _op, v in reversed(self.items):
            if k == key:
                return v
        return default

    def getall(self, key: str) -> list:
        return [v for k, _op, v in self.items if k == key]

    def __contains__(self, key: str) -> bool:
        return key in self._index()

    def __getitem__(self, key: str) -> Any:
        i = self._index().get(key)
        if i is None:
            raise KeyError(key)
        return self.items[i][2]

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[tuple]:
        return iter(self.items)

    def keys(self) -> list[str]:
        return [k for k, _op, _v in self.items if k is not None]

    def pairs(self) -> Iterator[tuple[str, Any]]:
        for k, _op, v in self.items:
            if k is not None:
                yield k, v

    def values(self) -> list:
        """Bare (unkeyed) values, e.g. the members of ``{ a b c }``."""
        return [v for k, _op, v in self.items if k is None]

    def is_list(self) -> bool:
        return all(k is None for k, _op, _v in self.items)

    def str(self, key: str, default: str | None = None) -> str | None:
        v = self.get(key)
        return v if isinstance(v, str) else default

    def num(self, key: str, default: float | None = None) -> float | None:
        v = self.get(key)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return default
        return default

    def block(self, key: str) -> "Block":
        v = self.get(key)
        return v if isinstance(v, Block) else Block()

    def path(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self
        for k in keys:
            if not isinstance(cur, Block):
                return default
            cur = cur.get(k)
            if cur is None:
                return default
        return cur

    def remove(self, key: str) -> None:
        self.items = [it for it in self.items if it[0] != key]
        self._idx = None

    def set(self, key: str, value: Any, op: str = "=") -> None:
        for i, (k, o, _v) in enumerate(self.items):
            if k == key:
                self.items[i] = (k, o, value)
                return
        self.add(key, value, op)

    def __repr__(self) -> str:
        if len(self.items) > 6:
            return f"Block(<{len(self.items)} items>)"
        return f"Block({self.items!r})"


# --------------------------------------------------------------------- lexing
_TOKEN = re.compile(
    r"""
    (?P<ws>[\s﻿]+)
  | (?P<comment>\#[^\n]*)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<op>\?=|>=|<=|!=|==|=|<|>)
  | (?P<open>\{)
  | (?P<close>\})
  | (?P<word>[^\s={}"#<>!?]+(?:[!?][^\s={}"#<>!?]+)*)
  | (?P<junk>.)
    """,
    re.VERBOSE | re.DOTALL,
)

_OPS = {"=", "?=", ">=", "<=", "!=", "==", "<", ">"}


def _tokens(text: str) -> Iterator[tuple[int, str]]:
    """Yield (kind, text). kind: 0 word, 1 quoted string, 2 op, 3 open, 4 close."""
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        if kind == "ws" or kind == "comment":
            continue
        s = m.group()
        if kind == "word":
            yield 0, s
        elif kind == "str":
            body = s[1:-1]
            if "\\" in body:
                body = body.replace('\\"', '"').replace("\\\\", "\\")
            yield 1, body
        elif kind == "op":
            yield 2, s
        elif kind == "open":
            yield 3, s
        elif kind == "close":
            yield 4, s
        else:  # stray char: treat as word so we never hang
            yield 0, s


def parse_text(text: str, *, strict: bool = False) -> Block:
    """Parse Paradox script text into a Block. Unbalanced braces are tolerated
    unless ``strict`` is set (game files frequently have an extra ``}``)."""
    root = Block()
    stack: list[Block] = [root]
    cur = root
    toks = _tokens(text)
    pending: tuple[int, str] | None = None

    def nxt():
        nonlocal pending
        if pending is not None:
            t = pending
            pending = None
            return t
        return next(toks, None)

    while True:
        t = nxt()
        if t is None:
            break
        kind, s = t
        if kind == 4:  # close
            if len(stack) > 1:
                stack.pop()
                cur = stack[-1]
            elif strict:
                raise ParseError("unbalanced '}'")
            continue
        if kind == 3:  # bare block
            b = Block()
            cur.items.append((None, None, b))
            stack.append(b)
            cur = b
            continue
        if kind == 2:  # stray operator
            if strict:
                raise ParseError(f"unexpected operator {s}")
            continue
        # word or string
        key = QStr(s) if kind == 1 else s
        t2 = nxt()
        if t2 is not None and t2[0] == 2:  # key op value
            op = t2[1]
            t3 = nxt()
            if t3 is None:
                break
            k3, s3 = t3
            if k3 == 3:
                b = Block()
                cur.items.append((key, op, b))
                stack.append(b)
                cur = b
            elif k3 == 4:  # "key = }" - malformed; close
                cur.items.append((key, op, ""))
                pending = t3
            elif k3 == 2:
                # "a = = b" malformed; skip op
                pending = None
            else:
                val = QStr(s3) if k3 == 1 else s3
                # tagged block: rgb { } / hsv { } / LIST { }
                t4 = nxt()
                if t4 is not None and t4[0] == 3 and k3 == 0:
                    b = Block()
                    cur.items.append((key, op, Tagged(s3, b)))
                    stack.append(b)
                    cur = b
                else:
                    cur.items.append((key, op, val))
                    pending = t4
        elif t2 is not None and t2[0] == 3 and kind == 0:
            # "key { ... }" without '=' - the engine reads it as "key = { ... }",
            # except colour tags inside lists ("rgb { 1 2 3 }")
            b = Block()
            if s in ("rgb", "hsv", "hsv360", "LIST", "RANGE"):
                cur.items.append((None, None, Tagged(s, b)))
            else:
                cur.items.append((key, "=", b))
            stack.append(b)
            cur = b
        else:
            cur.items.append((None, None, key))
            pending = t2
    if strict and len(stack) != 1:
        raise ParseError("unbalanced '{'")
    return root


def read_text(path) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def parse_file(path) -> Block:
    return parse_text(read_text(path))


# -------------------------------------------------------------------- writing
_BARE_OK = re.compile(r"^[A-Za-z0-9_.:@\-\[\]'|$/]+$")


def fmt_value(v: Any) -> str:
    if isinstance(v, QStr):
        return '"' + str(v).replace('"', '\\"') + '"'
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        s = f"{v:.3f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        if v == "" or not _BARE_OK.match(v):
            return '"' + v.replace('"', '\\"') + '"'
        return v
    raise TypeError(f"cannot format {type(v)}")


def dump(block: Block, indent: int = 0, *, inline_lists: bool = True) -> str:
    """Serialise a Block's entries (without surrounding braces)."""
    out: list[str] = []
    _dump_into(block, indent, out, inline_lists)
    return "".join(out)


def _dump_into(block: Block, indent: int, out: list[str], inline_lists: bool) -> None:
    pad = "\t" * indent
    for k, op, v in block.items:
        prefix = pad if k is None else f"{pad}{fmt_value(k) if isinstance(k, QStr) else k} {op or '='} "
        if isinstance(v, Block):
            if inline_lists and v.items and v.is_list() and all(not isinstance(x[2], (Block, Tagged)) for x in v.items) and len(v.items) <= 40:
                out.append(prefix + "{ " + " ".join(fmt_value(x[2]) for x in v.items) + " }\n")
            elif not v.items:
                out.append(prefix + "{ }\n")
            else:
                out.append(prefix + "{\n")
                _dump_into(v, indent + 1, out, inline_lists)
                out.append(pad + "}\n")
        elif isinstance(v, Tagged):
            if v.block.is_list() and all(not isinstance(x[2], (Block, Tagged)) for x in v.block.items):
                out.append(prefix + v.tag + " { " + " ".join(fmt_value(x[2]) for x in v.block.items) + " }\n")
            else:
                out.append(prefix + v.tag + " {\n")
                _dump_into(v.block, indent + 1, out, inline_lists)
                out.append(pad + "}\n")
        else:
            out.append(prefix + fmt_value(v) + "\n")


def block_from(obj: Any) -> Block:
    """Build a Block from python data: dict -> keyed, list of pairs -> keyed
    (allowing repeated keys), list/tuple of scalars -> bare list."""
    b = Block()
    if isinstance(obj, dict):
        for k, v in obj.items():
            b.add(k, _conv(v))
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                b.add(item[0], _conv(item[1]))
            else:
                b.append(_conv(item))
    return b


def _conv(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return block_from(v)
    if isinstance(v, (Block, Tagged, str)):
        return v
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return fmt_value(v)
    raise TypeError(type(v))


def iter_top_level_spans(text: str) -> Iterable[tuple[str, int, int]]:
    """Yield (key, start, end) for each top-level ``key = {...}`` or
    ``key = value`` entry in ``text`` using the tokenizer (robust to comments
    and strings). ``end`` is exclusive and covers the value."""
    depth = 0
    key = None
    key_start = 0
    expect_value = False
    last_word_start = 0
    last_word = None
    for m in _TOKEN.finditer(text):
        kind = m.lastgroup
        if kind in ("ws", "comment"):
            continue
        s = m.group()
        if kind == "open":
            if depth == 0 and expect_value:
                expect_value = False
            depth += 1
        elif kind == "close":
            depth -= 1
            if depth == 0 and key is not None:
                yield key, key_start, m.end()
                key = None
            if depth < 0:
                depth = 0
        elif depth == 0:
            if kind == "op":
                if last_word is not None:
                    key, key_start = last_word, last_word_start
                    expect_value = True
            elif expect_value:
                expect_value = False
                # scalar value; could be a tagged block like rgb{...}
                yield key, key_start, m.end()
                key = None
                last_word = None
            else:
                last_word = s.strip('"') if kind == "str" else s
                last_word_start = m.start()
