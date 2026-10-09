"""Synthetic check of the map-alignment pipeline.

A fake 'EU5' world of Voronoi locations is drawn; a fake 'CK3' map is drawn
from a different, coarser Voronoi partition living in a non-linearly warped
projection. Some location/barony pairs share names (plus deliberate wrong
matches). The matcher must recover the true location->barony assignment.
"""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SAVE2MOD_HOME", tempfile.mkdtemp())

from save2mod.geomap import MapMatcher  # noqa: E402

rng = np.random.default_rng(7)
EW, EH = 2048, 1024
CW, CH = 2600, 1500


def warp(x, y):
    """EU5 -> CK3 'projection' (smooth, non-linear)."""
    u = (x - 500) / 1000.0
    v = (y - 150) / 700.0
    cx = 300 + 2000 * u + 180 * u * u - 90 * u * v + 25 * np.sin(3 * v)
    cy = 100 + 1250 * v + 120 * u * v - 60 * u * u
    return cx, cy


def word(k):
    letters = "abcdefghijklmnopqrstuvwxyz"
    out = ""
    k += 1000
    while k:
        k, r = divmod(k, 26)
        out += letters[r]
    return "zq" + out


def colors(n):
    c = rng.choice(256 ** 3 - 2, size=n, replace=False) + 1
    return [(int(v >> 16) & 255, int(v >> 8) & 255, int(v) & 255) for v in c]


def build(tmp):
    # ---------------- EU5 world
    n_loc = 2600
    land_mask = np.zeros((EH, EW), bool)
    yy, xx = np.mgrid[0:EH, 0:EW]
    land_mask |= ((xx - 1000) / 700.0) ** 2 + ((yy - 500) / 380.0) ** 2 < 1       # CK3-covered continent
    land_mask |= ((xx - 1850) / 150.0) ** 2 + ((yy - 300) / 200.0) ** 2 < 1       # far island (uncovered)
    pts = np.column_stack([rng.uniform(0, EW, n_loc), rng.uniform(0, EH, n_loc)])
    tree = cKDTree(pts)
    _, lab = tree.query(np.column_stack([xx.ravel(), yy.ravel()]))
    lab = lab.reshape(EH, EW) + 1
    lab[~land_mask] = 0                            # 0 = sea
    ecol = colors(n_loc + 1)
    img = np.zeros((EH, EW, 3), np.uint8)
    pal = np.array(ecol, np.uint8)
    img[:] = pal[lab]
    os.makedirs(os.path.join(tmp, "eu5"), exist_ok=True)
    Image.fromarray(img).save(os.path.join(tmp, "eu5", "locations.png"))
    locations = {}
    loc_order = []
    for i in range(n_loc + 1):
        key = "sea0" if i == 0 else f"loc{i}"
        kind = "sea" if i == 0 else "land"
        if i and not (lab == i).any():
            continue
        r, g, b = ecol[i]
        locations[key] = SimpleNamespace(key=key, color=(r << 16) | (g << 8) | b, kind=kind)
        loc_order.append(key)
    # ---------------- CK3 world: sample warped plane with its own Voronoi partition
    n_bar = 1100
    cy_, cx_ = np.mgrid[0:CH, 0:CW]
    # inverse-warp approximation: build baronies from seeds placed in EU5 space then warped
    seeds_e = np.column_stack([rng.uniform(250, 1750, n_bar), rng.uniform(100, 900, n_bar)])
    sx, sy = warp(seeds_e[:, 0], seeds_e[:, 1])
    seeds_c = np.column_stack([sx, sy])
    ctree = cKDTree(seeds_c)
    _, clab = ctree.query(np.column_stack([cx_.ravel(), cy_.ravel()]))
    clab = clab.reshape(CH, CW) + 1
    # CK3 land = warped image of EU5 continent (approx via forward warp of EU5 land samples)
    ck_land = np.zeros((CH, CW), bool)
    ey, ex = np.nonzero(land_mask & (xx < 1750))
    wx, wy = warp(ex.astype(float), ey.astype(float))
    ok = (wx >= 0) & (wx < CW) & (wy >= 0) & (wy < CH)
    ck_land[wy[ok].astype(int), wx[ok].astype(int)] = True
    from scipy.ndimage import binary_closing
    ck_land = binary_closing(ck_land, iterations=3)
    clab[~ck_land] = 0                              # province 0 -> sea (id n_bar+1)
    sea_id = n_bar + 1
    clab[clab == 0] = sea_id
    ccol = colors(n_bar + 2)
    cimg = np.array(ccol, np.uint8)[clab]
    os.makedirs(os.path.join(tmp, "ck3", "map_data"), exist_ok=True)
    Image.fromarray(cimg).save(os.path.join(tmp, "ck3", "map_data", "provinces.png"))
    titles = {}
    for pid in range(1, n_bar + 1):
        titles[f"b_bar{pid}"] = SimpleNamespace(tier=0, province=pid, parent=f"c_cty{pid}", children=[])
        titles[f"c_cty{pid}"] = SimpleNamespace(tier=1, province=None, parent=None, children=[f"b_bar{pid}"])
    province_colors = {pid: ccol[pid] for pid in range(1, n_bar + 2)}

    # ---------------- ground truth by forward-warping every EU5 land pixel
    truth = {}
    shares = {}
    ly, lx = np.nonzero(lab > 0)
    wx, wy = warp(lx.astype(float), ly.astype(float))
    inb = (wx >= 0) & (wx < CW) & (wy >= 0) & (wy < CH)
    pid = np.zeros(len(lx), int)
    pid[inb] = clab[wy[inb].astype(int), wx[inb].astype(int)]
    L = lab[ly, lx]
    for loc_i in np.unique(L):
        m = L == loc_i
        p = pid[m]
        land_p = p[(p > 0) & (p != sea_id)]
        if len(land_p) < 0.5 * m.sum():
            continue
        vals, cnts = np.unique(land_p, return_counts=True)
        truth[f"loc{loc_i}"] = f"b_bar{vals[cnts.argmax()]}"
        shares[f"loc{loc_i}"] = {f"b_bar{v}": c / len(land_p) for v, c in zip(vals, cnts)}

    # ---------------- names: shared names for ~35% of truth pairs, plus 6% wrong ones
    loc_names = {}
    bar_names = {}
    pairs = list(truth.items())
    rng.shuffle(pairs)
    used_b = set()
    k = 0
    for loc, bar in pairs[: int(len(pairs) * 0.35)]:
        if bar in used_b:
            continue
        used_b.add(bar)
        name = word(k)
        k += 1
        loc_names[loc] = name
        bar_names[bar] = name
    all_bars = [f"b_bar{i}" for i in range(1, n_bar + 1)]
    for loc, _bar in pairs[int(len(pairs) * 0.35): int(len(pairs) * 0.41)]:
        wrong = all_bars[rng.integers(len(all_bars))]
        if wrong in bar_names:
            continue
        name = word(k)
        k += 1
        loc_names[loc] = name
        bar_names[wrong] = name

    eu5 = SimpleNamespace(
        locations=locations, loc_order=loc_order, locations_png=os.path.join(tmp, "eu5", "locations.png"),
        root=os.path.join(tmp, "eu5"), loc=loc_names,
        land_locations=lambda: [k for k in loc_order if locations[k].kind == "land"])
    ck3 = SimpleNamespace(
        titles=titles, province_colors=province_colors, provinces_png=os.path.join(tmp, "ck3", "map_data", "provinces.png"),
        root=os.path.join(tmp, "ck3"), loc=bar_names, province_names={},
        sea_provinces={sea_id}, lake_provinces=set(), impassable_provinces=set(),
        county_of_barony=lambda b: titles[b].parent)
    return eu5, ck3, truth, shares


def test_alignment_recovers_truth():
    with tempfile.TemporaryDirectory() as tmp:
        eu5, ck3, truth, shares = build(tmp)
        res = MapMatcher(ck3, eu5, log=print, stride=2, cache=False, max_ctrl_dist=150).run()
        common = [l for l in truth if l in res.loc_to_barony]
        strict = sum(1 for l in common if res.loc_to_barony[l] == truth[l]) / max(1, len(common))
        # a pick is acceptable when that barony really covers a good part of the location
        correct = sum(1 for l in common if shares[l].get(res.loc_to_barony[l], 0) >= 0.3)
        recall = len(common) / len(truth)
        precision = correct / max(1, len(common))
        false_pos = [l for l in res.loc_to_barony if l not in truth]
        print(f"truth {len(truth)}  mapped {len(res.loc_to_barony)}  recall {recall:.3f}  "
              f"accuracy {precision:.3f} (strict {strict:.3f})  extra {len(false_pos)}")
        assert recall > 0.95
        assert precision > 0.93
        # the far island must stay unmapped
        assert len(false_pos) < 0.03 * len(truth)


if __name__ == "__main__":
    test_alignment_recovers_truth()
