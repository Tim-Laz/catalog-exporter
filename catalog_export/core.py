"""Shared plumbing: HTTP, catalog API, CDN urls, ImageMagick, naming, the run report."""

import concurrent.futures as cf
import json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.parse, urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
# Filled in by configure() from the catalog link the user passes; nothing host-specific
# is hard-coded.
API_ORG = None   # https://api.<domain>/publicapis/organization/<org>
CDN = None       # https://storagecdn.<domain>/
VIEWER = None    # https://view.<domain>/<org>/projectscene/<project>
LINK_RE = re.compile(r"https?://view\.([^/\s]+)/([^/\s?#]+)/projectscene/([0-9a-fA-F]{24})")
WORKERS = 12


def configure(link):
    """Read domain, org and project from a catalog link like
    https://view.<domain>/<org>/projectscene/<project>/... Returns (org, project) or None."""
    global API_ORG, CDN, VIEWER
    m = LINK_RE.search(link or "")
    if not m:
        return None
    domain, org, project = m.groups()
    API_ORG = f"https://api.{domain}/publicapis/organization/{org}"
    CDN = f"https://storagecdn.{domain}/"
    VIEWER = f"https://view.{domain}/{org}/projectscene/{project}"
    return org, project


def setup_console():
    """Never crash on a character the console cannot show (Windows consoles and
    redirected output default to a legacy code page)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def log(msg=""):
    print(msg, flush=True)


def kebab(s):
    """'Tower A' -> 'tower-a', '3BHK Duplex' -> '3bhk-duplex'. Safe for our DO slugs (no '_')."""
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s or "unnamed"


def slugify(value):
    """Standard slugify (lowercase, spaces to hyphens, punctuation dropped): '3BHK Duplex'
    -> '3bhk-duplex'. Top-view file names use it for the layout part."""
    value = re.sub(r"[^\w\s-]", "", str(value or "").lower())
    return re.sub(r"[-\s]+", "-", value).strip("-_")


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class NetworkError(RuntimeError):
    pass


_USE_CURL = False   # switched on when Python's own SSL certificates are missing


def _is_cert_error(e):
    return isinstance(getattr(e, "reason", e), ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def _fetch(url, dest, timeout):
    """Write url to the open file dest. A Python from python.org often has no CA
    certificates installed; macOS curl uses the system ones, so fall back to it."""
    global _USE_CURL
    if not _USE_CURL:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                shutil.copyfileobj(r, dest, 1 << 16)
            return
        except Exception as e:  # noqa: BLE001
            if not _is_cert_error(e):
                raise
            _USE_CURL = True
            log("   (Python has no SSL certificates here — using the system curl instead)")
    dest.seek(0)
    dest.truncate()
    subprocess.run(["curl", "-sSfL", "--max-time", str(timeout), "-A", UA, url], stdout=dest, check=True)


def _retry(fn, url, tries=4):
    last = None
    for n in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (n + 1))
    raise NetworkError(f"{url}\n  {last}")


def get_bytes(url):
    def go():
        with tempfile.TemporaryFile() as f:
            _fetch(url, f, 60)
            f.seek(0)
            return f.read()
    return _retry(go, url)


def get_json(url):
    return json.loads(get_bytes(url).decode("utf-8"))


def get_text(url):
    return get_bytes(url).decode("utf-8")


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def looks_valid(path):
    """Cheap check that a downloaded file is a complete image without starting a process
    per file (slow on Windows): the right header and, for JPEG/PNG, the end marker near
    the end. Only an unusual file falls back to a full decode."""
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - 4096))
            tail = f.read()
    except OSError:
        return False
    if head.startswith(b"\xff\xd8"):
        return b"\xff\xd9" in tail or decodes(path)
    if head.startswith(b"\x89PNG"):
        return b"IEND" in tail or decodes(path)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return int.from_bytes(head[4:8], "little") + 8 == size
    return False


def decodes(path):
    """Full decode. A truncated file only produces a warning ("premature end of data"),
    so the warning text is checked as well as the exit code."""
    r = subprocess.run(["magick", path + "[0]", "null:"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, text=True, errors="replace")
    return r.returncode == 0 and not re.search(r"premature end|corrupt|truncat|unexpected end", r.stderr, re.I)


def download(url, path):
    """Download to path (atomic). Files from an earlier run are kept, so a re-run
    resumes — unless an image no longer decodes, then it is fetched again."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        if not path.lower().endswith(IMAGE_EXT) or looks_valid(path):
            return path
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def go():
        with open(path + ".part", "wb") as f:
            _fetch(url, f, 180)
        if path.lower().endswith(IMAGE_EXT) and not looks_valid(path + ".part"):
            raise NetworkError("downloaded file is not a valid image")
        os.replace(path + ".part", path)
        return path
    try:
        return _retry(go, url)
    finally:
        if os.path.exists(path + ".part"):
            os.remove(path + ".part")


def download_many(pairs):
    """pairs = [(url, path), ...] downloaded in parallel. Ctrl+C cancels the queue
    at once instead of waiting for every pending file."""
    ex = cf.ThreadPoolExecutor(WORKERS)
    try:
        for f in cf.as_completed([ex.submit(download, *p) for p in pairs]):
            f.result()
    except BaseException:
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    ex.shutdown()


def head_ok(url):
    if _USE_CURL:
        return subprocess.run(["curl", "-sfI", "--max-time", "30", "-A", UA, url],
                              stdout=subprocess.DEVNULL).returncode == 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def cdn_url(firebase_url):
    """The catalog hands out Firebase Storage links; the same object is on their public CDN
    under the decoded path. The CDN form lets us address sibling files (DZI tiles)."""
    if not firebase_url:
        return None
    if "/o/" not in firebase_url:
        return firebase_url
    path = urllib.parse.unquote(firebase_url.split("/o/", 1)[1].split("?", 1)[0])
    return CDN + urllib.parse.quote(path)


def url_ext(url, default="jpg"):
    path = urllib.parse.unquote(urllib.parse.urlparse(url or "").path)
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return {"jpeg": "jpg"}.get(ext, ext) or default


# --------------------------------------------------------------------------- #
# Catalog API
# --------------------------------------------------------------------------- #

def unwrap(x):
    return x.get("_doc", x) if isinstance(x, dict) else x


def items(payload):
    if isinstance(payload, dict):
        return [unwrap(v) for v in payload.values()]
    if isinstance(payload, list):
        return [unwrap(v) for v in payload]
    return []


class Api:
    """Public, unauthenticated catalog endpoints. Raw responses are kept for save()."""

    def __init__(self, org, project):
        self.org_base = API_ORG
        self.base = f"{self.org_base}/project/{project}"
        self.raw = {}

    def _get(self, url, name):
        if name not in self.raw:
            self.raw[name] = get_json(url)
        data = self.raw[name]
        return data.get("data", data) if isinstance(data, dict) else data

    def save(self, folder):
        os.makedirs(folder, exist_ok=True)
        for name, data in self.raw.items():
            with open(os.path.join(folder, name + ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def project(self, endpoint):
        return self._get(f"{self.base}/{endpoint}", endpoint)

    def org(self, endpoint):
        return self._get(f"{self.org_base}/{endpoint}", endpoint)


# --------------------------------------------------------------------------- #
# ImageMagick
# --------------------------------------------------------------------------- #

def require_tools():
    if shutil.which("magick"):
        return
    log("ERROR: ImageMagick (the 'magick' command) was not found.")
    if os.name == "nt":
        log("Install it once:   winget install -e --id ImageMagick.ImageMagick")
        log("then close this window, open a new one and run the command again.")
    else:
        log("Install it once:   brew install imagemagick")
    sys.exit(1)


def magick(*args):
    subprocess.check_call(["magick", *map(str, args)], stderr=subprocess.DEVNULL)


def image_size(path):
    try:
        out = subprocess.check_output(["magick", "identify", "-format", "%w %h", path + "[0]"],
                                      stderr=subprocess.DEVNULL).decode().split()
        return int(out[0]), int(out[1])
    except Exception:  # noqa: BLE001
        return None


def to_webp(src, dst, lossless=False, max_side=None):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    args = [src]
    if max_side:
        args += ["-resize", f"{max_side}x{max_side}>"]
    args += ["-define", "webp:lossless=true"] if lossless else ["-quality", "90", "-define", "webp:alpha-quality=100"]
    magick(*args, dst)


# --------------------------------------------------------------------------- #
# Run report
# --------------------------------------------------------------------------- #

class Report:
    """Collects what was exported and every data problem found, then writes REPORT.md."""

    def __init__(self):
        self.sections = {}   # title -> list of markdown lines
        self.issues = []     # (section, text)

    def add(self, section, line):
        self.sections.setdefault(section, []).append(line)

    def issue(self, section, text):
        # kept for REPORT.md only; the console shows just the count at the end
        self.issues.append((section, text))

    def write(self, path, header):
        lines = [header, ""]
        lines += ["## Проблемы в данных источника", ""]
        if self.issues:
            lines += [f"- **{s}**: {t}" for s, t in self.issues]
        else:
            lines.append("Не найдено.")
        for title, body in self.sections.items():
            lines += ["", f"## {title}", "", *body]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
