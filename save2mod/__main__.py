"""Command line: python -m save2mod [gui|convert|inspect] ..."""
from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="save2mod", description=f"CK3 EU5 Save-2-Mod {__version__}: convert a CK3 save into an EU5 mod")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the graphical front end (default)")

    c = sub.add_parser("convert", help="convert a save from the command line")
    c.add_argument("--save", required=True, help="CK3 .ck3 save file")
    c.add_argument("--ck3", help="CK3 install (or its game/ folder); auto-detected if omitted")
    c.add_argument("--eu5", help="EU5 install (or its game/ folder); auto-detected if omitted")
    c.add_argument("--out", help="EU5 mod folder to write into; auto-detected if omitted")
    c.add_argument("--name", default="CK3 Conversion", help="mod name")
    c.add_argument("--no-vanilla-buildings", action="store_true", help="drop EU5's own buildings in converted land")
    c.add_argument("--majority-pops-only", action="store_true",
                   help="only convert the majority pop group to CK3 culture/religion")
    c.add_argument("--no-control", action="store_true", help="don't transfer CK3 county control")
    c.add_argument("--dev-multiplier", type=float, default=1.0)
    c.add_argument("--new-tags-only", action="store_true", help="never reuse EU5 tags")
    c.add_argument("--no-hre", action="store_true", help="don't rebuild the HRE")
    c.add_argument("--buildings-main-location", action="store_true",
                   help="put a barony's buildings only in its main EU5 location")
    c.add_argument("--no-map-cache", action="store_true", help="recompute the map alignment")
    c.add_argument("--no-characters", action="store_true", help="let EU5 generate rulers instead of converting them")
    c.add_argument("--ck3-title-names", action="store_true",
                   help="use the names CK3 shows (historical names, renames) instead of CK3's default title names")
    c.add_argument("--eu5-tag-names", action="store_true",
                   help="keep EU5's tag-specific naming rules for reused tags (a CK3 Egypt on MAM shows as the Mamluks)")
    c.add_argument("--game-version", default="", help="EU5 version for the mod metadata, e.g. 1.4.*")
    c.add_argument("--subject-tier", choices=["county", "duchy", "kingdom", "empire"], default="kingdom",
                   help="lowest CK3 vassal tier that becomes an EU5 subject (default kingdom)")
    c.add_argument("--split-admin-realms", action="store_true",
                   help="let governors of administrative realms (Byzantium, China) become subjects too")
    c.add_argument("--no-fill-pockets", action="store_true",
                   help="leave unmapped EU5 land surrounded by CK3 land vanilla")
    c.add_argument("--keep-exclaves", action="store_true", help="don't turn detached land into vassals")
    c.add_argument("--sea-hops", type=int, default=2,
                   help="sea/lake/wasteland locations a crossing may pass and still count as connected (default 2)")

    i = sub.add_parser("inspect", help="print the structure of a save (for bug reports)")
    i.add_argument("save")

    k = sub.add_parser("check", help="check a generated mod's setup against vanilla's rules")
    k.add_argument("mod", help="the generated mod folder")
    k.add_argument("--eu5", help="EU5 install (or its game/ folder); auto-detected if omitted")

    a = ap.parse_args(argv)
    if a.cmd in (None, "gui"):
        from .gui import main as gui_main
        return gui_main()
    if a.cmd == "inspect":
        from .ck3save import summarize_structure
        summarize_structure(a.save)
        return 0
    from . import paths as P
    if a.cmd == "check":
        from .eu5game import load_eu5_game
        from .validate import check_mod
        eu5_dir = a.eu5 or P.find_eu5_game()
        if not eu5_dir:
            print("Could not auto-detect EU5; pass --eu5.")
            return 2
        eu5g = load_eu5_game(eu5_dir, print)
        problems, stats = check_mod(a.mod, eu5g)
        print(stats)
        for p in problems:
            print(p)
        print(f"{len(problems)} problem(s)")
        return 1 if problems else 0
    from .convert import Options
    from .pipeline import Paths, run_conversion
    ck3 = a.ck3 or P.find_ck3_game()
    eu5 = a.eu5 or P.find_eu5_game()
    out = a.out or P.find_eu5_mod_dir()
    missing = [n for n, v in (("--ck3", ck3), ("--eu5", eu5), ("--out", out)) if not v]
    if missing:
        print("Could not auto-detect: " + ", ".join(missing) + ". Please pass them explicitly.")
        return 2
    opts = Options(keep_vanilla_buildings=not a.no_vanilla_buildings, all_pops_take_ck3=not a.majority_pops_only,
                   transfer_control=not a.no_control, dev_multiplier=a.dev_multiplier,
                   reuse_tags=not a.new_tags_only, rebuild_hre=not a.no_hre,
                   building_placement="main" if a.buildings_main_location else "all", mod_name=a.name,
                   convert_characters=not a.no_characters, game_version=a.game_version,
                   subject_min_tier={"county": 1, "duchy": 2, "kingdom": 3, "empire": 4}[a.subject_tier],
                   keep_admin_realms_whole=not a.split_admin_realms, split_exclaves=not a.keep_exclaves,
                   fill_enclaves=not a.no_fill_pockets,
                   exclave_sea_hop=a.sea_hops, ck3_title_names=a.ck3_title_names,
                   own_names_for_tag_rules=not a.eu5_tag_names)

    def prog(f, msg=""):
        print(f"[{int(f * 100):3d}%] {msg}", flush=True)
    res = run_conversion(Paths(ck3, eu5, a.save, out), opts, print, prog, use_map_cache=not a.no_map_cache)
    print(f"Done in {res.seconds:.0f}s -> {res.mod_root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
