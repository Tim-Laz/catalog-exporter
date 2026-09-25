"""Shared plumbing: HTTP, catalog API, CDN urls, ImageMagick, naming, the run report."""

import concurrent.futures as cf
import threading
import json, os, re, shutil, ssl, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

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


def check_python():
    if sys.version_info < (3, 9):
        log(f"ERROR: Python 3.9 or newer is needed (this is {sys.version.split()[0]}).")
        sys.exit(1)


def run_command():
    """How the user starts Python here, for messages."""
    return "py" if os.name == "nt" else "python3"


def setup_console():
    """Never crash on a character the console cannot show (Windows consoles and
    redirected output default to a legacy code page)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        _windows_keep_running()


def _windows_keep_running():
    """Two Windows habits that make a long run look frozen:
    - QuickEdit: a click inside the console window starts a text selection, and the
      program is paused at its next output until the selection ends. Switched off for
      this window while we run (restored at exit).
    - Sleep: the PC may go to sleep during the run. Kept awake until we finish."""
    import atexit, ctypes
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p        # a 64-bit handle, not an int
        kernel32.GetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel32.SetConsoleMode.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
        handle = kernel32.GetStdHandle(-10)                    # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ENABLE_QUICK_EDIT_MODE, ENABLE_EXTENDED_FLAGS = 0x0040, 0x0080
            kernel32.SetConsoleMode(handle, (mode.value | ENABLE_EXTENDED_FLAGS) & ~ENABLE_QUICK_EDIT_MODE)
            atexit.register(kernel32.SetConsoleMode, handle, mode.value)
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        atexit.register(kernel32.SetThreadExecutionState, ES_CONTINUOUS)
    except Exception:  # noqa: BLE001 — a convenience only; never block the export
        pass


def log(msg=""):
    p = Progress._active
    if p is not None and p.live:                # clear the live progress line first
        sys.stdout.write("\r" + " " * p._width + "\r")
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
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing a non-https URL: {url[:80]}")
    if not _USE_CURL:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    dest.write(chunk)
                    _count(len(chunk))
            return
        except Exception as e:  # noqa: BLE001
            if not _is_cert_error(e):
                raise
            _USE_CURL = True
            log("   (Python has no SSL certificates here — using the system curl instead)")
    dest.seek(0)
    dest.truncate()
    subprocess.run(["curl", "-sSfL", "--proto", "=https", "--max-time", str(timeout), "-A", UA, "--", url],
                   stdout=dest, check=True)
    _count(dest.tell())


def _retry(fn, url, tries=4):
    """Retry network trouble; a 4xx answer (the file is not there) fails at once."""
    last = None
    for n in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise NetworkError(f"{url}\n  HTTP {e.code} (not available on the server)") from None
            last = e
        except ValueError as e:                     # e.g. a non-https URL: retrying won't help
            raise NetworkError(f"{url}\n  {e}") from None
        except (urllib.error.URLError, OSError, subprocess.CalledProcessError, NetworkError) as e:
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
        return int.from_bytes(head[4:8], "little") + 8 == size or decodes(path)
    return decodes(path)             # another format: let ImageMagick decide


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


def download_many(pairs, progress=None):
    """pairs = [(url, path), ...] downloaded in parallel; each finished file advances
    `progress`. Ctrl+C cancels the queue at once instead of waiting for every pending file."""
    ex = cf.ThreadPoolExecutor(WORKERS)
    try:
        for f in cf.as_completed([ex.submit(download, *p) for p in pairs]):
            f.result()
            if progress:
                progress.advance()
    except BaseException:
        # wait for the downloads already running: on Windows their open files would
        # otherwise block deleting the temp folder and hide the real error
        ex.shutdown(wait=True, cancel_futures=True)
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


# --------------------------------------------------------------------------- #
# Progress: one live line per step
# --------------------------------------------------------------------------- #

_bytes = 0
_bytes_lock = threading.Lock()


def _count(n):
    global _bytes
    with _bytes_lock:
        _bytes += n


class Progress:
    """One status line for a step, redrawn in place twice a second so a long download
    visibly moves:  '   Tour 3BR  [########------------] 3/7 panoramas  41.2 MB (2.3 MB/s)  38s'.
    When the step ends the line stays as its one-line summary. A Progress opened inside
    another one stays silent (its bytes still show in the outer line). When the output
    is not a console (redirected to a file) only the summary line is printed."""

    _active = None

    def __init__(self, label, total=None, unit=""):
        self.label, self.total, self.unit, self.done = label, total, unit, 0
        self.nested = Progress._active is not None
        self.live = not self.nested and sys.stdout.isatty()
        self._b0, self._t0, self._width = _bytes, time.time(), 0
        self._stop = threading.Event()
        if not self.nested:
            Progress._active = self
        if self.live:
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()

    def advance(self, n=1):
        self.done += n

    def _line(self):
        secs = max(time.time() - self._t0, 0.001)
        mb = (_bytes - self._b0) / 1e6
        parts = [f"   {self.label}"]
        if self.total:
            filled = min(20, int(20 * self.done / self.total))
            parts.append(f"[{'#' * filled}{'-' * (20 - filled)}] {self.done}/{self.total} {self.unit}".rstrip())
        if mb >= 0.05:
            parts.append(f"{mb:.1f} MB ({mb / secs:.1f} MB/s)")
        parts.append(f"{int(secs)}s")
        return "  ".join(parts)

    def _draw(self, end=""):
        # never wider than the window: a wrapped line can't be redrawn in place
        line = self._line()[:max(20, shutil.get_terminal_size((100, 20)).columns - 1)]
        sys.stdout.write("\r" + line + " " * max(0, self._width - len(line)) + end)
        sys.stdout.flush()
        self._width = len(line)

    def _tick(self):
        while not self._stop.wait(0.5):
            self._draw()

    @classmethod
    def close_active(cls):
        """End the running line cleanly before an error message is printed."""
        if cls._active is not None:
            cls._active.close()

    def close(self):
        if self.nested or Progress._active is not self:
            return
        Progress._active = None
        self._stop.set()
        if self.live:
            self._thread.join()
            self._draw("\n")
        else:
            log(self._line())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


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
