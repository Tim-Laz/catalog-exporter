#!/usr/bin/env python3
"""
Catalog exporter
================

Downloads everything an online 3D property catalog shows: area and tower renders with their
masks, floor plans with their backgrounds and apartment masks, unique 360 tours,
apartment top views, amenity 360 photos, and an apartments table that ties it all
together.

    py export.py                         # Windows (python3 on macOS): the default catalog
    py export.py "<catalog link>"        # another catalog
    py export.py --only floors,tours     # just some parts

Needs Python 3.8+ and ImageMagick 7 (the `magick` command):
    Windows:  winget install -e --id ImageMagick.ImageMagick
    macOS:    brew install imagemagick

It only READS the catalog's public API and the files that API points to.
Re-running is safe: files already downloaded are kept.
"""

import argparse, os, shutil, sys, time

from catalog_export import steps, verify
from catalog_export.core import (Api, NetworkError, Report, check_python, configure, log, require_tools,
                                 run_command, setup_console)

# The catalog exported when no link is given on the command line.
DEFAULT_LINK = "https://view.propvr.tech/RgvWHf/projectscene/6a5f2c591272c5bbc4d298cf/6a5f2d691272c5bbc4d2acad"
PARTS = ["area", "buildings", "floors", "tours", "topviews", "amenities", "map"]
DEFAULT_PARTS = [p for p in PARTS if p != "map"]

OUTPUT_README = """# {project}: catalog export

Made by `export.py` on {date}. All names (project, buildings, layouts, tours,
amenities) come from the source catalog. Open **REPORT.md** first: it lists the problems found
in the source data and the results of the automatic checks of this export.

| What | Where |
|---|---|
| All apartments: building, floor, number, layout, tour, top-view file, check against the floor plan, source links | `apartments.csv` |
| Tower-selection render + tower mask (id = building slug) | `01_area/` |
| Each tower's render + floor mask (id = floor number) | `02_buildings/<building>/` |
| Floors, one folder per group of identical floors: `background.jpg` as shown in the source catalog; `plan.png` / `plan.webp` = the plan cut out on transparency, same frame; `mask.svg` = apartment outlines (id = position: floor 7 + id 03 = apartment 703); `preview.jpg`; `contours.csv` | `03_floors/<building>/floors-XX-YY/` |
| The same apartment mask once per floor (`floor_<N>.svg`) | `03_floors/<building>/per-floor/` |
| 360 panoramas of the UNIQUE tours only (`<tour>_<NN>_<room>.jpg`), `tour.json` = hotspots and apartments, `tours.csv` = tour → apartments | `04_tours/` |
| Apartment top views (see below) | `05_top-views/` |
| Amenity 360s by source category, `amenities.csv` (duplicates marked), overview render with pins | `06_amenities/` |
| Raw API responses | `_raw_api/` |

Every mask is an SVG in the pixel space of its render (viewBox = image size), one
`<path>` per zone. Each mask has a `*_preview.jpg` next to it: the mask drawn over the render.

## Layouts vs the floor plan

On the floor previews every outline is labelled with the layout the source assigns. The
script also reads the type printed on the plan itself (the operating system's text recognition). Where
they differ, `apartments.csv` fills in "layout / tour according to the plan". In the source catalog
the tour and top view follow the layout, so a wrong layout means a wrong tour and top view.

## Top views

- `by-configuration/`: one file per layout per building,
  `<developer>_<project>_<building>_<layout>_plan.webp` (layout = slug of the source layout name).
- `by-apartment/`: one copy per apartment, `<developer>_<project>_<building>_<number>_plan.webp`.
- `…_plan_floor-2.webp`: the upper floor of a duplex.
"""


def main():
    setup_console()
    check_python()
    ap = argparse.ArgumentParser(description="Export everything an online 3D property catalog shows.")
    ap.add_argument("link", nargs="?", default=DEFAULT_LINK,
                    help="link to the catalog, https://view.<domain>/<org>/projectscene/<project>/... "
                         "(default: the catalog this tool is set up for)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"),
                    help="output folder (default: output/ next to this script)")
    ap.add_argument("--project-slug", default=None,
                    help="project slug for file names (default: catalog project name, kebab-case)")
    ap.add_argument("--fresh", action="store_true",
                    help="delete this project's previous output first (otherwise files are reused)")
    ap.add_argument("--only", default=",".join(DEFAULT_PARTS),
                    help=f"comma list of parts: {','.join(PARTS)} (default: all but map)")
    args = ap.parse_args()

    ids = configure(args.link)
    if not ids:
        ap.error("this is not a catalog link. Run the command without anything after export.py:\n"
                 f"  {run_command()} export.py")
    args.org, args.project = ids
    parts = [p.strip() for p in args.only.split(",") if p.strip()]
    unknown = set(parts) - set(PARTS)
    if unknown:
        ap.error(f"unknown part(s): {', '.join(sorted(unknown))}")
    require_tools()

    started = time.time()
    report = Report()
    ctx = steps.Ctx(Api(args.org, args.project), None, report, args.org, args.project,
                    None, args.project_slug)
    ctx.load()
    ctx.out = os.path.abspath(os.path.join(args.out, ctx.project_slug))
    if args.fresh and os.path.isdir(ctx.out):
        log(f"--fresh: removing previous output {ctx.out}")
        shutil.rmtree(ctx.out)
    ctx.root = os.path.dirname(os.path.abspath(__file__))
    ctx.api.save(os.path.join(ctx.out, "_raw_api"))
    log(f"Output: {ctx.out}\n")

    mapping = steps.floor_mapping(ctx)   # needed by the table even when floors are skipped
    if "map" in parts:
        steps.step_map(ctx)
    if "area" in parts:
        steps.step_area(ctx)
    if "buildings" in parts:
        steps.step_buildings(ctx)
    if "floors" in parts:
        steps.step_floors(ctx, mapping)
    if "tours" in parts:
        steps.step_tours(ctx)
    if "topviews" in parts:
        steps.step_topviews(ctx)
    if "amenities" in parts:
        steps.step_amenities(ctx)
    steps.step_table(ctx)

    date = time.strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(ctx.out, "README.md"), "w", encoding="utf-8") as f:
        f.write(OUTPUT_README.format(project=ctx.project_name, date=date))
    checks = None
    if set(DEFAULT_PARTS) <= set(parts):
        log("\nVerifying the export ...")
        try:
            checks = verify.run(ctx.out)
            report.add("Автоматические проверки", checks.markdown())
        except Exception as e:  # noqa: BLE001 — a broken check must not cost the export its report
            report.issue("Проверки", f"проверка выгрузки упала: {type(e).__name__}: {e}")
            log(f"Checks could not run ({type(e).__name__}); the export itself is complete.")
    # a partial run must not replace the full report
    report_name = "REPORT.md" if set(DEFAULT_PARTS) <= set(parts) else "REPORT_partial.md"
    report.write(os.path.join(ctx.out, report_name),
                 f"# Отчёт о выгрузке — {ctx.project_name}\n\n"
                 f"{date}, части: {', '.join(parts)}. Org `{args.org}`, project `{args.project}`.")

    log("\n" + "=" * 64)
    log(f"Done in {time.time() - started:.0f}s")
    log(f"Result: {ctx.out}")
    if checks:
        log(f"Checks: {len(checks.results) - len(checks.failed)}/{len(checks.results)} passed")
        for g, name, _, detail in checks.failed:
            log(f"  FAIL [{g}] {name} — {detail}")
    log(f"Notes about the catalog data: {len(report.issues)} (saved in {report_name}, nothing to do)")
    if checks:
        log("Next: compress the Result folder and send it to us (README, last step).")
    log("=" * 64)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nStopped. Run the same command again to continue — finished files are kept.")
        sys.exit(130)
    except PermissionError as e:
        log(f"\nERROR: a file is in use and cannot be written:\n  {e.filename}\n"
            "Close any file from the output folder that is open (for example apartments.csv in Excel)\n"
            "and run the same command again.")
        sys.exit(1)
    except NetworkError as e:
        log(f"\nERROR: could not download from the catalog after several tries:\n  {e}\n"
            "Check the internet connection and run the same command again — finished files are kept.")
        sys.exit(1)
