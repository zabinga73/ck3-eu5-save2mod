"""Which EU5 locations touch each other, read from locations.png (plus the
strait crossings in adjacencies.csv). Cached next to the map alignment."""
from __future__ import annotations

import csv
import json
import os

import numpy as np

from .mappings import tables_dir


def _file_sig(paths: list[str]) -> str:
    import hashlib
    h = hashlib.sha1()
    for p in paths:
        try:
            st = os.stat(p)
            h.update(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode())
        except OSError:
            h.update(f"{p}|missing".encode())
    return h.hexdigest()[:16]


def _packed(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def pairs_from_image(path: str, colors: dict[int, int], wrap_x: bool = True, band: int = 512,
                     log=print) -> np.ndarray:
    """Unique (i, j), i < j, of 1-based colour indices whose pixels touch
    (4-neighbourhood; the left and right edges touch when wrap_x)."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None          # EU5's map is 134 Mpx
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    log(f"Reading {os.path.basename(path)} for location borders ({w}x{h}) ...")
    keys = np.array(sorted(colors), dtype=np.uint32)
    vals = np.array([colors[k] for k in keys.tolist()], dtype=np.int64)
    n = int(vals.max()) + 1 if len(vals) else 1
    found: list[np.ndarray] = []
    prev_last: np.ndarray | None = None

    def idx_of(p: np.ndarray) -> np.ndarray:
        pos = np.clip(np.searchsorted(keys, p), 0, len(keys) - 1)
        return np.where(keys[pos] == p, vals[pos], 0)

    def add(a: np.ndarray, b: np.ndarray) -> None:
        m = (a != b) & (a > 0) & (b > 0)
        if not m.any():
            return
        lo = np.minimum(a[m], b[m])
        hi = np.maximum(a[m], b[m])
        found.append(np.unique(lo * n + hi))

    for y0 in range(0, h, band):
        idx = idx_of(_packed(np.asarray(img.crop((0, y0, w, min(h, y0 + band))))))
        add(idx[:, :-1].ravel(), idx[:, 1:].ravel())
        if wrap_x:
            add(idx[:, -1], idx[:, 0])
        add(idx[:-1, :].ravel(), idx[1:, :].ravel())
        if prev_last is not None:
            add(prev_last, idx[0])
        prev_last = idx[-1].copy()
    if not found:
        return np.zeros((0, 2), dtype=np.int64)
    allk = np.unique(np.concatenate(found))
    return np.stack([allk // n, allk % n], axis=1)


def read_straits(path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        with open(path, encoding="utf-8-sig") as fh:
            rows = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return out
    for r in csv.reader(rows, delimiter=";"):
        if len(r) >= 2 and r[0] and r[1] and r[0] != "From":
            out.append((r[0].strip(), r[1].strip()))
    return out


def location_adjacency(eu5, log=print, use_cache: bool = True) -> dict[str, set[str]] | None:
    """loc -> neighbouring locations (any kind). None if locations.png is missing."""
    png = eu5.locations_png
    straits_csv = os.path.join(eu5.root, "in_game", "map_data", "adjacencies.csv")
    if not os.path.exists(png):
        log("locations.png not found: can't work out which land is connected (exclaves are left as they are)")
        return None
    sig = _file_sig([png, straits_csv, os.path.join(eu5.root, "in_game", "map_data", "definitions.txt")])
    d = os.path.join(os.path.dirname(tables_dir()), "cache")
    os.makedirs(d, exist_ok=True)
    cp = os.path.join(d, f"adjacency_{sig}.json")
    edges: list[tuple[str, str]] | None = None
    if use_cache and os.path.exists(cp):
        try:
            with open(cp, encoding="utf-8") as fh:
                edges = [tuple(e) for e in json.load(fh)]      # type: ignore[misc]
            log(f"Loaded cached location borders ({len(edges)} pairs)")
        except (OSError, ValueError):
            edges = None
    if edges is None:
        keys = eu5.loc_order
        colors = {eu5.locations[k].color: i + 1 for i, k in enumerate(keys)}
        pairs = pairs_from_image(png, colors, log=log)
        edges = [(keys[a - 1], keys[b - 1]) for a, b in pairs.tolist()]
        with open(cp, "w", encoding="utf-8") as fh:
            json.dump(edges, fh)
        log(f"Location borders: {len(edges)} pairs")
    adj: dict[str, set[str]] = {}
    for a, b in edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    for a, b in read_straits(straits_csv):
        if a in eu5.locations and b in eu5.locations:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)
    return adj


def components(locs: list[str], adj: dict[str, set[str]], passable_land: set[str], is_land,
               max_hops: int) -> list[set[str]]:
    """Split ``locs`` into groups connected through ``passable_land`` (the
    realm's own land) directly, or across at most ``max_hops`` non-land
    locations (sea, lake, wasteland, corridor)."""
    parent = {l: l for l in passable_land}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for l in passable_land:
        for nb in adj.get(l, ()):
            if nb in parent:
                union(l, nb)
    if max_hops > 0:
        for l in passable_land:
            start = [nb for nb in adj.get(l, ()) if not is_land(nb)]
            if not start:
                continue
            seen = set(start)
            frontier = start
            depth = 1
            while frontier and depth <= max_hops:
                nxt: list[str] = []
                for s in frontier:
                    for nb in adj.get(s, ()):
                        if nb in parent:
                            union(l, nb)
                        elif nb not in seen and not is_land(nb) and depth < max_hops:
                            seen.add(nb)
                            nxt.append(nb)
                frontier = nxt
                depth += 1
    groups: dict[str, set[str]] = {}
    for l in locs:
        if l in parent:
            groups.setdefault(find(l), set()).add(l)
    return list(groups.values())
