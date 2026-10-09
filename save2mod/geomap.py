"""Map every EU5 location to the CK3 barony that covers it.

The two games use different map projections, so the maps are aligned
automatically:

1. Both province bitmaps are read and each province's centroid is measured.
2. CK3 baronies/counties and EU5 locations that share a name (``b_paris`` /
   ``paris``) become control points - usually several thousand.
3. A 2-D polynomial EU5->CK3 transform is fitted with RANSAC (bad name matches
   are thrown out), then local distortions are removed by interpolating the
   remaining residuals with a thin-plate spline.
4. Every (sub-sampled) EU5 land pixel is projected into CK3 space and the CK3
   province under it is looked up. Each EU5 location takes the barony that
   covers most of it; locations mostly outside CK3's land stay vanilla.

Results are cached per game-version, and a user overrides file can pin
individual locations.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .mappings import norm, tables_dir

Image.MAX_IMAGE_PIXELS = None


@dataclass
class MapResult:
    loc_to_barony: dict[str, str] = field(default_factory=dict)
    loc_share: dict[str, float] = field(default_factory=dict)
    barony_to_locs: dict[str, list[str]] = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    def county_of_loc(self, loc: str, ck3) -> str | None:
        b = self.loc_to_barony.get(loc)
        return ck3.county_of_barony(b) if b else None

    def to_json(self) -> dict:
        return {"loc_to_barony": self.loc_to_barony, "loc_share": self.loc_share, "stats": self.stats}

    @classmethod
    def from_json(cls, d: dict) -> "MapResult":
        r = cls(loc_to_barony=d["loc_to_barony"], loc_share=d.get("loc_share", {}), stats=d.get("stats", {}))
        r.rebuild_inverse()
        return r

    def rebuild_inverse(self) -> None:
        inv: dict[str, list[str]] = {}
        for loc, b in self.loc_to_barony.items():
            inv.setdefault(b, []).append(loc)
        self.barony_to_locs = inv


# ------------------------------------------------------------------ images
def _packed(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def index_image(path: str, colors: dict[int, int], log=print, band: int = 512):
    """Return (index_map uint16/uint32 [H,W] with 0 = unknown, centroids
    float64 [N+1,2] (x,y), pixel counts [N+1]) for an image whose pixel colors
    are keys of ``colors`` (color -> 1-based index)."""
    img = Image.open(path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    log(f"Reading {os.path.basename(path)} ({w}x{h}) ...")
    arr = np.asarray(img)
    keys = np.array(sorted(colors), dtype=np.uint32)
    vals = np.array([colors[k] for k in keys.tolist()], dtype=np.uint32)
    n = int(vals.max()) + 1 if len(vals) else 1
    dtype = np.uint16 if n < 65535 else np.uint32
    out = np.zeros((h, w), dtype=dtype)
    sx = np.zeros(n)
    sy = np.zeros(n)
    cnt = np.zeros(n)
    xs = np.arange(w, dtype=np.float64)
    for y0 in range(0, h, band):
        p = _packed(arr[y0:y0 + band])
        pos = np.searchsorted(keys, p)
        pos = np.clip(pos, 0, len(keys) - 1)
        hit = keys[pos] == p
        idx = np.where(hit, vals[pos], 0).astype(dtype)
        out[y0:y0 + band] = idx
        rows = idx.shape[0]
        flat = idx.ravel().astype(np.int64)
        cnt += np.bincount(flat, minlength=n)
        sx += np.bincount(flat, weights=np.tile(xs, rows), minlength=n)
        sy += np.bincount(flat, weights=np.repeat(np.arange(y0, y0 + rows, dtype=np.float64), w), minlength=n)
    del arr
    with np.errstate(invalid="ignore", divide="ignore"):
        cent = np.stack([sx / cnt, sy / cnt], axis=1)
    return out, cent, cnt, (w, h)


# ----------------------------------------------------------- transform fit
class PolyRBF:
    """EU5 (x,y) -> CK3 (x,y): polynomial + optional smoothed thin-plate
    correction of the residuals."""

    def __init__(self, deg: int, src_norm, coef, rbf=None):
        self.deg = deg
        self.src_norm = src_norm   # (cx, cy, sx, sy)
        self.coef = coef
        self.rbf = rbf

    @staticmethod
    def feats(xy: np.ndarray, deg: int, nrm) -> np.ndarray:
        x = (xy[:, 0] - nrm[0]) / nrm[2]
        y = (xy[:, 1] - nrm[1]) / nrm[3]
        cols = [x ** i * y ** j for i in range(deg + 1) for j in range(deg + 1 - i)]
        return np.stack(cols, axis=1)

    def _n(self, xy: np.ndarray) -> np.ndarray:
        return np.column_stack([(xy[:, 0] - self.src_norm[0]) / self.src_norm[2],
                                (xy[:, 1] - self.src_norm[1]) / self.src_norm[3]])

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        out = self.feats(xy, self.deg, self.src_norm) @ self.coef
        if self.rbf is not None:
            out = out + self.rbf(self._n(xy))
        return out


def _ransac_inliers(F: np.ndarray, dst: np.ndarray, iters: int, rng) -> tuple[np.ndarray, float]:
    n, k = F.shape
    scale = np.median(np.abs(dst - np.median(dst, axis=0))) + 1.0
    thresh = max(10.0, scale * 0.02)
    best, best_n = None, -1
    sample = min(n, k + 4)
    for _ in range(iters):
        idx = rng.choice(n, sample, replace=False)
        try:
            A, *_ = np.linalg.lstsq(F[idx], dst[idx], rcond=None)
        except np.linalg.LinAlgError:
            continue
        inl = np.linalg.norm(F @ A - dst, axis=1) < thresh * 3
        c = int(inl.sum())
        if c > best_n:
            best_n, best = c, inl
    inl = best
    for _ in range(10):
        A, *_ = np.linalg.lstsq(F[inl], dst[inl], rcond=None)
        r = np.linalg.norm(F @ A - dst, axis=1)
        med = np.median(r[inl])
        new = r < max(thresh, 4.0 * med)
        if (new == inl).all():
            break
        inl = new
    return inl, thresh


def _cv_error(src, dst, deg, nrm, smoothing, folds, rng) -> float:
    from scipy.interpolate import RBFInterpolator
    n = len(src)
    order = rng.permutation(n)
    errs = []
    for f in range(folds):
        test = order[f::folds]
        train = np.setdiff1d(order, test)
        F = PolyRBF.feats(src[train], deg, nrm)
        A, *_ = np.linalg.lstsq(F, dst[train], rcond=None)
        pred = PolyRBF.feats(src[test], deg, nrm) @ A
        if smoothing is not None:
            tr = PolyRBF(deg, nrm, A)
            res = dst[train] - F @ A
            try:
                rbf = RBFInterpolator(tr._n(src[train]), res, kernel="thin_plate_spline",
                                      smoothing=smoothing)
                pred = pred + rbf(tr._n(src[test]))
            except Exception:
                return float("inf")
        errs.append(np.linalg.norm(pred - dst[test], axis=1))
    return float(np.median(np.concatenate(errs)))


class _ChunkedRBF:
    """Evaluate a global RBF in chunks to bound memory."""

    def __init__(self, rbf, chunk: int = 20000):
        self.rbf = rbf
        self.chunk = chunk

    def __call__(self, xy: np.ndarray) -> np.ndarray:
        if len(xy) <= self.chunk:
            return self.rbf(xy)
        return np.concatenate([self.rbf(xy[i:i + self.chunk]) for i in range(0, len(xy), self.chunk)])


def fit_transform(src: np.ndarray, dst: np.ndarray, *, iters: int = 3000,
                  seed: int = 0, log=print) -> tuple[PolyRBF, np.ndarray]:
    """Robustly fit dst ~ f(src). RANSAC (cubic) removes wrong name matches;
    polynomial degree and residual-spline smoothing are then chosen by
    cross-validation. Returns (transform, inlier mask)."""
    if len(src) < 20:
        raise ValueError(f"only {len(src)} usable control points were found - the two maps could not be "
                         f"aligned. Are the CK3/EU5 folders correct?")
    nrm = (src[:, 0].mean(), src[:, 1].mean(), max(src[:, 0].std(), 1.0), max(src[:, 1].std(), 1.0))
    rng = np.random.default_rng(seed)
    inl, _thresh = _ransac_inliers(PolyRBF.feats(src, 3, nrm), dst, iters, rng)
    S, D = src[inl], dst[inl]
    if len(S) > 5000:                       # keep the spline system small
        keep = rng.choice(len(S), 5000, replace=False)
        S, D = S[keep], D[keep]
    best = (float("inf"), 3, None)
    for deg in (2, 3, 4, 5):
        if len(S) < (deg + 1) * (deg + 2) * 3:
            continue
        for sm in (None, 1000.0, 100.0, 10.0, 1.0, 0.1):
            e = _cv_error(S, D, deg, nrm, sm, 5, np.random.default_rng(seed + 1))
            if e < best[0] * (0.97 if sm is not None else 1.0):
                best = (e, deg, sm)
    err, deg, sm = best
    F = PolyRBF.feats(S, deg, nrm)
    A, *_ = np.linalg.lstsq(F, D, rcond=None)
    tr = PolyRBF(deg, nrm, A)
    if sm is not None:
        from scipy.interpolate import RBFInterpolator
        tr.rbf = _ChunkedRBF(RBFInterpolator(tr._n(S), D - F @ A, kernel="thin_plate_spline", smoothing=sm))
    log(f"Map alignment: {int(inl.sum())}/{len(src)} control points kept; degree {deg}, "
        f"local correction {'off' if sm is None else sm}; cross-validated error {err:.1f}px")
    return tr, inl


# ---------------------------------------------------------------- matcher
def _file_sig(paths: list[str]) -> str:
    h = hashlib.sha1()
    for p in paths:
        try:
            st = os.stat(p)
            h.update(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode())
        except OSError:
            h.update(p.encode())
    return h.hexdigest()[:16]


def _names_ck3(ck3) -> dict[str, list[tuple[str, str]]]:
    """normalized name -> list of (kind, key) where kind is 'b' or 'c'."""
    out: dict[str, list[tuple[str, str]]] = {}

    def add(name: str, item):
        n = norm(name)
        if len(n) >= 4:
            lst = out.setdefault(n, [])
            if item not in lst:
                lst.append(item)

    for key, t in ck3.titles.items():
        if t.tier == 0 and t.province is not None:
            add(key[2:], ("b", key))
            loc = ck3.loc.get(key)
            if loc:
                add(loc, ("b", key))
            pn = ck3.province_names.get(t.province)
            if pn:
                add(pn, ("b", key))
        elif t.tier == 1:
            add(key[2:], ("c", key))
            loc = ck3.loc.get(key)
            if loc:
                add(loc, ("c", key))
    return out


def _names_eu5(eu5, land: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k in land:
        for name in {k, eu5.loc.get(k, "")}:
            n = norm(name)
            if len(n) >= 4:
                lst = out.setdefault(n, [])
                if k not in lst:
                    lst.append(k)
    return out


class MapMatcher:
    def __init__(self, ck3, eu5, log=print, *, stride: int = 2, cache: bool = True,
                 max_ctrl_dist: float = 450.0, min_share: float = 0.5):
        self.ck3 = ck3
        self.eu5 = eu5
        self.log = log
        self.stride = stride
        self.use_cache = cache
        self.max_ctrl_dist = max_ctrl_dist
        self.min_share = min_share
        self.progress = lambda f: None

    # cache -----------------------------------------------------------------
    def _cache_path(self) -> str:
        sig = _file_sig([self.eu5.locations_png, self.ck3.provinces_png,
                         os.path.join(self.ck3.root, "map_data", "definition.csv"),
                         os.path.join(self.eu5.root, "in_game", "map_data", "definitions.txt"),
                         os.path.join(self.ck3.root, "common", "landed_titles", "00_landed_titles.txt")])
        d = os.path.join(os.path.dirname(tables_dir()), "cache")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"map_{sig}_s{self.stride}_v2.json")   # v2: CK3 mountains are neutral

    def preview_path(self) -> str:
        return self._cache_path().replace(".json", "_preview.npy")

    def run(self) -> MapResult:
        cp = self._cache_path()
        if self.use_cache and os.path.exists(cp):
            with open(cp, encoding="utf-8") as fh:
                res = MapResult.from_json(json.load(fh))
            self.log(f"Loaded cached map alignment ({len(res.loc_to_barony)} locations mapped)")
        else:
            res = self._compute()
            with open(cp, "w", encoding="utf-8") as fh:
                json.dump(res.to_json(), fh)
        self._apply_overrides(res)
        return res

    def _apply_overrides(self, res: MapResult) -> None:
        path = os.path.join(tables_dir(), "map_overrides.csv")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# eu5_location,ck3_barony   (use - as barony to force a location to stay vanilla)\n"
                         "eu5_location,ck3_barony\n")
            return
        n = 0
        with open(path, encoding="utf-8-sig") as fh:
            lines = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
        for row in csv.DictReader(lines):
            loc, bar = (row.get("eu5_location") or "").strip(), (row.get("ck3_barony") or "").strip()
            if not loc:
                continue
            if bar in ("", "-"):
                res.loc_to_barony.pop(loc, None)
            elif bar in self.ck3.titles:
                res.loc_to_barony[loc] = bar
                res.loc_share[loc] = 1.0
            n += 1
        if n:
            res.rebuild_inverse()
            self.log(f"Applied {n} manual map overrides")

    # core ------------------------------------------------------------------
    def _compute(self) -> MapResult:
        ck3, eu5, log = self.ck3, self.eu5, self.log
        land = eu5.land_locations()
        # --- EU5 index
        eu5_keys = eu5.loc_order
        eu5_colors = {eu5.locations[k].color: i + 1 for i, k in enumerate(eu5_keys)}
        e_idx, e_cent, e_cnt, (ew, eh) = index_image(eu5.locations_png, eu5_colors, log)
        try:
            np.save(self.preview_path(), e_idx[::8, ::8].astype(np.uint16))
        except OSError:
            pass
        self.progress(0.25)
        # --- CK3 index (by province id)
        ck_colors = {}
        for pid, (r, g, b) in ck3.province_colors.items():
            ck_colors.setdefault((r << 16) | (g << 8) | b, pid)
        c_idx, c_cent, c_cnt, (cw, chh) = index_image(ck3.provinces_png, ck_colors, log)
        self.progress(0.45)

        # --- control points
        ck_names = _names_ck3(ck3)
        eu_names = _names_eu5(eu5, land)
        prov_of = {k: t.province for k, t in ck3.titles.items() if t.tier == 0 and t.province}
        county_cap: dict[str, int] = {}
        for k, t in ck3.titles.items():
            if t.tier == 1:
                bars = [c for c in t.children if c in prov_of]
                if bars:
                    county_cap[k] = prov_of[bars[0]]
        src, dst = [], []
        eu_pos = {k: i + 1 for i, k in enumerate(eu5_keys)}
        for n, items in ck_names.items():
            locs = eu_names.get(n)
            if not locs or len(locs) != 1:
                continue
            bars = [it for it in items if it[0] == "b"]
            if len(bars) == 1:
                kind, key = bars[0]
            elif not bars and len(items) == 1:
                kind, key = items[0]
            else:
                continue
            pid = prov_of.get(key) if kind == "b" else county_cap.get(key)
            if pid is None or pid >= len(c_cnt) or c_cnt[pid] == 0:
                continue
            ei = eu_pos[locs[0]]
            if ei >= len(e_cnt) or e_cnt[ei] == 0:
                continue
            src.append(e_cent[ei])
            dst.append(c_cent[pid])
        src_a = np.array(src, dtype=np.float64)
        dst_a = np.array(dst, dtype=np.float64)
        log(f"Found {len(src_a)} name-matched control points")
        tr, inl = fit_transform(src_a, dst_a, log=log)
        ctrl = src_a[inl]
        self.progress(0.6)

        # --- which CK3 provinces are land baronies
        n_c = len(c_cnt)
        is_bar = np.zeros(n_c + 1, dtype=bool)
        is_imp = np.zeros(n_c + 1, dtype=bool)
        for pid in ck3.impassable_provinces:
            if 0 <= pid < n_c:
                is_imp[pid] = True
        imp_hits = np.zeros(len(e_cnt), dtype=np.int64)
        bar_of_pid: dict[int, str] = {}
        for key, pid in prov_of.items():
            if pid < n_c and pid not in ck3.sea_provinces and pid not in ck3.lake_provinces \
                    and pid not in ck3.impassable_provinces:
                is_bar[pid] = True
                bar_of_pid[pid] = key

        # --- project the EU5 grid into CK3 space, band by band
        from scipy.spatial import cKDTree
        tree = cKDTree(ctrl)
        s = self.stride
        coarse = 8                                    # coarse step in stride units
        gx = np.arange(0, ew, s * coarse, dtype=np.float64)
        pair_counts: dict[int, int] = {}
        tot = np.zeros(len(e_cnt), dtype=np.int64)
        band_rows = 64 * coarse
        H = eh
        for y0 in range(0, H, s * band_rows):
            ys = np.arange(y0, min(H, y0 + s * band_rows), s)
            if len(ys) == 0:
                continue
            gy = np.arange(ys[0], ys[-1] + s * coarse + 1, s * coarse, dtype=np.float64)
            GX, GY = np.meshgrid(gx, gy)
            pts = np.stack([GX.ravel(), GY.ravel()], axis=1)
            dist, _ = tree.query(pts, k=1, distance_upper_bound=self.max_ctrl_dist * 2)
            valid_c = (dist <= self.max_ctrl_dist).reshape(GY.shape)
            if not valid_c.any():
                # still count location totals for share computation
                sub = e_idx[ys][:, ::s]
                tot += np.bincount(sub.ravel().astype(np.int64), minlength=len(tot))
                continue
            mapped = np.full((pts.shape[0], 2), np.nan)
            vi = valid_c.ravel()
            mapped[vi] = tr(pts[vi])
            MX = mapped[:, 0].reshape(GY.shape)
            MY = mapped[:, 1].reshape(GY.shape)
            # bilinear upsample to the stride grid
            xs_full = np.arange(0, ew, s, dtype=np.float64)
            fx = xs_full / (s * coarse)
            fy = (ys - gy[0]) / (s * coarse)
            x0 = np.clip(np.floor(fx).astype(int), 0, len(gx) - 2 if len(gx) > 1 else 0)
            y0i = np.clip(np.floor(fy).astype(int), 0, len(gy) - 2 if len(gy) > 1 else 0)
            tx = (fx - x0)[None, :]
            ty = (fy - y0i)[:, None]

            def interp(M):
                a = M[y0i][:, x0]
                b = M[y0i][:, np.minimum(x0 + 1, M.shape[1] - 1)]
                c = M[np.minimum(y0i + 1, M.shape[0] - 1)][:, x0]
                d = M[np.minimum(y0i + 1, M.shape[0] - 1)][:, np.minimum(x0 + 1, M.shape[1] - 1)]
                return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

            PX = interp(MX)
            PY = interp(MY)
            sub = e_idx[ys][:, ::s].astype(np.int64)
            tot += np.bincount(sub.ravel(), minlength=len(tot))
            ok = np.isfinite(PX) & np.isfinite(PY) & (sub > 0)
            PX = np.where(np.isfinite(PX), PX, -1.0)
            PY = np.where(np.isfinite(PY), PY, -1.0)
            ix = np.round(PX).astype(np.int64)
            iy = np.round(PY).astype(np.int64)
            ok &= (ix >= 0) & (ix < cw) & (iy >= 0) & (iy < chh)
            if not ok.any():
                continue
            pid = np.zeros_like(sub)
            pid[ok] = c_idx[iy[ok], ix[ok]]
            # CK3 impassable mountains don't count against a location (the Alps'
            # valleys are EU5 land but mostly CK3 wasteland)
            imp = ok & is_imp[np.minimum(pid, n_c)]
            if imp.any():
                imp_hits += np.bincount(sub[imp], minlength=len(imp_hits))
            ok &= is_bar[np.minimum(pid, n_c)]
            if not ok.any():
                continue
            key = sub[ok] * 100000 + pid[ok]
            u, c = np.unique(key, return_counts=True)
            for kk, cc in zip(u.tolist(), c.tolist()):
                pair_counts[kk] = pair_counts.get(kk, 0) + cc
            self.progress(0.6 + 0.35 * min(1.0, (y0 + s * band_rows) / H))

        # --- choose the majority barony per EU5 location; a location counts as
        #     covered when most of it lies on CK3 barony land (any barony)
        best: dict[int, tuple[int, int]] = {}
        land_hits: dict[int, int] = {}
        for kk, cc in pair_counts.items():
            ei, pid = divmod(kk, 100000)
            land_hits[ei] = land_hits.get(ei, 0) + cc
            if ei not in best or cc > best[ei][1]:
                best[ei] = (pid, cc)
        res = MapResult()
        land_set = set(land)
        for ei, (pid, cc) in best.items():
            loc = eu5_keys[ei - 1]
            if loc not in land_set:
                continue
            covered = land_hits[ei] / max(1, tot[ei] - imp_hits[ei])
            if covered < self.min_share or land_hits[ei] < 0.05 * tot[ei]:
                continue
            res.loc_to_barony[loc] = bar_of_pid[pid]
            res.loc_share[loc] = round(float(cc / max(1, land_hits[ei])), 3)
        res.rebuild_inverse()
        res.stats = {"control_points": int(len(src_a)), "inliers": int(inl.sum()),
                     "mapped_locations": len(res.loc_to_barony), "land_locations": len(land),
                     "baronies_used": len(res.barony_to_locs)}
        log(f"Mapped {len(res.loc_to_barony)} of {len(land)} EU5 land locations onto "
            f"{len(res.barony_to_locs)} CK3 baronies")
        self.progress(1.0)
        return res


def render_preview(eu5, preview_npy: str, owner_color: dict[str, tuple[int, int, int]],
                   covered: set[str] | None = None, out_png: str | None = None, scale: int = 1):
    """Render a small political map: converted owners in their colours, land
    that stays vanilla dark grey, water blue. Returns a PIL image."""
    idx = np.load(preview_npy)
    n = len(eu5.loc_order) + 1
    pal = np.zeros((n, 3), np.uint8)
    pal[0] = (20, 24, 36)
    for i, k in enumerate(eu5.loc_order, start=1):
        li = eu5.locations[k]
        if li.kind in ("sea", "lake"):
            pal[i] = (38, 70, 110)
        elif li.kind in ("wasteland", "corridor"):
            pal[i] = (95, 90, 80)
        else:
            pal[i] = owner_color.get(k, (58, 56, 52) if (covered is None or k not in covered) else (200, 200, 200))
    img = pal[np.minimum(idx, n - 1)]
    # borders between different owners
    diff = np.zeros(idx.shape, bool)
    diff[:, 1:] |= (img[:, 1:] != img[:, :-1]).any(axis=2)
    diff[1:, :] |= (img[1:, :] != img[:-1, :]).any(axis=2)
    img[diff] = (img[diff] * 0.55).astype(np.uint8)
    im = Image.fromarray(img)
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    if out_png:
        im.save(out_png)
    return im
