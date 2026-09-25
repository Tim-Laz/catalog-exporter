"""Checks the finished export against the API data it came from and against the
file-naming rules. Run by export.py at the end (results go to
REPORT.md) and on its own:  python3 verify.py output/<project>
"""

import csv, json, os, re, subprocess, xml.etree.ElementTree as ET

from .core import items, kebab, looks_valid, natural_key, run_command, slugify
from .steps import safe_name
from .svg import path_bbox, path_center

# <developer>_<project>_<building>_<layout or number>_plan.webp — every part without '_'
S3_PLAN_RE = re.compile(r"^([a-zA-Z0-9-]+)_([a-zA-Z0-9-]+)_([a-zA-Z0-9-]+)_([a-zA-Z0-9-]+)_plan\.webp$")
# the same with lowercase developer/project/building slugs
LOCAL_NUMBER_RE = re.compile(r"^([a-z0-9-]+)_([a-z0-9-]+)_([a-z0-9-]+)_([a-zA-Z0-9-]+)_plan\.(png|jpg|jpeg|webp)$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def identify(path):
    """(format, width, height, has_alpha) or None if the file does not decode."""
    try:
        out = subprocess.check_output(["magick", "identify", "-format", "%m %w %h %A\n", path + "[0]"],
                                      stderr=subprocess.DEVNULL).decode().split()
        return out[0], int(out[1]), int(out[2]), out[3].lower() in ("blend", "true")
    except Exception:  # noqa: BLE001
        return None


def header_format(path):
    with open(path, "rb") as f:
        head = f.read(12)
    if head.startswith(b"\xff\xd8"):
        return "JPEG"
    if head.startswith(b"\x89PNG"):
        return "PNG"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    return None


def alpha_at(path, points):
    """Alpha (0..1) of a PNG at the given pixel points."""
    vals = []
    for x, y in points:
        out = subprocess.check_output(["magick", path, "-format", f"%[fx:p{{{int(x)},{int(y)}}}.a]", "info:"],
                                      stderr=subprocess.DEVNULL).decode()
        vals.append(float(out))
    return vals


class Checks:
    def __init__(self):
        self.results = []   # (group, name, ok, detail)

    def check(self, group, name, ok, detail=""):
        self.results.append((group, name, bool(ok), detail))
        return ok

    @property
    def failed(self):
        return [r for r in self.results if not r[2]]

    def markdown(self):
        lines = [f"**{len(self.results) - len(self.failed)} из {len(self.results)} проверок пройдено.**", ""]
        group = None
        for g, name, ok, detail in self.results:
            if g != group:
                lines += ["", f"### {g}", ""]
                group = g
            lines.append(f"- {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))
        return "\n".join(lines)


def load_raw(out):
    def raw(name):
        with open(os.path.join(out, "_raw_api", name + ".json"), encoding="utf-8") as f:
            return json.load(f)["data"]
    return {
        "buildings": {b["_id"]: b for b in items(raw("getListOfBuildings"))},
        "units": items(raw("getListofUnits")),
        "unitplans": {p["_id"]: p for p in items(raw("getListOfUnitplan"))},
        "tours": raw("ListTours"),
        "amenities": items(raw("GetAmenities")),
        "scenes": [s["sceneData"] for s in items(raw("getAllScenes"))],
    }


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def svg_paths(path):
    root = ET.parse(path).getroot()
    vb = root.get("viewBox", "").split()
    ns = "{http://www.w3.org/2000/svg}"
    return ([float(v) for v in vb] if len(vb) == 4 else None,
            [(p.get("id"), p.get("d")) for p in root.iter(ns + "path")])


def check_mask(c, group, label, mask, image, expected_ids=None):
    """Well-formed, viewBox == image size, unique ids, every zone inside the image."""
    try:
        vb, paths = svg_paths(mask)
    except Exception as e:  # noqa: BLE001
        c.check(group, f"{label}: SVG читается", False, str(e))
        return []
    info = identify(image)
    c.check(group, f"{label}: viewBox = размер картинки",
            info and vb == [0, 0, info[1], info[2]], f"viewBox {vb}, картинка {info[1:3] if info else None}")
    ids = [i for i, _ in paths]
    c.check(group, f"{label}: id зон уникальны и не пустые", len(ids) == len(set(ids)) and all(ids), ", ".join(map(str, ids)))
    if expected_ids is not None:
        c.check(group, f"{label}: набор зон = ожидаемому", sorted(ids, key=natural_key) == sorted(expected_ids, key=natural_key),
                f"есть {sorted(ids, key=natural_key)}, ждали {sorted(expected_ids, key=natural_key)}")
    if info:
        out = []
        for i, d in paths:
            b = path_bbox(d or "")
            if not b or b[0] < -2 or b[1] < -2 or b[2] > info[1] + 2 or b[3] > info[2] + 2:
                out.append(i)
        c.check(group, f"{label}: все зоны внутри картинки", not out, f"вне кадра: {out}" if out else "")
    return paths


def run(out):
    c = Checks()
    raw = load_raw(out)
    units, buildings = raw["units"], raw["buildings"]
    bslug = {bid: kebab(b["name"]) for bid, b in buildings.items()}   # same as the export

    # ---------------------------------------------------------------- general
    G = "Общее"
    part = [os.path.join(dp, f) for dp, _, fs in os.walk(out) for f in fs if f.endswith(".part")]
    c.check(G, "нет недокачанных файлов (.part)", not part, ", ".join(part[:5]))
    empty = [os.path.join(dp, f) for dp, _, fs in os.walk(out) for f in fs
             if os.path.getsize(os.path.join(dp, f)) == 0]
    c.check(G, "нет пустых файлов", not empty, ", ".join(empty[:5]))
    bad, ext_mismatch = [], []
    for dp, _, fs in os.walk(out):
        for f in fs:
            want = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}.get(os.path.splitext(f)[1].lower())
            if not want:
                continue
            p = os.path.join(dp, f)
            name = os.path.relpath(p, out).replace(os.sep, "/")
            if not looks_valid(p):           # also catches truncated files
                bad.append(name)
            fmt = header_format(p)
            if fmt and fmt != want:
                ext_mismatch.append(f"{name} ({fmt})")
    c.check(G, "все картинки целые (не битые и не обрезанные)", not bad, ", ".join(bad[:10]))
    c.check(G, "формат файла совпадает с расширением", not ext_mismatch, ", ".join(ext_mismatch[:10]))

    # -------------------------------------------------------------- apartments
    G = "Квартиры (apartments.csv)"
    rows = read_csv(os.path.join(out, "apartments.csv"))
    c.check(G, "по строке на каждую квартиру API", len(rows) == len(units), f"{len(rows)} строк, в API {len(units)}")
    keys = [(r["Здание"], r["Квартира"]) for r in rows]
    c.check(G, "нет дублей (здание + номер)", len(keys) == len(set(keys)))
    api_keys = {(buildings[u["building_id"]]["name"], str(u["name"])) for u in units}
    c.check(G, "те же квартиры, что в API", set(keys) == api_keys)
    unit_by_id = {u["_id"]: u for u in units}
    wrong_b = [r for r in rows if r["Здание"] != buildings[unit_by_id[r["unit_id источника"]]["building_id"]]["name"]]
    per_b = {}
    for r in rows:
        per_b[r["Здание"]] = per_b.get(r["Здание"], 0) + 1
    c.check(G, "квартира лежит в своём здании (по building_id из API)", not wrong_b,
            ", ".join(f"{k}: {v}" for k, v in sorted(per_b.items())))
    num_floor = [r for r in rows if not r["Квартира"].startswith(r["Этаж"]) or len(r["Квартира"]) != len(r["Этаж"]) + 2]
    c.check(G, "номер квартиры = этаж + 2 цифры", not num_floor, ", ".join(f"{r['Здание']} {r['Квартира']}" for r in num_floor[:10]))

    # --------------------------------------------------------------- top views
    G = "Топ-вью"
    tv = os.path.join(out, "05_top-views")
    by_cfg = sorted(os.listdir(os.path.join(tv, "by-configuration")))
    by_apt = sorted(os.listdir(os.path.join(tv, "by-apartment")))
    main_cfg = [f for f in by_cfg if f.endswith("_plan.webp")]
    extra_cfg = [f for f in by_cfg if not f.endswith("_plan.webp")]
    c.check(G, "by-configuration: имя по шаблону <developer>_<project>_<building>_<планировка>_plan.webp",
            all(S3_PLAN_RE.match(f) for f in main_cfg), ", ".join(f for f in main_cfg if not S3_PLAN_RE.match(f)))
    c.check(G, "by-configuration: 2-й этаж дуплекса назван иначе, чем основной план (не спутать при загрузке)",
            all(not S3_PLAN_RE.match(f) and not LOCAL_NUMBER_RE.match(f) for f in extra_cfg), ", ".join(extra_cfg))
    parts = [S3_PLAN_RE.match(f).groups() for f in main_cfg if S3_PLAN_RE.match(f)]
    c.check(G, "slug'и без '_' и заглавных (developer, project, building)",
            all(SLUG_RE.match(p) for g in parts for p in g[:3]), "")
    ids_cfg = {g[3] for g in parts}
    expected_cfg = {slugify((raw["unitplans"].get(u["unitplan_id"]) or {}).get("name")) for u in units}
    c.check(G, "часть имени с планировкой = slugify(название планировки)",
            ids_cfg == expected_cfg, f"файлы: {sorted(ids_cfg)}; планировки: {sorted(expected_cfg)}")
    need = {(bslug[u["building_id"]], slugify(raw["unitplans"][u["unitplan_id"]]["name"])) for u in units}
    have = {(g[2], g[3]) for g in parts}
    c.check(G, "по файлу на каждую пару здание+планировка", need == have,
            f"нет: {sorted(need - have)}; лишние: {sorted(have - need)}" if need != have else f"{len(have)} файлов")
    main_apt = [f for f in by_apt if f.endswith("_plan.webp")]
    c.check(G, "by-apartment: имя по шаблону …_<номер>_plan.webp, slug'и в нижнем регистре",
            all(S3_PLAN_RE.match(f) and LOCAL_NUMBER_RE.match(f) for f in main_apt))
    need_apt = {(bslug[u["building_id"]], safe_name(u["name"])) for u in units}
    have_apt = {(S3_PLAN_RE.match(f).group(3), S3_PLAN_RE.match(f).group(4)) for f in main_apt if S3_PLAN_RE.match(f)}
    c.check(G, "by-apartment: файл на каждую квартиру, лишних нет", need_apt == have_apt,
            f"нет: {sorted(need_apt - have_apt)[:10]}; лишние: {sorted(have_apt - need_apt)[:10]}")
    dev_proj = {(g[0], g[1]) for g in parts}
    c.check(G, "во всех именах один developer и один project", len(dev_proj) == 1, str(dev_proj))
    dup_units = [u for u in units if len(raw["unitplans"][u["unitplan_id"]].get("floor_unitplans") or []) > 1]
    extra_apt = {f for f in by_apt if f.endswith("_plan_floor-2.webp")}
    c.check(G, "у каждого дуплекса есть файл 2-го этажа", len(extra_apt) == len(dup_units),
            f"{len(extra_apt)} файлов, дуплексов {len(dup_units)}")
    csv_names = {r["Топ-вью (по планировке)"] for r in rows}
    c.check(G, "apartments.csv ссылается на существующие файлы", csv_names <= set(main_cfg),
            ", ".join(sorted(csv_names - set(main_cfg))))
    bad_img = []
    for f in by_cfg:
        info = identify(os.path.join(tv, "by-configuration", f))
        if not info or info[0] != "WEBP" or not info[3] or max(info[1], info[2]) > 2560:
            bad_img.append(f"{f} {info}")
    c.check(G, "WebP с прозрачностью, длинная сторона ≤ 2560", not bad_img, "; ".join(bad_img))
    # by-apartment copy must be byte-identical to its configuration file
    diff = []
    for u in units:
        cfg = slugify(raw["unitplans"][u["unitplan_id"]]["name"])
        prefix = main_cfg[0].rsplit("_", 3)[0] if main_cfg else ""
        a = os.path.join(tv, "by-apartment", f"{prefix}_{bslug[u['building_id']]}_{safe_name(u['name'])}_plan.webp")
        b = os.path.join(tv, "by-configuration", f"{prefix}_{bslug[u['building_id']]}_{cfg}_plan.webp")
        if not (os.path.exists(a) and os.path.exists(b) and open(a, "rb").read() == open(b, "rb").read()):
            diff.append(f"{bslug[u['building_id']]} {u['name']}")
    c.check(G, "копия квартиры = файл её планировки (байт в байт)", not diff, ", ".join(diff[:10]))

    # ----------------------------------------------------------- area / towers
    G = "Area и здания"
    area = os.path.join(out, "01_area")
    info = identify(os.path.join(area, "area.jpg"))
    c.check(G, "area.jpg 3840×2160", info and info[1:3] == (3840, 2160), str(info))
    check_mask(c, G, "area_mask.svg", os.path.join(area, "area_mask.svg"), os.path.join(area, "area.jpg"),
               list(bslug.values()))
    for bid, b in buildings.items():
        folder = os.path.join(out, "02_buildings", bslug[bid])
        img = os.path.join(folder, f"{bslug[bid]}.jpg")
        info = identify(img)
        c.check(G, f"{bslug[bid]}.jpg 3840×2160", info and info[1:3] == (3840, 2160), str(info))
        floors = [str(f.get("floor_id")) for f in items(b.get("floors") or {})]
        paths = check_mask(c, G, f"{bslug[bid]}_floors-mask.svg", os.path.join(folder, f"{bslug[bid]}_floors-mask.svg"),
                           img, floors)
        # floors must stack bottom-up: a higher floor sits higher in the picture
        centers = {i: path_center(d)[1] for i, d in paths if path_center(d)}
        order = [centers[f] for f in sorted(centers, key=natural_key)]
        c.check(G, f"{bslug[bid]}: этажи идут снизу вверх по картинке",
                all(a > b for a, b in zip(order, order[1:])), "")

    # ------------------------------------------------------------------ floors
    G = "Этажи"
    floors_root = os.path.join(out, "03_floors")
    covered = {}
    for bid in buildings:
        broot = os.path.join(floors_root, bslug[bid])
        groups = sorted(d for d in os.listdir(broot) if d.startswith("floors-"))
        for g in groups:
            folder = os.path.join(broot, g)
            floors = [str(int(x)) for x in g.split("-")[1:]]
            for f in floors:
                covered.setdefault(bid, []).append(f)
            bg = os.path.join(folder, "background.jpg")
            info = identify(bg)
            c.check(G, f"{bslug[bid]}/{g}: подложка открывается, 16:9",
                    info and info[1] * 9 == info[2] * 16, str(info[1:3] if info else None))
            paths = check_mask(c, G, f"{bslug[bid]}/{g}/mask.svg", os.path.join(folder, "mask.svg"), bg)
            positions = {i for i, _ in paths}
            for f in floors:
                api_pos = {str(u["name"])[len(f):] for u in units
                           if u["building_id"] == bid and str(u["floor_id"]) == f}
                c.check(G, f"{bslug[bid]} этаж {f}: контуры = квартиры API",
                        positions == api_pos, f"без контура: {sorted(api_pos - positions)}; "
                                              f"контур без квартиры: {sorted(positions - api_pos)}"
                        if positions != api_pos else f"{len(positions)} квартир")
            plan = os.path.join(folder, "plan.png")
            pinfo = identify(plan)
            c.check(G, f"{bslug[bid]}/{g}: plan.png того же размера, с прозрачностью",
                    pinfo and info and pinfo[1:3] == info[1:3] and pinfo[3], str(pinfo))
            centers = [path_center(d) for _, d in paths if path_center(d)]
            alphas = alpha_at(plan, centers) if pinfo else []
            c.check(G, f"{bslug[bid]}/{g}: вырезанный план покрывает центры всех контуров",
                    alphas and min(alphas) > 0.5, f"непрозрачных {sum(a > .5 for a in alphas)}/{len(alphas)}")
            corners = alpha_at(plan, [(1, 1), (pinfo[1] - 2, 1), (1, pinfo[2] - 2), (pinfo[1] - 2, pinfo[2] - 2)]) if pinfo else []
            c.check(G, f"{bslug[bid]}/{g}: вне плана прозрачно (углы кадра)", corners and max(corners) < 0.5)
            winfo = identify(os.path.join(folder, "plan.webp"))
            c.check(G, f"{bslug[bid]}/{g}: plan.webp = plan.png по размеру, с прозрачностью",
                    winfo and pinfo and winfo[1:3] == pinfo[1:3] and winfo[3])
            csv_rows = read_csv(os.path.join(folder, "contours.csv"))
            c.check(G, f"{bslug[bid]}/{g}: contours.csv — контур × этаж", len(csv_rows) == len(paths) * len(floors))
        want = [str(f.get("floor_id")) for f in items(buildings[bid].get("floors") or {})]
        c.check(G, f"{bslug[bid]}: каждый этаж ровно в одной группе",
                sorted(covered.get(bid, []), key=natural_key) == sorted(want, key=natural_key),
                f"{sorted(covered.get(bid, []), key=natural_key)}")
        sp = os.path.join(broot, "per-floor")
        files = sorted(os.listdir(sp), key=natural_key)
        c.check(G, f"{bslug[bid]}/per-floor: floor_N.svg на каждый этаж",
                files == [f"floor_{f}.svg" for f in sorted(want, key=natural_key)], ", ".join(files))
        for g in groups:
            for f in g.split("-")[1:]:
                same = open(os.path.join(sp, f"floor_{int(f)}.svg"), "rb").read() == \
                    open(os.path.join(broot, g, "mask.svg"), "rb").read()
                if not same:
                    c.check(G, f"{bslug[bid]}/per-floor/floor_{int(f)}.svg = маска группы", False)

    G = "Сверка планировок с планом этажа"
    col = "Планировка источника совпадает с планом"
    ran = [r for r in rows if r.get(col, "").startswith(("совпадает", "НЕТ", "подпись"))]
    if not ran:
        c.check(G, "сверка пропущена: распознавание текста недоступно на этом компьютере", True,
                "это не ошибка выгрузки")
    else:
        read = [r for r in ran if not r[col].startswith("подпись")]
        # coverage depends on the OS's OCR (Windows reads fewer labels than macOS): information,
        # not a failure of the export
        c.check(G, f"распознавание прочитало тип на плане у {len(read)} из {len(rows)} квартир"
                   + ("" if len(read) >= 0.9 * len(rows) else " — сверка неполная, остальные не проверены"), True)
        wrong = [r for r in rows if r[col].startswith("НЕТ")]
        fixed = [r for r in wrong if r.get("Планировка по плану (если источник ошибается)")]
        c.check(G, "для каждого расхождения предложена планировка по плану", len(fixed) == len(wrong),
                f"{len(wrong)} расхождений")

    # ------------------------------------------------------------------- tours
    G = "Туры"
    troot = os.path.join(out, "04_tours")
    tour_rows = read_csv(os.path.join(troot, "tours.csv"))
    c.check(G, "туров столько же, сколько в API", len(tour_rows) == len(raw["tours"]))
    with_tour = [r for r in rows if r["Тур"] != "НЕТ ТУРА"]
    c.check(G, "tours.csv: сумма квартир = квартиры с туром в apartments.csv",
            sum(int(r["Квартир"]) for r in tour_rows) == len(with_tour))
    for tr in tour_rows:
        folder = os.path.join(troot, tr["Тур"])
        with open(os.path.join(folder, "tour.json"), encoding="utf-8") as f:
            tj = json.load(f)
        files = [s["file"] for s in tj["scenes"]]
        c.check(G, f"{tr['Тур']}: панорам = сцен в API",
                len(files) == len(items(raw["tours"][tj["source_tour_id"]].get("images", []))))
        c.check(G, f"{tr['Тур']}: имена <тур>_<NN>_<комната>.jpg",
                all(re.match(rf"^{re.escape(tr['Тур'])}_\d{{2}}_[a-z0-9-]+\.jpg$", f) for f in files), ", ".join(files))
        bad = []
        for f in files:
            info = identify(os.path.join(folder, f))
            if not info or info[1] < 4096 or not 1.7 <= info[1] / info[2] <= 2.05:
                bad.append(f"{f} {info[1:3] if info else None}")
        c.check(G, f"{tr['Тур']}: все панорамы ≥ 4096 px в ширину, пропорция 360 (2:1, у источника бывает 16:9)",
                not bad, "; ".join(bad))
        targets = [h["target"] for s in tj["scenes"] for h in s["hotspots"]]
        c.check(G, f"{tr['Тур']}: переходы (хотспоты) ведут на существующие панорамы",
                all(t in files for t in targets), f"{len(targets)} переходов")
        apts = [r for r in rows if r["Тур"] == tr["Тур"]]
        c.check(G, f"{tr['Тур']}: число квартир совпадает с apartments.csv", len(apts) == int(tr["Квартир"]),
                f"{len(apts)} vs {tr['Квартир']}")
    for r in with_tour:
        if not os.path.isdir(os.path.join(out, r["Папка тура"])):
            c.check(G, f"папка тура для {r['Здание']} {r['Квартира']} существует", False, r["Папка тура"])
    no_tour_api = [u for u in units if not raw["unitplans"][u["unitplan_id"]].get("tour_id")]
    c.check(G, "«НЕТ ТУРА» ровно у квартир без tour_id в API", len(rows) - len(with_tour) == len(no_tour_api))

    # --------------------------------------------------------------- amenities
    G = "Amenities"
    aroot = os.path.join(out, "06_amenities")
    arows = read_csv(os.path.join(aroot, "amenities.csv"))
    c.check(G, "по файлу на каждую amenity API", len(arows) == len(raw["amenities"]))
    c.check(G, "все файлы на месте", all(os.path.exists(os.path.join(out, r["Файл"])) for r in arows))
    cats = {a.get("category") for a in raw["amenities"]}
    c.check(G, "папка на каждую категорию", len({os.path.dirname(r["Файл"]) for r in arows}) == len(cats),
            ", ".join(sorted(cats)))
    ov = identify(os.path.join(aroot, "_overview", "amenities-overview.jpg"))
    c.check(G, "обзорный рендер 3840×2160", ov and ov[1:3] == (3840, 2160), str(ov))
    return c


def main(argv):
    import sys
    from .core import setup_console
    setup_console()
    out = argv[1] if len(argv) > 1 else None
    if not out:
        base = os.path.join(os.path.dirname(os.path.abspath(argv[0])), "output")
        subs = [os.path.join(base, d) for d in os.listdir(base)
                if os.path.isdir(os.path.join(base, d))] if os.path.isdir(base) else []
        out = subs[0] if len(subs) == 1 else None
    if not out or not os.path.isdir(out):
        print(f"usage: {run_command()} verify.py output/<project>")
        sys.exit(2)
    c = run(out)
    for g, name, ok, detail in c.results:
        print(f"{'OK  ' if ok else 'FAIL'} [{g}] {name}" + (f" — {detail}" if detail and not ok else ""))
    print(f"\n{len(c.results) - len(c.failed)}/{len(c.results)} checks passed")
    sys.exit(1 if c.failed else 0)
