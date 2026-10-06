"""OpenStreetMap: nazwy miejscowości (Nominatim) i podkład rastrowy (kafle XYZ).

Zasady korzystania z serwerów OSM:
  * Nominatim: max 1 zapytanie/s, własny User-Agent  -> https://operations.osmfoundation.org/policies/nominatim/
  * kafle:     rozsądne użycie, cache, User-Agent      -> https://operations.osmfoundation.org/policies/tiles/
Wyniki są zapisywane w katalogu cache, więc ponowne uruchomienie nie pobiera
ich drugi raz.
"""
from __future__ import annotations

import io
import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .uklady import pl2000_to_wgs84, wgs84_to_tile_px

DEFAULT_USER_AGENT = "Orientacje-arkusze/1.0 (generator planow orientacyjnych DXF)"
DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Kolejność pól adresu Nominatim, z których bierzemy "miejscowość"
PLACE_KEYS = ("village", "town", "city", "hamlet", "isolated_dwelling",
              "suburb", "quarter", "neighbourhood", "municipality")


class OsmError(RuntimeError):
    pass


def _http_get(url: str, user_agent: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                               "Accept-Language": "pl"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as exc:          # noqa: BLE001 - ponawiamy każdą awarię sieci
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise OsmError(f"Nie udało się pobrać {url}: {last}")


# ---------------------------------------------------------------------------
# Nominatim
# ---------------------------------------------------------------------------
class Geocoder:
    def __init__(self, cache_dir: Path, user_agent: str = DEFAULT_USER_AGENT,
                 place_keys=PLACE_KEYS):
        self.cache_path = Path(cache_dir) / "nominatim.json"
        self.user_agent = user_agent
        self.place_keys = place_keys
        self._last = 0.0
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    def _save(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=1),
                                   encoding="utf-8")

    def reverse(self, lat: float, lon: float) -> dict:
        key = f"{lat:.6f},{lon:.6f}"
        if key not in self.cache:
            wait = 1.1 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            q = urllib.parse.urlencode({"format": "jsonv2", "lat": f"{lat:.7f}",
                                        "lon": f"{lon:.7f}", "zoom": 18,
                                        "addressdetails": 1, "accept-language": "pl"})
            data = _http_get(f"{NOMINATIM_URL}?{q}", self.user_agent)
            self._last = time.time()
            self.cache[key] = json.loads(data.decode("utf-8"))
            self._save()
        return self.cache[key]

    def locality(self, lat: float, lon: float) -> dict:
        """Zwraca słownik: miejscowosc, gmina, powiat, wojewodztwo, adres."""
        res = self.reverse(lat, lon)
        addr = res.get("address", {}) if isinstance(res, dict) else {}
        name = next((addr[k] for k in self.place_keys if addr.get(k)), "")
        gmina = addr.get("municipality", "")
        return {
            "miejscowosc": name,
            "gmina": gmina.replace("gmina ", ""),
            "powiat": addr.get("county", "").replace("powiat ", ""),
            "wojewodztwo": addr.get("state", "").replace("województwo ", ""),
            "adres": res.get("display_name", "") if isinstance(res, dict) else "",
        }


# ---------------------------------------------------------------------------
# Podkład rastrowy
# ---------------------------------------------------------------------------
class TileSource:
    def __init__(self, cache_dir: Path, url: str = DEFAULT_TILE_URL,
                 user_agent: str = DEFAULT_USER_AGENT, tile_size: int = 256,
                 delay: float = 0.05):
        self.url = url
        self.user_agent = user_agent
        self.tile_size = tile_size
        self.delay = delay
        safe = urllib.parse.urlparse(url).netloc.replace(":", "_") or "tiles"
        self.dir = Path(cache_dir) / "kafle" / safe
        self.downloaded = 0

    def tile_bytes(self, z: int, x: int, y: int) -> bytes:
        path = self.dir / str(z) / str(x) / f"{y}.png"
        if path.exists() and path.stat().st_size > 0:
            return path.read_bytes()
        url = self.url.format(z=z, x=x, y=y, s="abc"[(x + y) % 3])
        data = _http_get(url, self.user_agent)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        self.downloaded += 1
        time.sleep(self.delay)
        return data


def _require_pillow():
    try:
        from PIL import Image  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise OsmError("Do pobierania podkładu potrzebny jest Pillow: pip install pillow") from exc


def render_basemap(bbox, zone: int, out_png: Path, tiles: TileSource,
                   zoom: int = 16, resolution: float = 1.0, mesh_step: int = 64):
    """Tworzy podkład OSM w układzie PL-2000 dla prostokąta bbox=(xmin,ymin,xmax,ymax).

    Kafle (Web Mercator) są sklejane i przeliczane (siatka MESH Pillow) do
    regularnej siatki PL-2000, więc obraz można wstawić do DXF bez obrotu.
    Zapisuje PNG + plik georeferencji .pgw. Zwraca słownik z parametrami obrazu.
    """
    _require_pillow()
    from PIL import Image

    xmin, ymin, xmax, ymax = bbox
    width = max(1, int(math.ceil((xmax - xmin) / resolution)))
    height = max(1, int(math.ceil((ymax - ymin) / resolution)))
    xmax, ymin = xmin + width * resolution, ymax - height * resolution

    def to_px(px, py):
        lat, lon = pl2000_to_wgs84(xmin + px * resolution, ymax - py * resolution, zone)
        return wgs84_to_tile_px(lat, lon, zoom, tiles.tile_size)

    # zakres kafli
    corners = [to_px(px, py) for px in (0, width) for py in (0, height)]
    gx = [c[0] for c in corners]
    gy = [c[1] for c in corners]
    ts = tiles.tile_size
    tx0, tx1 = int(min(gx) // ts), int(max(gx) // ts)
    ty0, ty1 = int(min(gy) // ts), int(max(gy) // ts)
    n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    if n_tiles > 2500:
        raise OsmError(f"Za dużo kafli ({n_tiles}) - zmniejsz poziom zoom")

    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * ts, (ty1 - ty0 + 1) * ts), "white")
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            with Image.open(io.BytesIO(tiles.tile_bytes(zoom, tx, ty))) as im:
                mosaic.paste(im.convert("RGB"), ((tx - tx0) * ts, (ty - ty0) * ts))
    ox, oy = tx0 * ts, ty0 * ts

    # siatka transformacji: prostokąt wynikowy -> czworokąt w mozaice
    cache = {}

    def src(px, py):
        if (px, py) not in cache:
            x, y = to_px(px, py)
            cache[(px, py)] = (x - ox, y - oy)
        return cache[(px, py)]

    mesh = []
    for y0 in range(0, height, mesh_step):
        y1 = min(height, y0 + mesh_step)
        for x0 in range(0, width, mesh_step):
            x1 = min(width, x0 + mesh_step)
            quad = (*src(x0, y0), *src(x0, y1), *src(x1, y1), *src(x1, y0))
            mesh.append(((x0, y0, x1, y1), quad))

    # Pillow >= 9.1 ma enumy Image.Transform / Image.Resampling, starsze - stałe modułu
    mesh_mode = getattr(Image, "Transform", Image).MESH
    resample = getattr(Image, "Resampling", Image).BICUBIC
    out = mosaic.transform((width, height), mesh_mode, mesh, resample=resample,
                           fillcolor="white")
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_png, optimize=True)
    # world file: środek lewego górnego piksela
    out_png.with_suffix(".pgw").write_text(
        "\n".join(f"{v:.10f}" for v in (resolution, 0.0, 0.0, -resolution,
                                         xmin + resolution / 2, ymax - resolution / 2)) + "\n")
    return {"path": out_png, "width": width, "height": height,
            "xmin": xmin, "ymin": ymin, "resolution": resolution, "tiles": n_tiles}
