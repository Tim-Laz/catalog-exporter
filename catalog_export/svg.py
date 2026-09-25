"""Mask geometry: read source SVG layers, write clean masks and QA previews.

Every mask we write is one `<path id="...">` per zone in the pixel space of its
render (viewBox = render size), so it overlays its render 1:1.
"""

import base64, html, os, re, subprocess, tempfile

from .core import magick

SHAPE_RE = re.compile(r"<(path|polygon|polyline|rect)\b([^>]*?)/?>", re.S)
ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"', re.S)

PREVIEW_STYLE = """
  .zone { fill: #2f80ed; fill-opacity: .35; stroke: #0b3e86; stroke-width: 2;
          vector-effect: non-scaling-stroke; }
  .zone.alt { fill: #eb5757; stroke: #8c1f1f; }
  .label { font: 700 %(fs)dpx Helvetica, Arial, sans-serif; fill: #fff;
           stroke: #000; stroke-width: %(sw)spx; paint-order: stroke; text-anchor: middle;
           dominant-baseline: middle; }
"""


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
    with open(path, "w") as f:
        f.write("\n".join(l for l in lines if l) + "\n")


def render_preview(image_path, width, height, zones, out_jpg, labels=None, max_width=1920):
    """Render the mask over its render so alignment can be checked by eye.
    labels = [(text, x, y), ...]; defaults to each zone id at the centre of its shape."""
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
    fs = max(14, round(width / 110))
    body = [f'<image href="data:{mime};base64,{b64}" width="{width}" height="{height}"/>']
    for i, (zid, d) in enumerate(zones):
        body.append(f'<path class="zone{" alt" if i % 2 else ""}" d="{d}"/>')
    if labels is None:
        labels = [(zid, *c) for zid, d in zones if (c := path_center(d))]
    for t, x, y in labels:
        lines = str(t).split("\n")
        top = y - (len(lines) - 1) * fs * 0.6
        spans = "".join(f'<tspan x="{x:.0f}" y="{top + n * fs * 1.2:.0f}">{html.escape(l)}</tspan>'
                        for n, l in enumerate(lines))
        body.append(f'<text class="label">{spans}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}"><style>{PREVIEW_STYLE % {"fs": fs, "sw": fs / 5}}</style>'
           + "".join(body) + "</svg>")
    with tempfile.TemporaryDirectory() as tmp:
        src, png = os.path.join(tmp, "p.svg"), os.path.join(tmp, "p.png")
        with open(src, "w") as f:
            f.write(svg)
        subprocess.check_call(["rsvg-convert", "-o", png, src], stderr=subprocess.DEVNULL)
        magick(png, "-resize", f"{max_width}x>", "-quality", "85", out_jpg)
