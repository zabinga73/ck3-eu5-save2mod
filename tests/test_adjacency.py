"""Location borders from a bitmap, and exclave grouping."""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from save2mod.adjacency import components, pairs_from_image, read_straits   # noqa: E402


def _img(grid: list[str], colors: dict[str, tuple[int, int, int]], scale: int = 3) -> str:
    h, w = len(grid), len(grid[0])
    a = np.zeros((h * scale, w * scale, 3), dtype=np.uint8)
    for y, row in enumerate(grid):
        for x, ch in enumerate(row):
            a[y * scale:(y + 1) * scale, x * scale:(x + 1) * scale] = colors[ch]
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    Image.fromarray(a).save(p)
    return p


def test_pairs_from_image():
    # A B | C ; D spans the bottom; E only touches A through the wrap-around
    grid = ["EABC",
            "EABC",
            "DDDD"]
    cols = {"A": (10, 0, 0), "B": (20, 0, 0), "C": (30, 0, 0), "D": (40, 0, 0), "E": (50, 0, 0)}
    idx = {"A": 1, "B": 2, "C": 3, "D": 4, "E": 5}
    colors = {(r << 16) | (g << 8) | b: idx[k] for k, (r, g, b) in cols.items()}
    p = _img(grid, cols)
    try:
        pairs = {tuple(x) for x in pairs_from_image(p, colors, wrap_x=True, band=4, log=lambda s: None).tolist()}
        pairs_nowrap = {tuple(x) for x in pairs_from_image(p, colors, wrap_x=False, band=5, log=lambda s: None).tolist()}
    finally:
        os.remove(p)
    expect = {(1, 2), (2, 3), (1, 4), (2, 4), (3, 4), (4, 5), (1, 5), (3, 5)}
    assert pairs == expect, pairs
    assert pairs_nowrap == expect - {(3, 5)}, pairs_nowrap


def test_components_with_sea_hops():
    # land: a1-a2 (mainland), island i1 across one sea tile, far island f1 across three
    adj = {
        "a1": {"a2", "s1"}, "a2": {"a1", "x1"},
        "s1": {"a1", "i1", "s2"}, "i1": {"s1"},
        "s2": {"s1", "s3"}, "s3": {"s2", "s4"}, "s4": {"s3", "f1"}, "f1": {"s4"},
        "x1": {"a2", "e1"}, "e1": {"x1"},     # e1 lies beyond someone else's land x1
    }
    land = {"a1", "a2", "i1", "f1", "x1", "e1"}
    mine = ["a1", "a2", "i1", "f1", "e1"]
    g2 = components(mine, adj, set(mine), lambda l: l in land, max_hops=2)
    assert sorted(sorted(g) for g in g2) == [["a1", "a2", "i1"], ["e1"], ["f1"]]
    g4 = components(mine, adj, set(mine), lambda l: l in land, max_hops=4)
    assert sorted(sorted(g) for g in g4) == [["a1", "a2", "f1", "i1"], ["e1"]]
    # a subject owning x1 connects e1 through the realm
    g_realm = components(mine, adj, set(mine) | {"x1"}, lambda l: l in land, max_hops=0)
    assert sorted(sorted(g) for g in g_realm) == [["a1", "a2", "e1"], ["f1"], ["i1"]]


def test_read_straits():
    fd, p = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("From;To;Type;Through;start_x;start_y;stop_x;stop_y;Comment\n"
                 "messina;reggiocal;sea;strait_messina;1;2;3;4;xxx\n")
    try:
        assert read_straits(p) == [("messina", "reggiocal")]
    finally:
        os.remove(p)
