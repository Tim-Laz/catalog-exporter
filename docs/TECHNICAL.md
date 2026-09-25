# Technical notes

This is the reference for developers and AI assistants. User-facing instructions are in
the [README](../README.md).

## Commands

`python3` on macOS, `py` (or `python`) on Windows:

```bash
python3 export.py "<link>"                       # everything -> output/<project>/  (~5–10 min, ~0.5 GB)
python3 export.py "<link>" --only floors,tours   # just some parts (writes REPORT_partial.md)
python3 export.py "<link>" --fresh               # delete this project's previous output first
python3 verify.py [output/<project>]             # re-check a finished export
```

`<link>` is the catalog viewer link, `https://view.<domain>/<org>/projectscene/<project>/…`.
Without it, the catalog in `DEFAULT_LINK` (`export.py`) is used. The API
(`api.<domain>`), CDN (`storagecdn.<domain>`) and page links are derived from the link.
Nothing else is host- or project-specific; the developer slug comes from the
organisation's name in the API.

| Flag | Meaning |
|---|---|
| `--only a,b` | Parts to run: `area`, `buildings`, `floors`, `tours`, `topviews`, `amenities`, `map`. The default is all except `map` (the map overview, 7680×4320). |
| `--fresh` | Remove `output/<project>` before starting |
| `--out DIR` | Output root (default: `output/` next to `export.py`) |
| `--project-slug` | Second part (default: the catalog's project name in kebab-case) |

Requirements:

- Windows 10/11 or macOS, Python 3.9+ (standard library only).
- `magick` (ImageMagick 7), which does all image work, including the previews (drawn
  from MVG files).
- Optional, for the printed-layout check:
  - macOS: Vision, compiled once with `swiftc` from the Xcode command line tools
  - Windows: `Windows.Media.Ocr` through Windows PowerShell 5.1, which needs an OCR
    language installed (English is standard)

  Without either, the check is skipped and noted in the report.

## Output

```
output/<project>/
├─ README.md, REPORT.md      what is where · data problems + automatic checks
├─ apartments.csv            one row per apartment
├─ 01_area/                  area.jpg 3840×2160 · area_mask.svg · area_preview.jpg · _source/
├─ 02_buildings/<building>/  <building>.jpg · <building>_floors-mask.svg · preview · _source/
├─ 03_floors/<building>/
│  ├─ floors-02-10-15/       background.jpg · plan.png/.webp · mask.svg · preview.jpg · contours.csv
│  └─ per-floor/             floor_1.svg … floor_15.svg
├─ 04_tours/<tour>/          <tour>_<NN>_<room>.jpg · tour.json · _preview/   (+ tours.csv)
├─ 05_top-views/             by-configuration/ · by-apartment/ · _source/
├─ 06_amenities/<category>/  NN_<name>.jpg   (+ amenities.csv, _overview/)
└─ _raw_api/                 API responses as-is
```

Conventions:

- **Names** are the catalog's, in kebab-case: `Tower A` → `tower-a`, `3BHK Duplex` →
  `3bhk-duplex`. Tours keep their name (`1BR`).
- **Masks** are SVG in the pixel space of their render (viewBox = image size), one
  `<path id>` per zone:
  - area: `id` = building slug
  - tower: `id` = floor number
  - floor: `id` = position, and apartment number = floor + position (floor 7, `03` → 703)
- **Floor background** is the catalog's single image, the plan drawn over a blurred aerial
  photo. The catalog has no plan-only file. `plan.png` is cut out of it by local sharpness
  (`images.cut_floor_plan`), full frame and transparent outside the plan.
- **Top views**: `<developer>_<project>_<building>_<layout-slug>_plan.webp`
  (by-configuration) and `…_<number>_plan.webp` (by-apartment). The upper floor of a
  duplex is `…_plan_floor-2.webp`. Files are WebP, long side ≤ 2560.
- **Tours**: only unique tours are downloaded. Preview-only scenes (1475×737) are
  rebuilt from the 512 px tile grid to 4096×2048, and the preview is kept in `_preview/`.

## Checks (`catalog_export/verify.py`, ~190 of them)

- **The export against the API:**
  - apartments per building, contours per floor, 15 floors per tower mask
  - masks inside their images, tower floors stacked bottom-up
  - every tour scene present, and hotspots pointing at real files
  - top views: one per apartment, byte-identical to their layout file
  - images decode and match their extension, no leftover `.part` files
- **File names** match the top-view pattern, with slugs free of `_` and upper case.
- **Printed layout vs catalog layout.** `ocr.py` reads "N BHK" on each floor plan with
  the OS's own OCR and compares it with the catalog's layout. Look-alike Cyrillic letters
  are normalised. The check is skipped when no OCR is available.

The steps also report problems in the catalog's own data:

- contours linked to other apartments
- apartments without a tour
- panoramas that are not 2:1
- visually duplicated amenity panoramas

## Code map

| File | Role |
|---|---|
| `export.py` | CLI, order of steps, output README text |
| `catalog_export/core.py` | HTTP with retry/resume, curl fallback, `Api`, Firebase → CDN URLs, ImageMagick helpers, `Report` |
| `catalog_export/steps.py` | `Ctx.load()` (API + scene roles), `floor_mapping()`, one `step_*` per output folder |
| `catalog_export/images.py` | Deep Zoom stitching, tour tile stitching, plan cut-out, duplicate detection |
| `catalog_export/svg.py` | reading the catalog's SVG layers, writing masks, path geometry, previews (ImageMagick + MVG) |
| `catalog_export/ocr.py` | printed-type reading, dispatching to `ocr.swift` (macOS, compiled once into `.cache/`) or `ocr_windows.ps1` (Windows) |
| `catalog_export/verify.py` | the checks |

## Catalog API (verified)

Base: `https://api.<domain>/publicapis/organization/<ORG>[/project/<PROJECT>]/<endpoint>`.
It is public and needs no auth. Responses are `{status, data}`, where `data` is usually a
dict keyed by id; some records keep their fields under `_doc` (`unwrap()`).

| Endpoint | Gives |
|---|---|
| `getOrganization` (org) | organisation name → developer slug (first part of top-view names) |
| `listProjectsFromOrganization` (org) | project name → project slug |
| `getListOfBuildings` | buildings + floors. Its `units` field is always empty. |
| `getListofUnits` | **all** apartments of the project. `building_id`/`floor_id` params are ignored; use each unit's `building_id`. |
| `getListOfUnitplan` | layouts: `tour_id`, `image_url` (top view). A duplex has `floor_unitplans` (lower, upper). |
| `ListTours` | tours → `images[]` (360 scenes) with `links[]` hotspots |
| `GetAmenities` | `category`, `name`, `file` (360 photo), `rotation` |
| `getAllScenes` | every viewer scene: `sceneData.background` and `svgData[].layers` |

How the endpoints connect:

- **Apartment → tour:** `unit.unitplan_id → unitplan.tour_id`. `unit.tour_id` is always null.
- **Scene roles:**
  - `type: identical_unitplan` → a floor scene (`floor_ids`, `building_id`)
  - `floor` layers → a tower
  - `pin` layers pointing at tower scenes → the area
  - `amenity` layers → the amenities overview
  - `root: true` → the map
- **Deep Zoom renders:** tiles live at `<dzi-without-.dzi>_files/<level>/<col>_<row>.jpeg`
  on `storagecdn.<domain>` (`cdn_url()`). The top level is ceil(log2(max(W,H))),
  with TileSize 254 and Overlap 1.
- **Tower and area layers** are `<g transform="translate(-x,-y)">` fragments whose
  coordinates are already in render pixels, so the translate is dropped.
- **Floor scenes** have one SVG (`svg_url`) with a `<g id="<layer_id>">` per contour. The
  layer name is the position (`01`), or the full number on floor 1 (`101`).
  `label_x/label_y` are always 0.
- **Tour tiles:** `storagecdn.<domain>/…/tours/<tour>/<scene>/lod_0/tile_<row>_<col>.jpg`, an 8×8
  grid holding a square-squeezed equirectangular image.

## Robustness (keep when changing code)

- **Downloads** go to `.part`, are checked, then renamed. `looks_valid` checks the header
  and the JPEG/PNG end marker, or the WebP RIFF size, without starting a process per
  file (slow on Windows). A cached image that fails the check is fetched again.
- **Truncated files:** a truncated image only produces an ImageMagick *warning*, so
  `decodes()` checks the warning text too.
- **Portability:**
  - every text file is read and written as UTF-8 explicitly (Windows defaults to cp1252)
  - the console is reconfigured so it never crashes on a character it can't show
  - long ImageMagick drawing commands go through `-draw @file.mvg`, because Windows
    caps the command line at 32k characters
- **Windows console:** QuickEdit is switched off while running, because a stray click
  would otherwise pause the process at its next output. System sleep is also blocked
  until the run ends. Both are restored at exit.
- **Console output:** the notes about the catalog data are written only to REPORT.md.
  The console shows their count, so the person running the tool doesn't see
  internal notes.
- **Stitched files** are written in a temp dir and moved into place.
- **Tour upgrades** are decided by the file's actual width, so an interrupted rebuild
  is redone.
- **Ctrl+C:** `download_many` cancels its queue and waits for the running downloads, so
  Windows can delete the temp folder and the friendly message is shown.
- **OCR never stops an export:** one failing image is skipped, and a crash of the OCR step
  turns it off with a note. The availability probe recognises a real generated image.
- **HTTP 4xx fails at once** (no retries); network errors and 5xx are retried.
- **Python without CA certificates** (python.org installs) falls back to the system `curl`.
- **Output is deterministic:** re-runs produce byte-identical files (PNG date chunks
  are excluded).
- **Partial runs** write `REPORT_partial.md`, so they never overwrite the full report.
