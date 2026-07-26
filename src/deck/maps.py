"""Online slippy-map tiles with a size-capped, offline-capable disk cache.

Web-Mercator (OSM/"slippy") tiles are fetched on demand over Wi-Fi and cached
to disk. The cache is BOUNDED: when it grows past MAP_CACHE_MAX_MB the
least-recently-used tiles are deleted, so it can never bloat storage. Any tile
you have already viewed is served from disk, so places you have browsed keep
working with no network.

Nothing here decodes coordinates offline (no reverse geocoding, no GPS on this
device) -- the city search reuses weather.geocode().
"""

import io
import json
import math
import os
import queue
import threading
import time
import urllib.request
from collections import OrderedDict

from PIL import Image

import config

_DIR = os.path.dirname(os.path.abspath(__file__))
_VIEW = os.path.join(_DIR, "maps.json")

CACHE_DIR = getattr(config, "MAP_CACHE_DIR", os.path.join(_DIR, "tiles"))
CACHE_MAX_MB = getattr(config, "MAP_CACHE_MAX_MB", 150)
TILE_URL = getattr(
    config, "MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
USER_AGENT = getattr(
    config, "MAP_USER_AGENT", "RG35XXH-Deck/1.0 (personal handheld map viewer)")

TILE_PX = 256
MIN_Z, MAX_Z = 2, 18
_FAIL_COOLDOWN = 15.0   # seconds before retrying a tile that failed to fetch


# --- coordinate math (Web Mercator, fractional tile units) -------------------

def deg2num(lat, lon, z):
    """(lat, lon) -> fractional tile coordinates at zoom z."""
    n = 1 << z
    lat = max(-85.05, min(85.05, lat))
    lat_r = math.radians(lat)
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n
    return x, y


def num2deg(x, y, z):
    """Fractional tile coordinates -> (lat, lon)."""
    n = 1 << z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lat, lon


def pan(lat, lon, z, dpx, dpy):
    """Shift the centre by (dpx, dpy) screen pixels, returning new (lat, lon)."""
    n = 1 << z
    x, y = deg2num(lat, lon, z)
    x = (x + dpx / TILE_PX) % n          # wrap around the antimeridian
    y = max(0.0, min(n, y + dpy / TILE_PX))
    return num2deg(x, y, z)


# --- disk cache (size-capped, LRU by mtime) ----------------------------------

_cache_bytes = None      # running total; None until first scan


def _tile_path(z, x, y):
    return os.path.join(CACHE_DIR, str(z), str(x), "%d.png" % y)


def _scan_cache_bytes():
    total = 0
    for root, _, names in os.walk(CACHE_DIR):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def cache_size_mb():
    global _cache_bytes
    if _cache_bytes is None:
        _cache_bytes = _scan_cache_bytes()
    return _cache_bytes / (1024 * 1024)


def cache_summary():
    return "%dMB/%d" % (round(cache_size_mb()), CACHE_MAX_MB)


def _enforce_cap():
    """Delete least-recently-used tiles until back under 90% of the cap."""
    global _cache_bytes
    if CACHE_MAX_MB <= 0:
        return
    if _cache_bytes is None:
        _cache_bytes = _scan_cache_bytes()
    limit = CACHE_MAX_MB * 1024 * 1024
    if _cache_bytes <= limit:
        return
    files = []
    for root, _, names in os.walk(CACHE_DIR):
        for name in names:
            fp = os.path.join(root, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, fp))
    files.sort()                       # oldest use first
    target = int(limit * 0.9)
    for _, size, fp in files:
        if _cache_bytes <= target:
            break
        try:
            os.remove(fp)
            _cache_bytes -= size
        except OSError:
            pass


def read_disk(z, x, y):
    """Cached PNG bytes for a tile, marking it recently used; None if absent."""
    p = _tile_path(z, x, y)
    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError:
        return None
    try:
        os.utime(p, None)              # LRU: touch on read, not just on write
    except OSError:
        pass
    return data


def write_disk(z, x, y, data):
    global _cache_bytes, _writes
    p = _tile_path(z, x, y)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        old = os.path.getsize(p) if os.path.exists(p) else 0
        with open(p, "wb") as f:
            f.write(data)
    except OSError:
        return
    if _cache_bytes is None:
        _cache_bytes = _scan_cache_bytes()
    else:
        _cache_bytes += len(data) - old
    # Cheap when under the cap (just an int compare); the directory walk only
    # runs on the rare write that actually pushes us over.
    _enforce_cap()


def forget_tile(z, x, y):
    """Drop one cached tile from disk (used to force a re-fetch on refresh)."""
    global _cache_bytes
    p = _tile_path(z, x, y)
    try:
        size = os.path.getsize(p)
        os.remove(p)
    except OSError:
        return
    if _cache_bytes is not None:
        _cache_bytes -= size


def clear_cache():
    global _cache_bytes
    import shutil
    try:
        shutil.rmtree(CACHE_DIR)
    except OSError:
        pass
    _cache_bytes = 0


def fetch_tile(z, x, y):
    """Download one tile over the network; None on any failure (e.g. offline)."""
    url = TILE_URL.format(z=z, x=x, y=y)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception:
        return None


def _decode(data):
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


# --- tile manager: memory cache + background fetch ---------------------------

class TileManager:
    """Serves decoded PIL tiles. Memory and disk hits return instantly; a miss
    enqueues a network fetch and returns None, so the UI never blocks. When a
    fetch lands, `dirty` is set so the screen can redraw and fill in."""

    def __init__(self, max_mem=96):
        self._mem = OrderedDict()       # (z,x,y) -> PIL.Image
        self._max_mem = max_mem
        self._inflight = set()
        self._failed = {}               # (z,x,y) -> monotonic time of failure
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self.dirty = threading.Event()
        self._stop = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def get(self, z, x, y):
        n = 1 << z
        if not (0 <= x < n and 0 <= y < n):
            return None
        key = (z, x, y)
        with self._lock:
            img = self._mem.get(key)
            if img is not None:
                self._mem.move_to_end(key)
                return img
        data = read_disk(z, x, y)
        if data is not None:
            img = _decode(data)
            if img is not None:
                self._put_mem(key, img)
            return img
        with self._lock:
            recent = self._failed.get(key)
            if recent and time.monotonic() - recent < _FAIL_COOLDOWN:
                return None             # don't hammer a tile that just failed
            if key not in self._inflight:
                self._inflight.add(key)
                self._q.put(key)
        return None

    def _put_mem(self, key, img):
        with self._lock:
            self._mem[key] = img
            self._mem.move_to_end(key)
            while len(self._mem) > self._max_mem:
                self._mem.popitem(last=False)

    def _run(self):
        while not self._stop:
            try:
                key = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if key is None:
                break
            z, x, y = key
            data = fetch_tile(z, x, y)
            with self._lock:
                self._inflight.discard(key)
                if data:
                    self._failed.pop(key, None)
                else:
                    self._failed[key] = time.monotonic()
            if data:
                write_disk(z, x, y, data)
                img = _decode(data)
                if img is not None:
                    self._put_mem(key, img)
                    self.dirty.set()

    def refresh(self, keys):
        """Forget these tiles (memory + disk) so the next get() re-fetches the
        current version. Used to refresh what's on screen."""
        with self._lock:
            for k in keys:
                self._mem.pop(k, None)
                self._failed.pop(k, None)
        for (z, x, y) in keys:
            forget_tile(z, x, y)

    def clear(self):
        clear_cache()
        with self._lock:
            self._mem.clear()
            self._failed.clear()

    def close(self):
        self._stop = True
        self._q.put(None)


# --- remembered view ---------------------------------------------------------

def load_view():
    try:
        with open(_VIEW) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_view(view):
    try:
        with open(_VIEW, "w") as f:
            json.dump(view, f)
    except OSError:
        pass
