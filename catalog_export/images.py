"""Image assembly: Deep Zoom renders, tiled 360 panoramas, floor-plan cut-outs."""

import math, os, re, shutil, subprocess, tempfile

from .core import download_many, get_text, head_ok, magick

# --------------------------------------------------------------------------- #
# Deep Zoom (OpenSeadragon) renders: area, towers, amenities overview
# --------------------------------------------------------------------------- #


def dzi_info(dzi_url):
    xml = get_text(dzi_url)
    num = lambda k: int(re.search(rf'{k}="(\d+)"', xml).group(1))  # noqa: E731
    fmt = re.search(r'Format="(\w+)"', xml).group(1)
    return {"width": num("Width"), "height": num("Height"),
            "tile": num("TileSize"), "overlap": num("Overlap"), "format": fmt}


def stitch_dzi(dzi_url, out_path):
    """Rebuild the full-resolution render from the top Deep Zoom level.
    The catalog never serves the render as one file — the viewer paints tiles on a canvas."""
    info = dzi_info(dzi_url)
    w, h, ts, ov = info["width"], info["height"], info["tile"], info["overlap"]
    level = math.ceil(math.log2(max(w, h)))
    cols, rows = math.ceil(w / ts), math.ceil(h / ts)
    base = dzi_url[: -len(".dzi")] + f"_files/{level}"
    with tempfile.TemporaryDirectory() as tmp:
        tiles = {(c, r): os.path.join(tmp, f"{c}_{r}.{info['format']}")
                 for c in range(cols) for r in range(rows)}
        download_many([(f"{base}/{c}_{r}.{info['format']}", p) for (c, r), p in tiles.items()])
        col_files = []
        for c in range(cols):
            args = []
            for r in range(rows):
                # every tile except the first row/column carries `overlap` extra pixels
                sx, sy = (ov if c else 0), (ov if r else 0)
                cw, ch = min(ts, w - c * ts), min(ts, h - r * ts)
                args += ["(", tiles[(c, r)], "-crop", f"{cw}x{ch}+{sx}+{sy}", "+repage", ")"]
            col = os.path.join(tmp, f"col_{c}.png")
            magick(*args, "-append", col)
            col_files.append(col)
        magick(*col_files, "+append", "-quality", "95", "jpg:" + out_path + ".part")
        os.replace(out_path + ".part", out_path)   # never leave a half-written render behind
    return w, h, cols * rows


# --------------------------------------------------------------------------- #
# 360 panoramas: some scenes are served only at preview size; rebuild from tiles
# --------------------------------------------------------------------------- #

TOUR_TILE = 512


def stitch_pano_tiles(tile_base, out_path):
    """tile_base = .../tours/<tour>/<scene>/lod_0 holding tile_<row>_<col>.jpg.
    The tile grid stores the equirectangular image squeezed into a square; the
    result is resized back to 2:1. Returns (w, h) or None when there are no tiles."""
    if not head_ok(f"{tile_base}/tile_0_0.jpg"):
        return None
    cols = 0
    while cols < 64 and head_ok(f"{tile_base}/tile_0_{cols}.jpg"):
        cols += 1
    rows = 0
    while rows < 64 and head_ok(f"{tile_base}/tile_{rows}_0.jpg"):
        rows += 1
    w, h = cols * TOUR_TILE, cols * TOUR_TILE // 2
    with tempfile.TemporaryDirectory() as tmp:
        grid = {(r, c): os.path.join(tmp, f"t_{r}_{c}.jpg") for r in range(rows) for c in range(cols)}
        download_many([(f"{tile_base}/tile_{r}_{c}.jpg", p) for (r, c), p in grid.items()])
        row_files = []
        for r in range(rows):
            row = os.path.join(tmp, f"row_{r}.png")
            magick(*[grid[(r, c)] for c in range(cols)], "+append", row)
            row_files.append(row)
        square = os.path.join(tmp, "square.png")
        magick(*row_files, "-append", square)
        magick(square, "-resize", f"{w}x{h}!", "-quality", "92", "jpg:" + out_path + ".part")
        os.replace(out_path + ".part", out_path)
    return w, h


# --------------------------------------------------------------------------- #
# Floor plan cut-out
# --------------------------------------------------------------------------- #


def _plan_mask(background, out, open_disk):
    magick(background, "-resize", "1920x1080!", "-colorspace", "Gray",
           "-statistic", "StandardDeviation", "5x5", "-threshold", "4%",
           "-morphology", "Close", "Disk:4",
           "-bordercolor", "black", "-border", "1",
           "-fill", "red", "-draw", "color 0,0 floodfill",
           "-fill", "white", "+opaque", "red", "-fill", "black", "-opaque", "red",
           "-shave", "1x1", "-morphology", "Open", f"Disk:{open_disk}",
           "-define", "connected-components:area-threshold=20000",
           "-define", "connected-components:mean-color=true",
           "-connected-components", "8", out)
    return float(subprocess.check_output(["magick", out, "-format", "%[fx:mean]", "info:"],
                                         stderr=subprocess.DEVNULL))


def cut_floor_plan(background, out_png, width, height):
    """The floor scene is one flat image: a sharp plan drawn over a blurred aerial
    photo. No plan-only file exists, so the plan is separated by local sharpness:
    std-dev 5x5 -> threshold -> close gaps -> fill holes -> open (drops thin slivers of
    background along the edge) -> keep the big component. Computed at 1920 wide so
    thresholds suit every scene. The strongest opening that keeps >= 99% of the plan
    is used — too strong an opening can split a plan at a narrow waist.
    Output keeps the full frame (transparent outside the plan) so it lines up 1:1
    with the background and the mask. Returns the plan bbox 'WxH+X+Y'."""
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "m4.png")
        area4 = _plan_mask(background, base, 4)
        mask = base
        for disk in (8, 6):
            candidate = os.path.join(tmp, f"m{disk}.png")
            if _plan_mask(background, candidate, disk) >= 0.99 * area4:
                mask = candidate
                break
        full = os.path.join(tmp, "full.png")
        magick(mask, "-resize", f"{width}x{height}!", "-threshold", "50%", full)
        magick(background, full, "-alpha", "off", "-compose", "CopyOpacity", "-composite",
               "-define", "png:exclude-chunks=date,time", out_png)
    return subprocess.check_output(["magick", out_png, "-format", "%@", "info:"],
                                   stderr=subprocess.DEVNULL).decode().strip()


def visual_duplicates(paths, threshold=0.04):
    """Groups of images that are the same picture (possibly at different resolutions):
    compare 64x32 grayscale thumbnails by RMSE."""
    with tempfile.TemporaryDirectory() as tmp:
        thumbs = {}
        for i, p in enumerate(paths):
            t = os.path.join(tmp, f"{i}.png")
            try:
                magick(p + "[0]", "-resize", "64x32!", "-colorspace", "Gray", t)
            except subprocess.CalledProcessError:
                continue                     # not an image ImageMagick can read: no duplicate check
            thumbs[p] = t
        paths = [p for p in paths if p in thumbs]
        groups, seen = [], set()
        for i, a in enumerate(paths):
            if a in seen:
                continue
            group = [a]
            for b in paths[i + 1:]:
                if b in seen:
                    continue
                r = subprocess.run(["magick", "compare", "-metric", "RMSE", thumbs[a], thumbs[b], "null:"],
                                   capture_output=True, text=True, errors="replace")
                m = re.search(r"\(([\d.e-]+)\)", r.stderr)
                if m and float(m.group(1)) < threshold:
                    group.append(b)
                    seen.add(b)
            if len(group) > 1:
                groups.append(group)
        return groups


def copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
