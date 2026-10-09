"""The whole conversion as one call, shared by the GUI and the CLI."""
from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass, field

from .adjacency import location_adjacency
from .ck3game import load_ck3_game
from .ck3save import load_save
from .convert import Converter, Options
from .eu5game import load_eu5_game
from .geomap import MapMatcher, render_preview
from .mappings import update_realm_table
from .validate import check_mod, summarize
from .vanilla import load_vanilla
from .writer import ModWriter


@dataclass
class Paths:
    ck3_game: str
    eu5_game: str
    save: str
    out_dir: str


@dataclass
class Result:
    mod_root: str
    world: object
    preview_png: str | None = None
    seconds: float = 0.0
    warnings: list[str] = field(default_factory=list)


def run_conversion(paths: Paths, options: Options, log=print, progress=lambda f, msg="": None,
                   use_map_cache: bool = True) -> Result:
    t0 = time.time()
    progress(0.02, "Reading CK3 game files")
    ck3 = load_ck3_game(paths.ck3_game, log)
    progress(0.08, "Reading EU5 game files")
    eu5 = load_eu5_game(paths.eu5_game, log)
    dlc_setup = glob.glob(os.path.join(eu5.root, "dlc", "*", "main_menu", "setup", "start", "*.txt"))
    warnings: list[str] = []
    if dlc_setup:
        warnings.append(f"EU5 DLC setup files found ({len(dlc_setup)}); they are not rewritten by the converter")
    progress(0.14, "Reading the CK3 save")
    save = load_save(paths.save, log)
    progress(0.30, "Aligning the CK3 and EU5 maps")
    mm = MapMatcher(ck3, eu5, log, cache=use_map_cache)
    mm.progress = lambda f: progress(0.30 + 0.35 * f, "Aligning the CK3 and EU5 maps")
    mapres = mm.run()
    adj = None
    if options.split_exclaves or options.fill_enclaves:
        progress(0.64, "Finding location borders")
        try:
            adj = location_adjacency(eu5, log, use_cache=use_map_cache)
        except Exception as e:      # exclaves are optional; never fail the conversion over them
            log(f"(location borders unavailable, exclaves left as they are: {e})")
    progress(0.66, "Reading EU5's 1337 setup")
    vanilla = load_vanilla(eu5, log)
    progress(0.72, "Converting realms, people and land")
    world = Converter(save, ck3, eu5, mapres, vanilla, options, log, adjacency=adj).run()
    progress(0.85, "Writing the mod")
    root = ModWriter(world, eu5, ck3, save, vanilla, paths.out_dir, log).write()
    preview = None
    try:
        colors = {}
        for tag, c in world.countries.items():
            top = c
            while top.overlord and top.overlord in world.countries:
                top = world.countries[top.overlord]
            col = c.color if c.overlord is None else _mix(c.color, top.color)
            for loc in c.locations:
                colors[loc] = col
        preview = os.path.join(root, "save2mod_preview.png")
        img = render_preview(eu5, mm.preview_path(), colors, world.covered, preview)
        thumb = img.copy()
        thumb.thumbnail((512, 256))
        thumb.save(os.path.join(root, ".metadata", "thumbnail.png"))
    except Exception as e:     # preview is optional
        log(f"(map preview skipped: {e})")
        preview = None
    try:
        update_realm_table(world, eu5)
        log("Realm list updated (Mappings → Realms & tags): set a tag or a name there and convert again")
    except OSError as e:
        log(f"(couldn't update the realm list: {e})")
    progress(0.95, "Checking the mod")
    problems = self_check(root, eu5, log)
    if problems:
        warnings.append(f"self-check found {len(problems)} problem(s) - see the log / save2mod_report.txt; "
                        f"EU5 may crash with this mod")
    for w in warnings:
        log("WARNING: " + w)
    progress(1.0, "Done")
    return Result(mod_root=root, world=world, preview_png=preview, seconds=time.time() - t0, warnings=warnings)


def self_check(mod_root: str, eu5, log=print) -> list[str]:
    """Check the written setup against the rules vanilla follows; log and
    append the result to the report."""
    try:
        problems, stats = check_mod(mod_root, eu5)
    except Exception as e:          # never let the check break a conversion
        log(f"(self-check skipped: {e})")
        return []
    lines = [f"Self-check: {stats['setup_countries']} countries in setup, {stats['country_definitions']} defined, "
             f"{stats['characters']} characters - " + (f"{len(problems)} problem(s):" if problems else "OK")]
    lines += summarize(problems)
    for ln in lines:
        log(ln)
    try:
        with open(os.path.join(mod_root, "save2mod_report.txt"), "a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(lines) + "\n")
            if problems:
                fh.write("\nAll self-check problems:\n" + "\n".join("  " + p for p in problems) + "\n")
    except OSError:
        pass
    return problems


def _mix(a, b):
    return tuple(int(0.55 * x + 0.45 * y) for x, y in zip(a, b))
