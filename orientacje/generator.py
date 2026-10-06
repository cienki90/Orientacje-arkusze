"""Generowanie planów orientacyjnych dla stacji trafo na podstawie szablonu DXF."""
from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .dxf_tags import DxfDocument, Entity, fmt_float, mtext_escape, mtext_plain
from .uklady import pl2000_to_wgs84, pl2000_zone_from_easting, epsg_for_zone

LABEL_RE = re.compile(r"STACJA\s+TRAFO\s*(\d+)\s*-\s*(\d+)", re.IGNORECASE)
SCALE_TEXT_RE = re.compile(r"^\s*1\s*:\s*[\d\s.]+$")
STANDARD_SCALES = (500, 1000, 2000, 2500, 5000, 10000, 15000, 20000, 25000, 50000, 100000)
UNKNOWN_PLACE = "????"
OSM_ATTRIBUTION = "Podkład mapowy: © autorzy OpenStreetMap (openstreetmap.org/copyright)"


# ---------------------------------------------------------------------------
# geometria
# ---------------------------------------------------------------------------
def point_in_polygon(x, y, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xc > x:
                inside = not inside
    return inside


def polygon_area(pts) -> float:
    return abs(sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))) / 2


def dist_point_polygon(x, y, pts) -> float:
    best = math.inf
    for i in range(len(pts)):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (x1 + t * dx), y - (y1 + t * dy)))
    return best


def bbox_of(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# model danych
# ---------------------------------------------------------------------------
@dataclass
class Station:
    prefix: str                 # "03"
    number: str                 # "0541"
    label: Entity
    x: float                    # położenie stacji (grot odnośnika) - easting
    y: float                    # northing
    zone_poly: Entity | None = None
    zone_pts: list = field(default_factory=list)
    # wyniki
    place: dict = field(default_factory=dict)
    place_source: str = ""
    scale: int = 0
    dxf_name: str = ""
    basemap_name: str = ""
    notes: list = field(default_factory=list)

    @property
    def designation(self) -> str:
        return f"{self.prefix}-{self.number}"

    @property
    def nr(self) -> int:
        return int(self.number)

    @property
    def extent(self):
        """Prostokąt, który ma się zmieścić na arkuszu."""
        if self.zone_pts:
            xmin, ymin, xmax, ymax = bbox_of(self.zone_pts + [(self.x, self.y)])
            return xmin, ymin, xmax, ymax
        return self.x, self.y, self.x, self.y


def _leader_tip(ent: Entity):
    """Grot odnośnika MULTILEADER (pierwszy wierzchołek LEADER_LINE)."""
    in_line = False
    x = None
    for _, code, val in ent.items():
        if code == 304 and val.strip() == "LEADER_LINE{":
            in_line = True
        elif in_line and code == 10:
            x = float(val)
        elif in_line and code == 20 and x is not None:
            return x, float(val)
    # brak linii odnośnika - punkt tekstu
    return float(ent.get(12) or ent.get(10)), float(ent.get(22) or ent.get(20))


def _lwpoly_points(ent: Entity):
    pts, x = [], None
    for _, code, val in ent.items():
        if code == 10:
            x = float(val)
        elif code == 20 and x is not None:
            pts.append((x, float(val)))
            x = None
    return pts


def find_stations(doc: DxfDocument, zone_layer: str = "!trafo",
                  max_zone_dist: float = 150.0) -> list[Station]:
    stations = []
    for ent in doc.query(("MULTILEADER", "MTEXT", "TEXT"), section="ENTITIES"):
        if ent.paperspace:
            continue
        raw = ent.get(304) if ent.type == "MULTILEADER" else "".join(ent.values(3) + ent.values(1))
        m = LABEL_RE.search(mtext_plain(raw or ""))
        if not m:
            continue
        if ent.type == "MULTILEADER":
            x, y = _leader_tip(ent)
        else:
            x, y = float(ent.get(10)), float(ent.get(20))
        stations.append(Station(m.group(1), m.group(2), ent, x, y))

    polys = [(e, _lwpoly_points(e)) for e in doc.query("LWPOLYLINE", section="ENTITIES",
                                                        layer=zone_layer) if not e.paperspace]
    polys = [(e, p) for e, p in polys if len(p) >= 3]
    for st in stations:
        inside = [(polygon_area(p), e, p) for e, p in polys if point_in_polygon(st.x, st.y, p)]
        if inside:
            _, st.zone_poly, st.zone_pts = min(inside, key=lambda t: t[0])
            continue
        near = sorted(((dist_point_polygon(st.x, st.y, p), e, p) for e, p in polys), key=lambda t: t[0])
        if near and near[0][0] <= max_zone_dist:
            _, st.zone_poly, st.zone_pts = near[0]
            st.notes.append(f"obrys dopasowany wg odległości ({near[0][0]:.0f} m)")
        else:
            st.notes.append("nie znaleziono obrysu zasięgu stacji")

    # duplikaty numerów
    seen = {}
    for st in stations:
        if st.designation in seen:
            st.notes.append("UWAGA: numer stacji występuje w szablonie więcej niż raz")
        seen[st.designation] = st
    stations.sort(key=lambda s: (s.prefix, s.nr))
    return stations


# ---------------------------------------------------------------------------
# elementy arkusza
# ---------------------------------------------------------------------------
@dataclass
class SheetLayout:
    viewport: Entity
    paper_w: float
    paper_h: float
    target_x: float
    target_y: float
    template_scale: float

    @classmethod
    def from_doc(cls, doc: DxfDocument) -> "SheetLayout":
        vps = [e for e in doc.query("VIEWPORT", section="ENTITIES") if e.paperspace]
        # pierwsza rzutnia układu to zawsze "rzutnia papieru" (widok samego arkusza)
        vps = vps[1:] if len(vps) > 1 else vps
        if not vps:
            raise ValueError("Szablon nie ma rzutni (VIEWPORT) w obszarze papieru")
        vp = max(vps, key=lambda e: float(e.get(40)) * float(e.get(41)))
        twist = float(vp.get(51) or 0.0)
        if abs(twist) > 1e-9:
            raise ValueError("Rzutnia szablonu jest obrócona (kąt skręcenia widoku) - nieobsługiwane")
        w, h = float(vp.get(40)), float(vp.get(41))
        return cls(vp, w, h, float(vp.get(17) or 0.0), float(vp.get(27) or 0.0),
                   float(vp.get(45)) / h * 1000.0)

    def model_size(self, scale: int):
        """Wymiary rzutni w jednostkach modelu (m) dla skali 1:scale (papier w mm)."""
        return self.paper_w * scale / 1000.0, self.paper_h * scale / 1000.0

    def choose_scale(self, extent, base: int, fixed: bool, fill: float = 0.9) -> int:
        ex_w, ex_h = extent[2] - extent[0], extent[3] - extent[1]
        candidates = [base] if fixed else [base] + [s for s in STANDARD_SCALES if s > base]
        for s in candidates:
            w, h = self.model_size(s)
            if ex_w <= w * fill and ex_h <= h * fill:
                return s
        return candidates[-1]

    def apply(self, vp: Entity, cx: float, cy: float, scale: int):
        vp.set(12, cx - self.target_x)
        vp.set(22, cy - self.target_y)
        vp.set(45, self.paper_h * scale / 1000.0)


def format_scale(scale: int) -> str:
    return "1:" + f"{scale:,}".replace(",", " ")


def _replace_in_texts(ents, pattern: re.Pattern, repl) -> int:
    """Podmienia wzorzec w tekstach; zwraca liczbę trafień (także gdy tekst się nie zmienił)."""
    hits = 0
    for e in ents:
        for idx in e.tag_indices(1) + e.tag_indices(3):
            old = e.doc.tags[idx][2]
            new, n = pattern.subn(repl, old)
            hits += n
            if new != old:
                e.doc.set_value(idx, new)
    return hits


def _relative_windows_path(target: Path, start: Path) -> str:
    try:
        rel = os.path.relpath(target, start)
    except ValueError:            # inne dyski w Windows
        rel = str(target)
    return str(PureWindowsPath(Path(rel)))


# ---------------------------------------------------------------------------
# generator
# ---------------------------------------------------------------------------
@dataclass
class Options:
    template: Path
    out_dir: Path
    excel: Path
    cache_dir: Path
    scale: int = 10000
    fixed_scale: bool = False
    basemap: bool = True
    zoom: int = 16
    resolution: float = 1.0
    basemap_margin: float = 0.15
    tile_url: str | None = None
    user_agent: str | None = None
    geocode: bool = True
    names_csv: Path | None = None
    only: set | None = None
    keep_other_stations: bool = False
    zone_layer: str = "!trafo"
    table_block: str = "tabelka"
    place_phrase: str = "w miejscowości"
    station_placeholder: str | None = None     # np. "03-xx"; None = autodetekcja
    file_pattern: str = "{nr}.dxf"


def load_names_csv(path: Path) -> dict:
    """CSV/TXT: numer stacji ; miejscowość  (separator ; , lub tab)."""
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ";", delimiters=";,\t")
    out = {}
    for row in csv.reader(text.splitlines(), dialect):
        if len(row) < 2 or not row[0].strip():
            continue
        m = re.search(r"(\d+)\s*$", row[0].strip())
        if not m:
            continue                                   # nagłówek
        out[int(m.group(1))] = row[1].strip()
    return out


def _safe_filename(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", s).strip()


class Generator:
    def __init__(self, opt: Options, log=print):
        self.opt = opt
        self.log = log
        self.template = DxfDocument.load(opt.template)
        self.layout = SheetLayout.from_doc(self.template)
        self.stations = find_stations(self.template, opt.zone_layer)
        if not self.stations:
            raise ValueError("Nie znaleziono w szablonie odnośników 'STACJA TRAFO ...'")
        x0 = self.stations[0].x
        self.zone = pl2000_zone_from_easting(x0)
        self._detect_table_texts()
        self.names = load_names_csv(opt.names_csv) if opt.names_csv else {}
        self.geocoder = self.tiles = None
        if opt.geocode:
            from .osm import Geocoder, DEFAULT_USER_AGENT
            self.geocoder = Geocoder(opt.cache_dir, opt.user_agent or DEFAULT_USER_AGENT)
        if opt.basemap:
            try:
                import PIL  # noqa: F401
            except ImportError:
                raise ValueError("Do pobierania podkładu OSM potrzebna jest biblioteka Pillow: "
                                 "pip install pillow  (albo uruchom z opcją --bez-podkladu)") from None
            from .osm import TileSource, DEFAULT_TILE_URL, DEFAULT_USER_AGENT
            self.tiles = TileSource(opt.cache_dir, opt.tile_url or DEFAULT_TILE_URL,
                                    opt.user_agent or DEFAULT_USER_AGENT)

    # --- analiza szablonu ----------------------------------------------------
    def _table_entities(self, doc):
        ents = [e for e in doc.query(("MTEXT", "TEXT", "ATTDEF"), section="BLOCKS")
                if e.block == self.opt.table_block]
        ents += [e for e in doc.query(("MTEXT", "TEXT", "ATTRIB"), section="ENTITIES") if e.paperspace]
        return ents

    def _detect_table_texts(self):
        ents = self._table_entities(self.template)
        texts = [v for e in ents for v in e.values(1) + e.values(3)]
        ph = self.opt.station_placeholder
        if ph is None:
            for t in texts:
                m = re.search(r"\b\d+-x+\b", t, re.IGNORECASE)
                if m:
                    ph = m.group(0)
                    break
        if not ph or not any(ph in t for t in texts):
            raise ValueError("W tabelce nie znaleziono znacznika numeru stacji (np. '03-xx'). "
                             "Podaj go opcją --znacznik-stacji.")
        self.station_placeholder = ph
        phrase = re.escape(self.opt.place_phrase).replace(r"\ ", r"\s+")
        self.place_re = re.compile(rf"({phrase}\s+)([^{{}}\\]+?)(\s*)(?=$|[{{}}\\])", re.IGNORECASE)
        found = [m.group(2) for t in texts for m in [self.place_re.search(t)] if m]
        if not found:
            raise ValueError(f"W tabelce nie znaleziono frazy '{self.opt.place_phrase} <nazwa>'.")
        self.template_place = found[0]
        self.scale_texts = [e for e in self.template.query("MTEXT", section="ENTITIES")
                            if e.paperspace and SCALE_TEXT_RE.match(mtext_plain(e.get(1) or ""))]
        images = [e for e in self.template.query("IMAGE", section="ENTITIES") if not e.paperspace]
        self.image = images[0] if images else None

    # --- nazwy miejscowości ------------------------------------------------------
    def resolve_place(self, st: Station):
        lat, lon = pl2000_to_wgs84(st.x, st.y, self.zone)
        st.place = {"lat": lat, "lon": lon, "miejscowosc": "", "gmina": "", "powiat": "",
                    "wojewodztwo": ""}
        if self.geocoder:
            try:
                st.place.update(self.geocoder.locality(lat, lon))
                st.place_source = "OSM Nominatim"
            except Exception as exc:  # noqa: BLE001
                st.notes.append(f"geokodowanie nieudane: {exc}")
                self.log(f"UWAGA: Nominatim niedostępny ({exc}) - pomijam geokodowanie kolejnych stacji")
                self.geocoder = None
        if st.nr in self.names:
            st.place["miejscowosc"] = self.names[st.nr]
            st.place_source = "plik nazw"
        if not st.place["miejscowosc"]:
            st.place["miejscowosc"] = UNKNOWN_PLACE
            st.place_source = "BRAK"
            st.notes.append("NIE USTALONO miejscowości - popraw w tabelce lub podaj w pliku --nazwy")

    # --- jeden arkusz -------------------------------------------------------------
    def build_sheet(self, st: Station):
        opt = self.opt
        doc = self.template.copy()
        H = lambda ent: doc.by_handle[ent.handle.upper()]  # noqa: E731

        # 1) tabelka: numer stacji i miejscowość
        place = st.place["miejscowosc"]
        place_mt = mtext_escape(place)
        ents = self._table_entities(doc)
        n1 = _replace_in_texts(ents, re.compile(re.escape(self.station_placeholder)),
                               lambda m: st.designation)
        n2 = _replace_in_texts(ents, self.place_re, lambda m: m.group(1) + place_mt + m.group(3))
        if not n1 or not n2:
            st.notes.append("nie podmieniono wszystkich pól tabelki")

        # 2) rzutnia: środek i skala
        ext = st.extent
        st.scale = self.layout.choose_scale(ext, opt.scale, opt.fixed_scale)
        if st.scale != opt.scale:
            st.notes.append(f"zasięg nie mieści się w 1:{opt.scale} - użyto {format_scale(st.scale)}")
        cx, cy = (ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2
        self.layout.apply(H(self.layout.viewport), cx, cy, st.scale)
        for t in self.scale_texts:
            H(t).set(1, format_scale(st.scale))

        # 3) usuń odnośniki i obrysy pozostałych stacji
        if not opt.keep_other_stations:
            for other in self.stations:
                if other is st:
                    continue
                for ent in (other.label, other.zone_poly):
                    if ent is None or ent is st.zone_poly or ent.handle.upper() not in doc.by_handle:
                        continue
                    target = H(ent)
                    if doc.tags[target.start] is None:
                        continue
                    for w in doc.delete_entity(target):
                        st.notes.append(f"pozostawione odwołanie: {w}")

        # 4) podkład mapowy
        base = opt.template.stem
        st.dxf_name = _safe_filename(opt.file_pattern.format(
            nr=st.nr, numer=st.number, oznaczenie=st.designation, miejscowosc=place, szablon=base))
        if not st.dxf_name.lower().endswith(".dxf"):
            st.dxf_name += ".dxf"
        if self.image is not None:
            if self.tiles is not None:
                self._apply_basemap(doc, st, cx, cy)
            else:
                self._relink_template_image(doc)
        doc.save(opt.out_dir / st.dxf_name)

    def _apply_basemap(self, doc, st, cx, cy):
        from .osm import render_basemap
        opt = self.opt
        w, h = self.layout.model_size(st.scale)
        k = 1 + 2 * opt.basemap_margin
        bbox = (cx - w * k / 2, cy - h * k / 2, cx + w * k / 2, cy + h * k / 2)
        png = opt.out_dir / f"{Path(st.dxf_name).stem}_podklad.png"
        try:
            info = render_basemap(bbox, self.zone, png, self.tiles, opt.zoom, opt.resolution)
        except Exception as exc:  # noqa: BLE001
            st.notes.append(f"podkład OSM nieudany: {exc} - zostawiono obraz z szablonu")
            self.log(f"UWAGA: nie udało się pobrać podkładu ({exc}) - kolejne arkusze bez podkładu OSM")
            self.tiles = None
            self._relink_template_image(doc)
            return
        st.basemap_name = png.name
        img = doc.by_handle[self.image.handle.upper()]
        res = info["resolution"]
        img.set(10, info["xmin"])
        img.set(20, info["ymin"])
        img.set(11, res)
        img.set(21, 0.0)
        img.set(12, 0.0)
        img.set(22, res)
        img.set(13, float(info["width"]))
        img.set(23, float(info["height"]))
        clip = img.tag_indices(14)
        if len(clip) >= 2:                      # prostokątna ramka przycięcia
            doc.set_value(clip[0], -0.5)
            doc.set_value(img.tag_indices(24)[0], -0.5)
            doc.set_value(clip[1], info["width"] - 0.5)
            doc.set_value(img.tag_indices(24)[1], info["height"] - 0.5)
        idef = doc.by_handle.get((img.get(340) or "").strip().upper())
        if idef is not None:
            idef.set(1, png.name)
            idef.set(10, float(info["width"]))
            idef.set(20, float(info["height"]))
        self._add_attribution(doc)

    def _add_attribution(self, doc):
        if not self.scale_texts:
            return
        anchor = doc.by_handle[self.scale_texts[0].handle.upper()]
        vp = doc.by_handle[self.layout.viewport.handle.upper()]
        x = float(vp.get(10)) - self.layout.paper_w / 2 + 1.5
        y = float(vp.get(20)) - self.layout.paper_h / 2 + 1.5
        doc.add_entity_before(anchor, [
            (0, "MTEXT"), (5, doc.new_handle()), (330, anchor.get(330)), (100, "AcDbEntity"),
            (67, "     1"), (8, anchor.layer or "0"), (100, "AcDbMText"),
            (10, fmt_float(x)), (20, fmt_float(y)), (30, "0.0"), (40, "1.5"), (41, "0.0"),
            (71, "     7"), (72, "     1"), (1, OSM_ATTRIBUTION), (73, "     1"), (44, "1.0"),
            (90, "        3"), (63, "   256"), (45, "1.2"), (441, "        0"),
        ])

    def _relink_template_image(self, doc):
        """Bez pobierania podkładu: zostaw obraz z szablonu, ale popraw ścieżkę względną."""
        idef = doc.by_handle.get((self.image.get(340) or "").strip().upper())
        if idef is None:
            return
        ref = PureWindowsPath(idef.get(1) or "")
        tdir = self.opt.template.resolve().parent
        cands = [tdir / Path(*ref.parts), tdir / ref.name] if ref.name else []
        found = next((c for c in cands if c.exists()), None)
        if found:
            idef.set(1, _relative_windows_path(found.resolve(), self.opt.out_dir.resolve()))

    # --- całość --------------------------------------------------------------------
    def run(self):
        opt = self.opt
        opt.out_dir.mkdir(parents=True, exist_ok=True)
        todo = [s for s in self.stations if not opt.only or s.nr in opt.only or s.number in opt.only]
        self.log(f"Szablon: {opt.template}  |  układ PL-2000 strefa {self.zone} (EPSG:{epsg_for_zone(self.zone)})")
        self.log(f"Znaleziono stacji: {len(self.stations)}, do wygenerowania: {len(todo)}")
        self.log(f"Tabelka: znacznik '{self.station_placeholder}', miejscowość w szablonie '{self.template_place}'")
        for st in todo:
            self.resolve_place(st)
            self.build_sheet(st)
            extra = f", podkład {st.basemap_name}" if st.basemap_name else ""
            self.log(f"  {st.designation:>8}  {st.place['miejscowosc']:<20} 1:{st.scale:<6} -> "
                     f"{st.dxf_name}{extra}" + (f"   [{'; '.join(st.notes)}]" if st.notes else ""))
        self.write_excel(todo)
        if self.tiles is not None:
            self.log(f"Pobrano nowych kafli OSM: {self.tiles.downloaded}")
        self.log(f"Zestawienie: {opt.excel}")
        return todo

    def write_excel(self, stations):
        from .xlsx import write_xlsx
        cols = [("Lp.", 5, "int"), ("Oznaczenie stacji", 12, None), ("Nr stacji", 9, "int"),
                ("Miejscowość", 20, None), ("Gmina", 16, None), ("Powiat", 16, None),
                ("Województwo", 16, None), ("Źródło nazwy", 15, None),
                ("X PL-2000 [m] (północ)", 15, "2"), ("Y PL-2000 [m] (wschód)", 15, "2"),
                ("Szerokość geogr. [°]", 13, "6"), ("Długość geogr. [°]", 13, "6"),
                ("Obrys zasięgu w szablonie", 12, None),
                ("Skala arkusza", 10, None), ("Plik DXF", 16, None), ("Podkład mapowy", 22, None),
                ("Uwagi", 60, None)]
        rows = []
        for i, st in enumerate(stations, start=1):
            p = st.place
            rows.append([i, st.designation, st.nr, p.get("miejscowosc", ""), p.get("gmina", ""),
                         p.get("powiat", ""), p.get("wojewodztwo", ""), st.place_source,
                         round(st.y, 2), round(st.x, 2), round(p["lat"], 6), round(p["lon"], 6),
                         "tak" if st.zone_pts else "nie",
                         format_scale(st.scale), st.dxf_name,
                         st.basemap_name or ("szablon" if self.image is not None else ""),
                         "; ".join(st.notes)])
        self.opt.excel.parent.mkdir(parents=True, exist_ok=True)
        write_xlsx(self.opt.excel, "Stacje trafo", cols, rows)
