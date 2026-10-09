"""Paradox .yml localization read/write."""
from __future__ import annotations

import os
import re

_LINE = re.compile(r'^\s*([A-Za-z0-9_.\-\']+):\d*\s*"(.*)"\s*(?:#.*)?$')
_REF = re.compile(r"\$([A-Za-z0-9_.\-]+)\$")


def load_localization_dir(path: str, recursive: bool = True) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isdir(path):
        return out
    for dirpath, _dirs, files in os.walk(path):
        for fn in sorted(files):
            if fn.endswith(".yml"):
                _load_file(os.path.join(dirpath, fn), out)
        if not recursive:
            break
    return out


def _load_file(fp: str, out: dict[str, str]) -> None:
    try:
        with open(fp, encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                m = _LINE.match(line)
                if m:
                    out[m.group(1)] = m.group(2)
    except OSError:
        pass


def resolve(loc: dict[str, str], key: str, depth: int = 0) -> str | None:
    """Return a localized string with simple $key$ references resolved and
    formatting markup removed."""
    s = loc.get(key)
    if s is None:
        return None
    if depth < 3 and "$" in s:
        s = _REF.sub(lambda m: resolve(loc, m.group(1), depth + 1) or m.group(0), s)
    s = re.sub(r"#[A-Za-z_]+ |#!|\[[^\]]*\]", "", s)
    return s.strip()


def write_yml(path: str, language: str, entries: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as fh:
        fh.write(f"l_{language}:\n")
        for k, v in entries.items():
            v = v.replace('"', "'").replace("\n", " ")
            fh.write(f' {k}: "{v}"\n')
