"""The export steps. Each writes one numbered folder of the output and reports into ctx.report."""

import csv, json, os, re

from . import core, images, ocr, svg
from .core import (cdn_url, decodes, slugify, download, get_text, image_size, items, kebab, log, natural_key,
                   to_webp, unwrap, url_ext)



# --------------------------------------------------------------------------- #
# Loading and cross-referencing everything the API knows
# --------------------------------------------------------------------------- #

class Ctx:
    def __init__(self, api, out, report, org, project, developer_slug, project_slug=None):
        self.api, self.out, self.report = api, out, report
        self.org, self.project = org, project
        self.developer_slug = developer_slug
        self.project_slug = project_slug

    def load(self):
        log("Reading the catalog API ...")
        proj = next((p for p in items(self.api.org("listProjectsFromOrganization"))
                     if p.get("_id") == self.project), {})
        self.project_name = proj.get("name") or self.project
        self.project_slug = self.project_slug or kebab(self.project_name)

        self.buildings = {}
        for b in items(self.api.project("getListOfBuildings")):
            floors = sorted((str(f.get("floor_id")) for f in items(b.get("floors") or {})), key=natural_key)
            self.buildings[b["_id"]] = {"id": b["_id"], "name": b.get("name"), "slug": kebab(b.get("name")),
                                        "floors": floors}

        # getListofUnits ignores building_id / floor_id: one call returns the whole project
        self.units = sorted(items(self.api.project("getListofUnits")),
                            key=lambda u: (self.bslug(u["building_id"]), int(u.get("floor_id") or 0),
                                           natural_key(u.get("name"))))
        self.unit_by_id = {u["_id"]: u for u in self.units}
        self.unit_by_key = {(u["building_id"], str(u["floor_id"]), str(u["name"])): u for u in self.units}

        self.unitplans = {p["_id"]: p for p in items(self.api.project("getListOfUnitplan"))}
        self.tours = {tid: {**unwrap(t), "_id": tid} for tid, t in self.api.project("ListTours").items()}
        self.amenities = items(self.api.project("GetAmenities"))
        self.scenes = [
            {**s["sceneData"], "svg": list((s.get("svgData") or {}).values())}
            for s in items(self.api.project("getAllScenes"))
        ]
        self._classify_scenes()
        log(f"  project '{self.project_name}' -> slug '{self.project_slug}'")
        log(f"  {len(self.buildings)} buildings, {len(self.units)} apartments, "
            f"{len(self.unitplans)} unit plans, {len(self.tours)} tours, "
            f"{len(self.amenities)} amenities, {len(self.scenes)} scenes\n")

    def bslug(self, building_id):
        return self.buildings.get(building_id, {}).get("slug", "unknown-building")

    @staticmethod
    def layers(scene):
        return [l for sv in scene["svg"] for l in (sv.get("layers") or {}).values()]

    def _classify_scenes(self):
        self.floor_scenes = [s for s in self.scenes if s.get("type") == "identical_unitplan"]
        self.building_scene = {}   # building_id -> scene
        self.floor_scene_of = {}   # (building_id, floor) -> scene id, as the tower view links it
        for s in self.scenes:
            for l in self.layers(s):
                if l.get("type") == "floor" and l.get("building_id"):
                    self.building_scene[l["building_id"]] = s
                    self.floor_scene_of[(l["building_id"], str(l.get("floor_id")))] = l.get("scene_id")
        tower_ids = {s["_id"]: bid for bid, s in self.building_scene.items()}
        self.area_scene = next((s for s in self.scenes if any(
            l.get("type") == "pin" and l.get("scene_id") in tower_ids for l in self.layers(s))), None)
        self.tower_of_scene = tower_ids
        self.amenity_scene = next((s for s in self.scenes if any(
            l.get("type") == "amenity" for l in self.layers(s))), None)
        self.map_scene = next((s for s in self.scenes if s.get("root")), None)

    def unit_tour(self, u):
        plan = self.unitplans.get(u.get("unitplan_id")) or {}
        return self.tours.get(plan.get("tour_id")), plan


def fetch_fragment(layer):
    return get_text(cdn_url(layer["g"]))


def rel(ctx, path):
    return os.path.relpath(path, ctx.out)


def write_csv(path, rows, header):
    """UTF-8 with BOM so Excel and Google Sheets both open Cyrillic headers correctly."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def size_str(path):
    wh = image_size(path)
    return f"{wh[0]}×{wh[1]}" if wh else "?"


# --------------------------------------------------------------------------- #
# Deep Zoom scene + its mask (area, towers)
# --------------------------------------------------------------------------- #

def export_dzi_scene(ctx, scene, folder, name, zones_from_layers, section, mask_name="mask"):
    """zones_from_layers: [(zone_id, layer), ...]. Writes <name>.jpg, <name>_<mask_name>.svg,
    <name>_preview.jpg and the untouched layer fragments in _source/."""
    os.makedirs(folder, exist_ok=True)
    render = os.path.join(folder, f"{name}.jpg")
    dzi = cdn_url(scene["background"]["high_resolution"])
    if not os.path.exists(render) or not decodes(render):
        w, h, n = images.stitch_dzi(dzi, render)
        log(f"   render {w}×{h} stitched from {n} tiles")
    w, h = image_size(render)

    zones, labels = [], []
    for zid, layer in zones_from_layers:
        frag = fetch_fragment(layer)
        src = os.path.join(folder, "_source", f"{zid}.svg")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        with open(src, "w") as f:
            f.write(frag)
        d, transformed = svg.gtag_d(frag)
        if transformed:
            ctx.report.issue(section, f"{name}: зона {zid} содержит {transformed} фигур(ы) с собственным transform — проверьте превью")
        if not d:
            ctx.report.issue(section, f"{name}: у зоны {zid} нет геометрии")
            continue
        zones.append((zid, d))
        labels.append((zid, layer["x"] + layer["width"] / 2, layer["y"] + layer["height"] / 2))
    mask = os.path.join(folder, f"{name}_{mask_name}.svg")
    svg.write_mask(mask, w, h, zones, note=f"{name}: {len(zones)} zones, pixel space of {name}.jpg ({w}x{h})")
    svg.render_preview(render, w, h, zones, os.path.join(folder, f"{name}_preview.jpg"), labels)
    ctx.report.add(section, f"- `{rel(ctx, render)}` — {w}×{h}; маска `{rel(ctx, mask)}` — {len(zones)} зон: "
                            + ", ".join(z for z, _ in zones))
    return zones


def step_area(ctx):
    s = ctx.area_scene
    if not s:
        ctx.report.issue("Area", "сцена выбора башни не найдена")
        return
    log(f"== Area: scene '{s['name']}'")
    zones = []
    for l in ctx.layers(s):
        bid = ctx.tower_of_scene.get(l.get("scene_id"))
        if l.get("type") == "pin" and bid:
            zones.append((ctx.bslug(bid), l))
    export_dzi_scene(ctx, s, os.path.join(ctx.out, "01_area"), "area", zones, "Area")


def step_buildings(ctx):
    for bid, s in sorted(ctx.building_scene.items(), key=lambda kv: ctx.bslug(kv[0])):
        b = ctx.buildings[bid]
        log(f"== Building {b['name']}: scene '{s['name']}'")
        floor_layers = sorted(((str(l["floor_id"]), l) for l in ctx.layers(s)
                               if l.get("type") == "floor" and l.get("building_id") == bid),
                              key=lambda x: natural_key(x[0]))
        export_dzi_scene(ctx, s, os.path.join(ctx.out, "02_buildings", b["slug"]), b["slug"],
                         floor_layers, "Здания", mask_name="floors-mask")
        missing = [f for f in b["floors"] if f not in dict(floor_layers)]
        if missing:
            ctx.report.issue("Здания", f"{b['name']}: нет контура для этажей {', '.join(missing)}")


# --------------------------------------------------------------------------- #
# Floors: background (подложка), cut-out plan, apartment mask with real numbers
# --------------------------------------------------------------------------- #

def contour_position(layer_name):
    """The source names a contour by its position ('01'..'10'), except floor 1 where the
    full number is used ('101'). Our numbering is floor + 2-digit position."""
    name = str(layer_name).strip()
    return name[-2:] if len(name) > 2 else name.zfill(2)


def floor_mapping(ctx):
    """For every floor scene: contour -> apartment numbers, checked against the
    units the source itself attached to each contour. Fills ctx.unit_contour."""
    ctx.unit_contour = {}   # unit id -> 'ok' | problem text
    result = []
    for s in sorted(ctx.floor_scenes, key=lambda s: (ctx.bslug(s.get("building_id")),
                                                    natural_key(s.get("floor_ids", [""])[0]))):
        bid, floors = s.get("building_id"), [str(f) for f in s.get("floor_ids") or []]
        b = ctx.buildings[bid]
        contours, misbound = [], []
        for l in ctx.layers(s):
            if l.get("type") != "units":
                continue
            pos = contour_position(l.get("name"))
            expected = {f: ctx.unit_by_key.get((bid, f, f"{f}{pos}")) for f in floors}
            attached = [ctx.unit_by_id.get(uid) for uid in l.get("units") or []]
            wrong = [u for u in attached if u and u not in expected.values()]
            for f, u in expected.items():
                if not u:
                    ctx.report.issue("Этажи", f"{b['name']} этаж {f}: контур {pos} есть, а квартиры {f}{pos} в API нет")
                    continue
                if u in attached:
                    ctx.unit_contour[u["_id"]] = "ok"
                else:
                    ctx.unit_contour[u["_id"]] = "контур есть, но в источнике привязан к другой квартире"
            if wrong:
                misbound.append(f"{pos} → " + ", ".join(
                    f"{ctx.buildings[u['building_id']]['name']} №{u['name']}" for u in wrong))
            contours.append({"layer": l, "position": pos})
        if misbound:
            ctx.report.issue("Этажи", f"сцена «{s['name']}»: {len(misbound)} из {len(contours)} контуров в источнике "
                                      f"привязаны не к тем квартирам ({'; '.join(misbound)}). На исходном сайте эти "
                                      "квартиры не кликаются или открывают чужие. В выгрузке номер взят по положению "
                                      f"контура (этаж + позиция, здание {b['name']}).")
        for f in floors:
            have = {c["position"] for c in contours}
            for u in ctx.units:
                if u["building_id"] == bid and str(u["floor_id"]) == f and str(u["name"])[-2:] not in have:
                    ctx.unit_contour[u["_id"]] = "нет контура на плане этажа"
                    ctx.report.issue("Этажи", f"{b['name']} этаж {f}: у квартиры №{u['name']} нет контура на плане")
        result.append({"scene": s, "building": b, "floors": floors, "contours": contours})
    return result


def polygon_area(d):
    pts = svg.path_points(d)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def step_floors(ctx, mapping):
    groups_out = []
    for m in mapping:
        s, b, floors = m["scene"], m["building"], m["floors"]
        folder_name = "floors-" + "-".join(f.zfill(2) for f in floors)
        folder = os.path.join(ctx.out, "03_floors", b["slug"], folder_name)
        log(f"== Floors {b['name']} {', '.join(floors)}: scene '{s['name']}'")
        bg_url = s["background"]["high_resolution"]
        background = download(bg_url, os.path.join(folder, f"background.{url_ext(bg_url)}"))
        w, h = image_size(background)

        svg_url = next((sv.get("svg_url") for sv in s["svg"] if sv.get("svg_url")), None)
        raw = get_text(cdn_url(svg_url))
        os.makedirs(os.path.join(folder, "_source"), exist_ok=True)
        with open(os.path.join(folder, "_source", "mask_source.svg"), "w") as f:
            f.write(raw)
        groups = svg.floor_groups(raw)
        vb = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', raw)
        if vb and (round(float(vb.group(1))), round(float(vb.group(2)))) != (w, h):
            ctx.report.issue("Этажи", f"{folder_name} {b['name']}: viewBox маски {vb.group(1)}×{vb.group(2)} "
                                      f"не совпадает с подложкой {w}×{h}")

        zones, info = [], {}
        for c in sorted(m["contours"], key=lambda c: c["position"]):
            d, _ = groups.get(c["layer"]["layer_id"], ("", 0))
            if not d:
                ctx.report.issue("Этажи", f"{folder_name} {b['name']}: контур {c['position']} есть в API, но нет в SVG")
                continue
            pos = c["position"]
            zones.append((pos, d))
            flats = [ctx.unit_by_key.get((b["id"], f, f"{f}{pos}")) for f in floors]
            plans = sorted({(ctx.unitplans.get(u["unitplan_id"]) or {}).get("name") for u in flats if u})
            if len(plans) > 1:
                ctx.report.issue("Этажи", f"{b['name']} {folder_name}, позиция {pos}: этажи одинаковые, а "
                                          f"планировки в источнике разные ({' / '.join(plans)})")
            # area normalised to a 1920-wide frame so all scenes compare
            info[pos] = {"plans": plans, "area": polygon_area(d) * (1920 / w) ** 2, "units": flats}
        groups_out.append((m, folder, folder_name, background, w, h, zones, info))

    # A layout mismatch shows up as a contour much bigger/smaller than others of its API type
    by_type = {}
    for *_, info in groups_out:
        for i in info.values():
            if len(i["plans"]) == 1:
                by_type.setdefault(i["plans"][0], []).append(i["area"])
    median = {t: sorted(a)[len(a) // 2] for t, a in by_type.items() if len(a) >= 3}

    use_ocr = ocr.available()
    if not use_ocr:
        ctx.report.issue("Этажи", "swiftc не найден (нужны Xcode command line tools) — тип квартиры, "
                                  "напечатанный на плане, не сверен с источником; осталась только сверка по площади")
    ctx.unit_printed, ctx.unit_layout_check = {}, {}

    for m, folder, folder_name, background, w, h, zones, info in groups_out:
        b, floors = m["building"], m["floors"]
        mask = os.path.join(folder, "mask.svg")
        svg.write_mask(mask, w, h, zones,
                       note=f"{b['name']} floors {', '.join(floors)}: path id = position, "
                            "apartment number = floor + position (e.g. floor 7, id 03 -> 703)")
        plan = os.path.join(folder, "plan.png")
        bbox = images.cut_floor_plan(background, plan, w, h)
        to_webp(plan, os.path.join(folder, "plan.webp"), lossless=True)

        if use_ocr:
            bw, bh, bx, by = map(int, re.match(r"(\d+)x(\d+)\+(\d+)\+(\d+)", bbox).groups())
            labels_found = ocr.printed_types(background, (bx, by, bw, bh), os.path.join(ctx.root, ".cache"))
            for pos, d in zones:
                info[pos]["printed"] = sorted({n for n, x, y in labels_found if ocr.point_in_path(x, y, d)})

        mismatches = []
        for pos, i in info.items():
            printed = i.get("printed") or []
            wrong_floors = []
            for f, u in zip(floors, i["units"]):
                if not u:
                    continue
                api_plan = (ctx.unitplans.get(u["unitplan_id"]) or {}).get("name", "")
                if printed:
                    ctx.unit_printed[u["_id"]] = "/".join(f"{n} BHK" for n in printed)
                    ok = ocr.bedrooms(api_plan) in printed
                    ctx.unit_layout_check[u["_id"]] = "совпадает" if ok else \
                        f"НЕТ: на плане {ctx.unit_printed[u['_id']]}, в источнике {api_plan}"
                    if not ok:
                        wrong_floors.append((f, api_plan))
                elif use_ocr:
                    ctx.unit_layout_check[u["_id"]] = "подпись на плане не распознана"
            if wrong_floors:
                i["mismatch"] = True
                mismatches.append(f"№{', №'.join(f + pos for f, _ in wrong_floors)} — на плане "
                                  f"{'/'.join(f'{n} BHK' for n in printed)}, в источнике "
                                  f"{'/'.join(sorted({p for _, p in wrong_floors}))}")
            # size-based fallback only where the printed type could not be read
            own = median.get(i["plans"][0]) if len(i["plans"]) == 1 else None
            if not printed and own and abs(i["area"] - own) / own >= 0.3:
                closer = min(median, key=lambda t: abs(median[t] - i["area"]))
                if closer != i["plans"][0]:
                    i["suspect"] = closer
                    for u in i["units"]:
                        if u:
                            ctx.unit_layout_check[u["_id"]] = f"подозрение: по площади контура похоже на {closer}"
                    ctx.report.issue("Этажи", f"{b['name']} №{'/'.join(f + pos for f in floors)}: в источнике "
                                              f"{i['plans'][0]}, а контур по площади как у {closer}. Сверьте с "
                                              f"подписью на плане в {rel(ctx, folder)}/preview.jpg")
        if mismatches:
            ctx.report.issue("Планировки", f"{b['name']}, этажи {', '.join(floors)}: в источнике не та планировка "
                                           f"(а значит, не тот тур и топ-вью) — " + "; ".join(mismatches))

        # label = position + the layout the source assigns; a mismatch with the printed type is marked
        labels = []
        for pos, d in zones:
            i = info[pos]
            text = f"{pos} · {'/'.join(i['plans'])}"
            if i.get("mismatch"):
                text += f"\n≠ на плане {'/'.join(str(n) for n in i['printed'])}BHK"
            elif i.get("suspect"):
                text += " ?"
            labels.append((text, *svg.path_center(d)))
        svg.render_preview(background, w, h, zones, os.path.join(folder, "preview.jpg"), labels)

        rows = []
        for f in floors:
            for pos, _ in zones:
                u = ctx.unit_by_key.get((b["id"], f, f"{f}{pos}"))
                plan_name = (ctx.unitplans.get((u or {}).get("unitplan_id")) or {}).get("name", "")
                rows.append([b["name"], f, pos, f"{f}{pos}", plan_name,
                             "/".join(f"{n} BHK" for n in info[pos].get("printed") or []),
                             ctx.unit_layout_check.get((u or {}).get("_id"), ""),
                             round(info[pos]["area"]), (u or {}).get("_id", ""),
                             ctx.unit_contour.get((u or {}).get("_id"), "нет квартиры в API")])
        write_csv(os.path.join(folder, "contours.csv"), rows,
                  ["Здание", "Этаж", "Контур (id в mask.svg)", "Квартира", "Планировка в источнике",
                   "Тип на плане (OCR)", "Планировка совпадает", "Площадь контура (px, кадр 1920)",
                   "unit_id источника", "Контур привязан в источнике"])

        # the same mask once per floor, for tools that take one file per floor
        for f in floors:
            sp = os.path.join(ctx.out, "03_floors", b["slug"], "per-floor", f"floor_{f}.svg")
            images.copy(mask, sp)
        ctx.report.add("Этажи", f"- `{rel(ctx, folder)}/` — этажи {', '.join(floors)}: подложка {w}×{h}, "
                                f"план {bbox}, {len(zones)} контуров")


# --------------------------------------------------------------------------- #
# Tours (unique), panoramas named after the tour
# --------------------------------------------------------------------------- #

def tour_folder_name(tour):
    return re.sub(r"[^A-Za-z0-9-]+", "-", tour.get("name") or "tour").strip("-")


def step_tours(ctx):
    users = {}
    for u in ctx.units:
        t, _ = ctx.unit_tour(u)
        if t:
            users.setdefault(t["_id"], []).append(u)
    root = os.path.join(ctx.out, "04_tours")
    rows = []
    for tid, t in sorted(ctx.tours.items(), key=lambda kv: natural_key(kv[1].get("name"))):
        tname = tour_folder_name(t)
        folder = os.path.join(root, tname)
        scenes = sorted(items(t.get("images", [])), key=lambda i: i.get("order", 0))
        log(f"== Tour {tname}: {len(scenes)} scenes, {len(users.get(tid, []))} apartments")
        out_scenes, id2file = [], {}
        for i, im in enumerate(scenes, 1):
            sid = im.get("id") or im.get("_id")
            id2file[sid] = f"{tname}_{i:02d}_{kebab(im.get('name'))}.jpg"
        for i, im in enumerate(scenes, 1):
            sid = im.get("id") or im.get("_id")
            path = os.path.join(folder, id2file[sid])
            download(im["url"], path)
            w, h = image_size(path)
            note = ""
            preview = os.path.join(folder, "_preview", id2file[sid])
            if w < 4000:
                # decided by the file itself, so an interrupted rebuild is simply redone
                if not os.path.exists(preview):
                    images.copy(path, preview)
                # tiles sit next to the panorama: …/tours/<tour>/<scene>/lod_0/
                tiles = cdn_url(im["url"]).rsplit("/", 1)[0] + f"/{sid}/lod_0"
                if not images.stitch_pano_tiles(tiles, path):
                    ctx.report.issue("Туры", f"{tname}/{id2file[sid]}: только превью {w}×{h}, тайлов нет")
            if os.path.exists(preview) and image_size(path)[0] > image_size(preview)[0]:
                note = f"пересобрана из тайлов ({size_str(preview)} → {size_str(path)}), превью в _preview/"
            w, h = image_size(path)
            if w != 2 * h:
                ctx.report.issue("Туры", f"{rel(ctx, path)}: {w}×{h} — исходник в источнике не 2:1 (360-панорама "
                                         "растянута по вертикали); отдаём как есть — учесть при создании туров")
            links = []
            for l in items(im.get("links", [])):
                p = l.get("position") or {}
                links.append({"label": l.get("text", ""), "target": id2file.get(l.get("destination_img_id")),
                              "position_xyz": {k: p.get(k) for k in ("x", "y", "z")}})
            out_scenes.append({"file": id2file[sid], "name": im.get("name"), "size": size_str(path),
                               "initial_view_raw": im.get("rotation"), "hotspots": links, "note": note})
            if note:
                ctx.report.add("Туры", f"- `{rel(ctx, path)}` — {note}")
        apts = users.get(tid, [])
        with open(os.path.join(folder, "tour.json"), "w") as f:
            json.dump({"tour": tname, "source_tour_id": tid, "scenes": out_scenes,
                       "apartments": [f"{ctx.buildings[u['building_id']]['name']} №{u['name']}" for u in apts],
                       "hotspot_note": "position_xyz = point on a sphere (radius ~90), three.js coords: "
                                       "Y up, forward = -Z, camera at centre."},
                      f, indent=2, ensure_ascii=False)
        rows.append([tname, len(scenes), len(apts),
                     "; ".join(f"{ctx.bslug(u['building_id'])} {u['name']}" for u in apts)])
        ctx.report.add("Туры", f"- `{rel(ctx, folder)}/` — {len(scenes)} панорам, {len(apts)} квартир")
    write_csv(os.path.join(root, "tours.csv"), rows, ["Тур", "Панорам", "Квартир", "Квартиры"])
    no_tour = [u for u in ctx.units if not ctx.unit_tour(u)[0]]
    if no_tour:
        by_plan = {}
        for u in no_tour:
            by_plan.setdefault((ctx.unitplans.get(u["unitplan_id"]) or {}).get("name"), []).append(u)
        for plan, us in by_plan.items():
            ctx.report.issue("Туры", f"{len(us)} квартир без тура (планировка «{plan}»): "
                                     + ", ".join(f"{ctx.bslug(u['building_id'])} {u['name']}" for u in us))


# --------------------------------------------------------------------------- #
# Top views, named <developer>_<project>_<building>_<layout or number>_plan.webp
# --------------------------------------------------------------------------- #

def plan_images(ctx, plan):
    """[(suffix, url)]: one image, or lower/upper floors for a duplex."""
    if plan.get("image_url"):
        return [("", plan["image_url"])]
    out = []
    for i, fid in enumerate(plan.get("floor_unitplans") or []):
        img = (ctx.unitplans.get(fid) or {}).get("image_url")
        if img:
            out.append(("" if i == 0 else f"_floor-{i + 1}", img))
    return out


def config_slug(ctx, plan):
    slug = slugify(plan.get("name"))
    if "_" in slug or not slug:
        ctx.report.issue("Топ-вью", f"планировка «{plan.get('name')}» даёт slug «{slug}» — не подходит для имени файла")
    return slug


def topview_prefix(ctx, building_id):
    return f"{ctx.developer_slug}_{ctx.project_slug}_{ctx.bslug(building_id)}"


def topview_name(ctx, u):
    plan = ctx.unitplans.get(u.get("unitplan_id")) or {}
    if not plan_images(ctx, plan):
        return ""
    return f"{topview_prefix(ctx, u['building_id'])}_{config_slug(ctx, plan)}_plan.webp"


def step_topviews(ctx):
    root = os.path.join(ctx.out, "05_top-views")
    log("== Top views")
    prefix = lambda b: topview_prefix(ctx, b)  # noqa: E731
    made = {}
    for u in ctx.units:
        plan = ctx.unitplans.get(u.get("unitplan_id")) or {}
        imgs = plan_images(ctx, plan)
        if not imgs:
            ctx.report.issue("Топ-вью", f"{ctx.bslug(u['building_id'])} №{u['name']}: у планировки "
                                        f"«{plan.get('name')}» нет картинки")
            continue
        cfg, b = config_slug(ctx, plan), u["building_id"]
        for suffix, url in imgs:
            src = download(url, os.path.join(root, "_source", f"{cfg}{suffix}.{url_ext(url, 'png')}"))
            by_cfg = os.path.join(root, "by-configuration", f"{prefix(b)}_{cfg}_plan{suffix}.webp")
            if by_cfg not in made:
                to_webp(src, by_cfg, max_side=2560)
                made[by_cfg] = src
            images.copy(by_cfg, os.path.join(root, "by-apartment", f"{prefix(b)}_{u['name']}_plan{suffix}.webp"))
    for path, src in sorted(made.items()):
        ctx.report.add("Топ-вью", f"- `{rel(ctx, path)}` — {size_str(path)} (исходник {size_str(src)})")


# --------------------------------------------------------------------------- #
# Amenities: 360 photos grouped by source category, plus the overview render
# --------------------------------------------------------------------------- #

def step_amenities(ctx):
    root = os.path.join(ctx.out, "06_amenities")
    log("== Amenities")
    by_cat, entries = {}, []
    for a in sorted(ctx.amenities, key=lambda a: (a.get("category") or "", a.get("order") or 0)):
        by_cat.setdefault(a.get("category") or "other", []).append(a)
    for cat, group in by_cat.items():
        for i, a in enumerate(group, 1):
            path = os.path.join(root, kebab(cat), f"{i:02d}_{kebab(a.get('name'))}.{url_ext(a['file'])}")
            download(a["file"], path)
            entries.append((cat, a, path, image_size(path)))

    odd = [f"{rel(ctx, p)} ({wh[0]}×{wh[1]})" for _, _, p, wh in entries if wh and wh[0] != 2 * wh[1]]
    if odd:
        ctx.report.issue("Amenities", f"{len(odd)} из {len(entries)} панорам не 2:1 (обычно 360 = 2:1), "
                                      "источник всё равно показывает их как сферу: " + "; ".join(odd))
    dup_of = {}
    groups = images.visual_duplicates([p for _, _, p, _ in entries])
    for group in groups:
        best = max(group, key=lambda p: (image_size(p) or (0, 0))[0])
        for p in group:
            dup_of[p] = [rel(ctx, q) for q in group if q != p]
        ctx.report.issue("Amenities", "одна и та же панорама используется в нескольких местах: "
                                      + " = ".join(rel(ctx, p) for p in group)
                                      + f" (лучшее разрешение — {rel(ctx, best)}). Для создания туров достаточно одной копии.")
    rows = [[cat, a.get("name"), a.get("media_type"), rel(ctx, p), f"{wh[0]}×{wh[1]}" if wh else "?",
             ", ".join(dup_of.get(p, [])), a.get("rotation") or "",
             os.path.basename(a["file"].split("?")[0]).replace("%20", " ")]
            for cat, a, p, wh in entries]
    unique = len(entries) - sum(len(g) - 1 for g in groups)
    write_csv(os.path.join(root, "amenities.csv"), rows,
              ["Категория (тур)", "Название", "Тип", "Файл", "Размер", "Та же картинка, что", "Начальный вид (источник)",
               "Исходное имя в источнике"])
    ctx.report.add("Amenities", f"- {len(rows)} панорам ({unique} уникальных) в {len(by_cat)} категориях: "
                                + ", ".join(f"{c} ({len(g)})" for c, g in by_cat.items()))

    s = ctx.amenity_scene
    if s:
        folder = os.path.join(root, "_overview")
        os.makedirs(folder, exist_ok=True)
        render = os.path.join(folder, "amenities-overview.jpg")
        if not os.path.exists(render) or not decodes(render):
            images.stitch_dzi(cdn_url(s["background"]["high_resolution"]), render)
        w, h = image_size(render)
        names = {a["_id"]: a.get("name") for a in ctx.amenities}
        pins = []
        for l in ctx.layers(s):
            if l.get("type") == "amenity":
                sx = (l.get("scale") or {}).get("x", 1)
                sy = (l.get("scale") or {}).get("y", 1)
                pins.append([names.get(l.get("amenity_id"), l.get("amenity_id")),
                             round(l["x"] + l["width"] * sx / 2), round(l["y"] + l["height"] * sy / 2)])
        write_csv(os.path.join(folder, "amenity-pins.csv"), pins, ["Amenity", "x (px)", "y (px)"])
        svg.render_preview(render, w, h, [], os.path.join(folder, "amenities-overview_preview.jpg"),
                           [(n, x, y) for n, x, y in pins])
        ctx.report.add("Amenities", f"- `{rel(ctx, render)}` — обзорный рендер {w}×{h}, {len(pins)} пинов "
                                    "(точки, не контуры) в `amenity-pins.csv`")


def step_map(ctx):
    s = ctx.map_scene
    if not s:
        return
    log(f"== Map: scene '{s['name']}'")
    folder = os.path.join(ctx.out, "00_map")
    os.makedirs(folder, exist_ok=True)
    render = os.path.join(folder, "map.jpg")
    if not os.path.exists(render) or not decodes(render):
        images.stitch_dzi(cdn_url(s["background"]["high_resolution"]), render)
    ctx.report.add("Карта", f"- `{rel(ctx, render)}` — {size_str(render)}")


# --------------------------------------------------------------------------- #
# The apartments table (continues the manager's Google Sheet)
# --------------------------------------------------------------------------- #

def plan_by_printed(ctx, u):
    """The source layout whose bedroom count matches the type printed on the floor plan
    (only when the source's own layout disagrees with it). Prefers a layout that has a tour."""
    printed = getattr(ctx, "unit_printed", {}).get(u["_id"])
    check = getattr(ctx, "unit_layout_check", {}).get(u["_id"], "")
    if not printed or not check.startswith("НЕТ"):
        return None
    n = int(printed[0])
    cands = [p for p in ctx.unitplans.values()
             if p.get("unit_type") != "villa_floor" and ocr.bedrooms(p.get("name")) == n]
    cands.sort(key=lambda p: (not p.get("tour_id"), "duplex" in (p.get("name") or "").lower()))
    return cands[0] if cands else None


def step_table(ctx):
    base = core.VIEWER
    rows = []
    for u in ctx.units:
        b = ctx.buildings[u["building_id"]]
        tour, plan = ctx.unit_tour(u)
        scene = ctx.floor_scene_of.get((u["building_id"], str(u["floor_id"])))
        q = (f"?building_id={u['building_id']}&floor_id={u['floor_id']}&unit_id={u['_id']}"
             f"&unit_name={u['name']}")
        link = lambda kind: f"{base}/{scene}/{u['unitplan_id']}{q}&type={kind}" if scene else ""  # noqa: E731
        rows.append([
            b["name"], u["floor_id"], u["name"], (u.get("metadata") or {}).get("name", ""),
            u.get("bedroom") or "", u.get("measurement") or "", plan.get("name") or "",
            tour_folder_name(tour) if tour else "НЕТ ТУРА",
            f"04_tours/{tour_folder_name(tour)}/" if tour else "",
            topview_name(ctx, u),
            getattr(ctx, "unit_contour", {}).get(u["_id"], ""),
            getattr(ctx, "unit_printed", {}).get(u["_id"], ""),
            getattr(ctx, "unit_layout_check", {}).get(u["_id"], ""),
            (fixed := plan_by_printed(ctx, u)) and fixed.get("name") or "",
            fixed and tour_folder_name(ctx.tours.get(fixed.get("tour_id")) or {"name": "НЕТ ТУРА"}) or "",
            link("interior") if tour else "", link("unitplan"),
            u["_id"], u.get("unitplan_id"),
        ])
    write_csv(os.path.join(ctx.out, "apartments.csv"), rows, [
        "Здание", "Этаж", "Квартира", "Имя в источнике", "Спальни", "Площадь, sqft", "Планировка",
        "Тур", "Папка тура", "Топ-вью (по планировке)", "Контур на плане этажа",
        "Тип на плане этажа (OCR)", "Планировка источника совпадает с планом",
        "Планировка по плану (если источник ошибается)", "Тур по плану (если источник ошибается)",
        "Ссылка на тур (источник)", "Ссылка на топ-вью (источник)", "unit_id источника", "unitplan_id источника"])
    per_b = {}
    for u in ctx.units:
        per_b[ctx.buildings[u["building_id"]]["name"]] = per_b.get(ctx.buildings[u["building_id"]]["name"], 0) + 1
    ctx.report.add("Квартиры", f"- `apartments.csv` — {len(rows)} квартир: "
                               + ", ".join(f"{k} — {v}" for k, v in sorted(per_b.items())))
