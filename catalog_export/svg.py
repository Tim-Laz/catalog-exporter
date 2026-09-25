"""Mask geometry: read source SVG layers, write clean masks and QA previews.

Every mask we write is one `<path id="...">` per zone in the pixel space of its
render (viewBox = render size), so it overlays its render 1:1.
"""

import html, os, re, subprocess, tempfile


SHAPE_RE = re.compile(r"<(path|polygon|polyline|rect)\b([^>]*?)/?>", re.S)
ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"', re.S)

ZONE_COLORS = [("rgba(47,128,237,0.35)", "#0b3e86"), ("rgba(235,87,87,0.35)", "#8c1f1f")]
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _attrs(s):
    return dict(ATTR_RE.findall(s))


def _shape_d(tag, a):
    if tag == "path":
        d = a.get("d", "").strip()
        # a moveto with no drawing command is an editor leftover, not a shape
        return d if re.search(r"[LlHhVvCcSsQqTtAaZz]", d[1:]) else None
    if tag in ("polygon", "polyline"):
        pts = re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", a.get("points", ""))
        if len(pts) < 6:
            return None
        pairs = [f"{pts[i]},{pts[i + 1]}" for i in range(0, len(pts) - 1, 2)]
        return "M" + " L".join(pairs) + (" Z" if tag == "polygon" else "")
    if tag == "rect":
        x, y = float(a.get("x", 0)), float(a.get("y", 0))
        w, h = float(a.get("width", 0)), float(a.get("height", 0))
        return f"M{x},{y} h{w} v{h} h{-w} Z" if w and h else None
    return None


def shapes_to_d(svg_fragment):
    """All drawable shapes of a fragment merged into one path `d`, plus a count of
    shapes that carried their own transform (not flattened — reported instead)."""
    ds, transformed = [], 0
    for tag, raw in SHAPE_RE.findall(svg_fragment):
        a = _attrs(raw)
        if "transform" in a:
            transformed += 1
        d = _shape_d(tag, a)
        if d:
            ds.append(d)
    return " ".join(ds), transformed


_ARGS = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}


def path_points(d):
    """End points of every segment of a path, absolute (handles relative commands).
    Control points are ignored — enough for a bbox or a label position."""
    toks = re.findall(r"[MLHVCSQTAZmlhvcsqtaz]|-?(?:\d+\.?\d*|\.\d+)(?:e-?\d+)?", d)
    x = y = sx = sy = 0.0
    xs, ys, i, cmd = [], [], 0, None
    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                x, y = sx, sy
                continue
        if cmd is None:
            break
        n = _ARGS[cmd.upper()]
        vals = [float(v) for v in toks[i:i + n]]
        if len(vals) < n:
            break
        i += n
        up, rel = cmd.upper(), cmd.islower()
        if up == "H":
            x = x + vals[0] if rel else vals[0]
        elif up == "V":
            y = y + vals[0] if rel else vals[0]
        else:
            ex, ey = vals[-2], vals[-1]
            x, y = (x + ex, y + ey) if rel else (ex, ey)
        if up == "M":
            sx, sy = x, y
            cmd = "l" if rel else "L"   # extra pairs after a moveto are linetos
        xs.append(x)
        ys.append(y)
    return list(zip(xs, ys))


def path_bbox(d):
    pts = path_points(d)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def path_center(d):
    b = path_bbox(d)
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else None


def gtag_d(fragment):
    """A building/area layer fragment is `<g transform="translate(-x,-y)">…</g>`. Its
    coordinates are already in render pixel space; the translate only served the viewer's
    overlay positioning, so it is dropped."""
    body = re.sub(r"^\s*<g\s+transform=\"translate\([^)]*\)\"\s*>", "", fragment.strip())
    return shapes_to_d(body)


def floor_groups(svg_text):
    """Floor-scene SVG: `<g id="<layer_id>">shapes</g>` per apartment contour."""
    out = {}
    for gid, body in re.findall(r'<g\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</g>', svg_text, re.S):
        out[gid] = shapes_to_d(body)
    return out


def write_mask(path, width, height, zones, note=""):
    """zones = [(id, d), ...] -> clean SVG with viewBox = render size."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<!-- {html.escape(note)} -->" if note else "",
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
    ]
    lines += [f'  <path id="{html.escape(zid)}" d="{d}"/>' for zid, d in zones]
    lines.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(l for l in lines if l) + "\n")


def label_font():
    return next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)


def _mvg_text(t):
    return str(t).replace("\\", "\\\\").replace("'", "\\'")


def render_preview(image_path, width, height, zones, out_jpg, labels=None, max_width=1920):
    """Draw the mask over its render so alignment can be checked by eye.
    labels = [(text, x, y), ...]; defaults to each zone id at the centre of its shape.
    Drawn by ImageMagick from an MVG file (a command line would be too long on Windows)."""
    k = max(1.0, width / 1920)                   # the preview is scaled to 1920 wide
    fs = max(14, round(width / 110))
    mvg = [f"stroke-width {2 * k:.1f}", "stroke-linejoin round"]
    for i, (_, d) in enumerate(zones):
        fill, stroke = ZONE_COLORS[i % 2]
        mvg += [f"fill '{fill}'", f"stroke '{stroke}'", f"path '{d}'"]
    if labels is None:
        labels = [(zid, *c) for zid, d in zones if (c := path_center(d))]
    font = label_font()
    text = [f"font '{font.replace(chr(92), '/')}'"] if font else []
    text += [f"font-size {fs}", "text-anchor middle"]
    for t, x, y in labels:
        lines = str(t).split("\n")
        top = y - (len(lines) - 1) * fs * 0.6 + fs * 0.35
        for n, line in enumerate(lines):
            ty = top + n * fs * 1.2
            # a dark outline first, then the white text on top of it
            text += ["fill black", "stroke black", f"stroke-width {fs / 5:.1f}",
                     f"text {x:.0f},{ty:.0f} '{_mvg_text(line)}'",
                     "fill white", "stroke none", f"text {x:.0f},{ty:.0f} '{_mvg_text(line)}'"]
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "zones.mvg"), "w", encoding="utf-8") as f:
            f.write("\n".join(mvg) + "\n")
        with open(os.path.join(tmp, "labels.mvg"), "w", encoding="utf-8") as f:
            f.write("\n".join(text) + "\n")
        base = [os.path.abspath(image_path), "-draw", "@zones.mvg"]
        tail = ["-resize", f"{max_width}x>", "-quality", "85", os.path.abspath(out_jpg)]
        try:
            subprocess.check_call(["magick", *base, "-draw", "@labels.mvg", *tail],
                                  cwd=tmp, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            # no usable font for the labels — the outlines alone still show the alignment
            subprocess.check_call(["magick", *base, *tail], cwd=tmp, stderr=subprocess.DEVNULL)
