"""Read the apartment type printed on a floor plan ("1 BHK", "4 BHK DUPLEX"), so it can be
compared with the layout the source assigns. Uses the OCR built into the OS: macOS Vision
(compiled once with `swiftc` from the Xcode command line tools) or Windows.Media.Ocr (through
Windows PowerShell). Optional: when neither is available the check is skipped."""

import os, re, shutil, subprocess, sys, tempfile

from .core import magick
from .svg import path_points

HERE = os.path.dirname(os.path.abspath(__file__))
SWIFT_SRC = os.path.join(HERE, "ocr.swift")
PS1 = os.path.join(HERE, "ocr_windows.ps1")
# OCR engines sometimes return Cyrillic look-alikes ("3 BНК")
HOMOGLYPHS = str.maketrans("НКВОАСЕМРТХнквоасемртх", "HKBOACEMPTXHKBOACEMPTX")
TYPE_RE = re.compile(r"\b([1-6])\s*B\s*H\s*K\b")
_available = None


def _powershell():
    return [shutil.which("powershell") or "powershell", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", PS1]


def available():
    global _available
    if _available is None:
        if sys.platform == "darwin":
            _available = shutil.which("swiftc") is not None
        elif os.name == "nt" and shutil.which("powershell"):
            # also fails when Windows has no OCR language installed
            _available = subprocess.run(_powershell() + ["-Probe"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL).returncode == 0
        else:
            _available = False
    return _available


def _command(cache_dir):
    if os.name == "nt":
        return _powershell()
    exe = os.path.join(cache_dir, "catalog-ocr")
    if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(SWIFT_SRC):
        os.makedirs(cache_dir, exist_ok=True)
        subprocess.check_call(["swiftc", "-O", SWIFT_SRC, "-o", exe], stderr=subprocess.DEVNULL)
    return [exe]


def printed_types(image, bbox, cache_dir):
    """[(bedrooms, x, y), ...] for every "N BHK" label inside bbox=(x, y, w, h) of image.
    The plan is read in overlapping tiles upscaled 4x — small labels are missed at 1:1."""
    cmd = _command(cache_dir)
    x0, y0, bw, bh = bbox
    scale = max(1, round(bw / 1000))           # 1920-wide scenes vs 3840-wide ones
    tile, step, up = 300 * scale, 240 * scale, 4 // scale if scale < 4 else 1
    found = []
    with tempfile.TemporaryDirectory() as tmp:
        tiles = {}
        for ty in range(y0, y0 + bh, step):
            for tx in range(x0, x0 + bw, step):
                p = os.path.join(tmp, f"{tx}_{ty}.png")
                magick(image, "-crop", f"{tile}x{tile}+{tx}+{ty}", "+repage", "-resize", f"{up * 100}%", p)
                tiles[p] = (tx, ty)
        out = subprocess.check_output([*cmd, *tiles], stderr=subprocess.DEVNULL).decode("utf-8", "replace")
        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4 or parts[0] not in tiles:
                continue
            path, x, y, text = parts
            m = TYPE_RE.search(text.translate(HOMOGLYPHS).upper())
            if m:
                tx, ty = tiles[path]
                found.append((int(m.group(1)), tx + int(x) / up, ty + int(y) / up))
    # overlapping tiles read the same label twice
    uniq = []
    for n, x, y in found:
        if not any(n == m and abs(x - a) < 25 * scale and abs(y - b) < 25 * scale for m, a, b in uniq):
            uniq.append((n, x, y))
    return uniq


def point_in_path(x, y, d):
    pts = path_points(d)
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def bedrooms(plan_name):
    m = re.match(r"\s*(\d)", plan_name or "")
    return int(m.group(1)) if m else None
