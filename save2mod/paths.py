"""Best-effort discovery of game installs, save folders and the EU5 mod folder
on Windows and Linux (native Steam, Flatpak Steam, Proton prefixes)."""
from __future__ import annotations

import os
import re
import sys

CK3_APPID = "1158310"
EU5_APPID = "3450310"


def _home(*p: str) -> str:
    return os.path.join(os.path.expanduser("~"), *p)


def steam_roots() -> list[str]:
    cands = []
    if sys.platform.startswith("win"):
        for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), "C:/Program Files (x86)"):
            if pf:
                cands.append(os.path.join(pf, "Steam"))
    else:
        cands += [_home(".steam", "steam"), _home(".local", "share", "Steam"), _home(".steam", "root"),
                  _home(".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
                  _home(".var", "app", "com.valvesoftware.Steam", "data", "Steam")]
    roots: list[str] = []
    for c in cands:
        if os.path.isdir(c):
            r = os.path.realpath(c)
            if r not in roots:
                roots.append(r)
    # extra libraries from libraryfolders.vdf
    for r in list(roots):
        vdf = os.path.join(r, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as fh:
                for m in re.finditer(r'"path"\s*"([^"]+)"', fh.read()):
                    p = os.path.realpath(m.group(1).replace("\\\\", "\\"))
                    if os.path.isdir(p) and p not in roots:
                        roots.append(p)
        except OSError:
            pass
    return roots


def find_game(folder_name: str) -> str | None:
    for r in steam_roots():
        g = os.path.join(r, "steamapps", "common", folder_name, "game")
        if os.path.isdir(g):
            return g
    return None


def find_ck3_game() -> str | None:
    return find_game("Crusader Kings III")


def find_eu5_game() -> str | None:
    return find_game("Europa Universalis V")


def _documents_candidates(appid: str) -> list[str]:
    out = []
    if sys.platform.startswith("win"):
        out.append(_home("Documents"))
        od = os.environ.get("OneDrive")
        if od:
            out.append(os.path.join(od, "Documents"))
    else:
        out.append(_home(".local", "share"))           # native Linux Paradox games
        out.append(_home("Documents"))
        for r in steam_roots():
            out.append(os.path.join(r, "steamapps", "compatdata", appid, "pfx", "drive_c", "users",
                                    "steamuser", "Documents"))
    return out


def find_ck3_saves() -> str | None:
    for d in _documents_candidates(CK3_APPID):
        p = os.path.join(d, "Paradox Interactive", "Crusader Kings III", "save games")
        if os.path.isdir(p):
            return p
    return None


def find_eu5_mod_dir() -> str | None:
    for d in _documents_candidates(EU5_APPID):
        base = os.path.join(d, "Paradox Interactive", "Europa Universalis V")
        if os.path.isdir(base):
            return os.path.join(base, "mod")
    return None


def newest_save(folder: str | None) -> str | None:
    if not folder or not os.path.isdir(folder):
        return None
    saves = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".ck3")]
    return max(saves, key=os.path.getmtime) if saves else None
