#!/usr/bin/env python3
"""Generuje osobne plany orientacyjne DXF dla każdej stacji trafo z szablonu.

Przykłady:
    python generuj_arkusze.py                       # szablon.dxf -> wyniki/
    python generuj_arkusze.py --stacje 541,664      # tylko wybrane stacje
    python generuj_arkusze.py --bez-podkladu        # bez pobierania mapy OSM
    python generuj_arkusze.py --nazwy nazwy.csv     # ręczne nazwy miejscowości
"""
from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from xml.sax.saxutils import escape

__version__ = "1.1.0"


# ============================================================================
# UKŁADY WSPÓŁRZĘDNYCH (PL-2000 / WGS84 / Web Mercator)
# ============================================================================
_A = 6378137.0
_F = 1 / 298.257222101
_N = _F / (2 - _F)
_N2, _N3, _N4, _N5, _N6 = (_N ** k for k in range(2, 7))
_RECT = _A / (1 + _N) * (1 + _N2 / 4 + _N4 / 64 + _N6 / 256)
_E = math.sqrt(_F * (2 - _F))

_ALPHA = (
    _N / 2 - 2 * _N2 / 3 + 5 * _N3 / 16 + 41 * _N4 / 180 - 127 * _N5 / 288 + 7891 * _N6 / 37800,
    13 * _N2 / 48 - 3 * _N3 / 5 + 557 * _N4 / 1440 + 281 * _N5 / 630 - 1983433 * _N6 / 1935360,
    61 * _N3 / 240 - 103 * _N4 / 140 + 15061 * _N5 / 26880 + 167603 * _N6 / 181440,
    49561 * _N4 / 161280 - 179 * _N5 / 168 + 6601661 * _N6 / 7257600,
    34729 * _N5 / 80640 - 3418889 * _N6 / 1995840,
    212378941 * _N6 / 319334400,
)
_BETA = (
    _N / 2 - 2 * _N2 / 3 + 37 * _N3 / 96 - _N4 / 360 - 81 * _N5 / 512 + 96199 * _N6 / 604800,
    _N2 / 48 + _N3 / 15 - 437 * _N4 / 1440 + 46 * _N5 / 105 - 1118711 * _N6 / 3870720,
    17 * _N3 / 480 - 37 * _N4 / 840 - 209 * _N5 / 4480 + 5569 * _N6 / 90720,
    4397 * _N4 / 161280 - 11 * _N5 / 504 - 830251 * _N6 / 7257600,
    4583 * _N5 / 161280 - 108847 * _N6 / 3991680,
    20648693 * _N6 / 638668800,
)

PL2000_K0 = 0.999923
PL2000_ZONES = {5: 15, 6: 18, 7: 21, 8: 24}   # strefa -> południk osiowy


def pl2000_zone_from_easting(e: float) -> int:
    zone = int(e // 1_000_000)
    if zone not in PL2000_ZONES:
        raise ValueError(f"Współrzędna {e:.1f} nie wygląda na PL-2000 (strefy 5-8)")
    return zone


def epsg_for_zone(zone: int) -> int:
    return 2171 + zone          # 5->2176, 6->2177, 7->2178, 8->2179


def _tm_forward(lat, lon, lon0, k0):
    phi, lam = math.radians(lat), math.radians(lon - lon0)
    t = math.sinh(math.atanh(math.sin(phi)) - _E * math.atanh(_E * math.sin(phi)))
    xi_p = math.atan2(t, math.cos(lam))
    eta_p = math.atanh(math.sin(lam) / math.sqrt(1 + t * t))
    xi, eta = xi_p, eta_p
    for j, a in enumerate(_ALPHA, start=1):
        xi += a * math.sin(2 * j * xi_p) * math.cosh(2 * j * eta_p)
        eta += a * math.cos(2 * j * xi_p) * math.sinh(2 * j * eta_p)
    return k0 * _RECT * eta, k0 * _RECT * xi       # (easting bez FE, northing)


def _tm_inverse(x, y, lon0, k0):
    xi, eta = y / (k0 * _RECT), x / (k0 * _RECT)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(_BETA, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    tau_p = math.sin(xi_p) / math.sqrt(math.sinh(eta_p) ** 2 + math.cos(xi_p) ** 2)
    lam = math.atan2(math.sinh(eta_p), math.cos(xi_p))
    tau = tau_p
    for _ in range(8):                              # Newton
        sigma = math.sinh(_E * math.atanh(_E * tau / math.sqrt(1 + tau * tau)))
        tp = tau * math.sqrt(1 + sigma * sigma) - sigma * math.sqrt(1 + tau * tau)
        d = (tau_p - tp) / math.sqrt(1 + tp * tp) * (1 + (1 - _E * _E) * tau * tau) / (
            (1 - _E * _E) * math.sqrt(1 + tau * tau))
        tau += d
        if abs(d) < 1e-14:
            break
    return math.degrees(math.atan(tau)), lon0 + math.degrees(lam)


def pl2000_to_wgs84(easting: float, northing: float, zone: int | None = None):
    """(E, N) PL-2000 -> (lat, lon)."""
    zone = zone or pl2000_zone_from_easting(easting)
    lon0 = PL2000_ZONES[zone]
    return _tm_inverse(easting - (zone * 1_000_000 + 500_000), northing, lon0, PL2000_K0)


def wgs84_to_pl2000(lat: float, lon: float, zone: int):
    e, n = _tm_forward(lat, lon, PL2000_ZONES[zone], PL2000_K0)
    return e + zone * 1_000_000 + 500_000, n


# --- Web Mercator / kafelki XYZ ------------------------------------------------
def wgs84_to_tile_px(lat: float, lon: float, zoom: int, tile_size: int = 256):
    """Zwraca globalne współrzędne pikselowe (x, y) w siatce kafli XYZ."""
    scale = tile_size * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale
    return x, y


# ============================================================================
# EDYCJA DXF NA POZIOMIE TAGÓW
# ============================================================================
# kody grupowe będące odwołaniami do uchwytów (handle)
_POINTER_CODES = set(range(320, 370)) | set(range(390, 400)) | {480, 481, 1005}

_CODEPAGES = {
    "ANSI_1250": "cp1250", "ANSI_1251": "cp1251", "ANSI_1252": "cp1252",
    "ANSI_874": "cp874", "ANSI_932": "cp932", "ANSI_936": "gbk",
}


def fmt_float(v: float) -> str:
    s = repr(float(v))
    return s if ("." in s or "e" in s or "n" in s) else s + ".0"


@dataclass
class Entity:
    doc: "DxfDocument"
    start: int          # indeks tagu (0, TYP)
    end: int            # indeks pierwszego tagu następnej encji (wyłącznie)
    type: str
    section: str | None
    block: str | None   # nazwa bloku (dla sekcji BLOCKS)
    handle: str | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    # --- odczyt -----------------------------------------------------------
    def tag_indices(self, code: int):
        tags = self.doc.tags
        return [i for i in range(self.start + 1, self.end)
                if tags[i] is not None and tags[i][0] == code]

    def get(self, code: int, n: int = 0, default=None):
        idx = self.tag_indices(code)
        return self.doc.tags[idx[n]][2] if len(idx) > n else default

    def values(self, code: int):
        return [self.doc.tags[i][2] for i in self.tag_indices(code)]

    def items(self):
        for i in range(self.start + 1, self.end):
            t = self.doc.tags[i]
            if t is not None:
                yield i, t[0], t[2]

    @property
    def layer(self):
        return (self.get(8) or "").strip()

    @property
    def paperspace(self) -> bool:
        return (self.get(67) or "0").strip() == "1"

    # --- zapis ------------------------------------------------------------
    def set(self, code: int, value, n: int = 0):
        idx = self.tag_indices(code)
        if len(idx) <= n:
            raise KeyError(f"{self.type} {self.handle}: brak tagu {code} (#{n})")
        self.doc.set_value(idx[n], value)


class DxfDocument:
    def __init__(self, data: bytes):
        self.newline = "\r\n" if b"\r\n" in data[:4096] else "\n"
        self.encoding = self._detect_encoding(data)
        text = data.decode(self.encoding, errors="surrogateescape")
        lines = text.split(self.newline)
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2:
            raise ValueError("Uszkodzony DXF: nieparzysta liczba linii")
        # tag = [kod_int, kod_surowy, wartosc] ; None = usunięty
        self.tags: list = [[int(lines[i].strip()), lines[i], lines[i + 1]]
                           for i in range(0, len(lines), 2)]
        self.inserts: dict[int, list] = {}   # indeks -> lista tagów do wstawienia PRZED nim
        self._index()

    @classmethod
    def load(cls, path) -> "DxfDocument":
        with open(path, "rb") as f:
            return cls(f.read())

    def copy(self) -> "DxfDocument":
        new = object.__new__(DxfDocument)
        new.newline, new.encoding = self.newline, self.encoding
        new.tags = [None if t is None else list(t) for t in self.tags]
        new.inserts = copy.deepcopy(self.inserts)
        new._index()
        return new

    @staticmethod
    def _detect_encoding(data: bytes) -> str:
        head = data[:20000].decode("latin1")
        ver = re.search(r"\$ACADVER\s*\r?\n\s*1\s*\r?\n(\w+)", head)
        if ver and ver.group(1) >= "AC1021":       # AutoCAD 2007+ => UTF-8
            return "utf-8"
        cp = re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n(\w+)", head)
        return _CODEPAGES.get(cp.group(1).upper(), "cp1252") if cp else "cp1252"

    # --- indeks encji -------------------------------------------------------
    def _index(self):
        self.entities: list[Entity] = []
        self.by_handle: dict[str, Entity] = {}
        sec = blk = None
        expect_sec = False
        cur = None
        named = False
        tags = self.tags
        n = len(tags)

        def close(end):
            if cur is not None:
                cur.end = end
                self.entities.append(cur)
                if cur.handle:
                    self.by_handle[cur.handle.upper()] = cur

        for i in range(n):
            t = tags[i]
            if t is None:
                continue
            code, _, val = t
            if code == 0:
                close(i)
                cur = None
                if val == "SECTION":
                    expect_sec = True
                    continue
                if val == "ENDSEC":
                    sec = None
                    continue
                if val == "EOF":
                    continue
                cur = Entity(self, i, n, val, sec, blk)
                named = False
            else:
                if expect_sec and code == 2:
                    sec, expect_sec = val, False
                    continue
                if cur is not None:
                    if code == 5 or (code == 105 and cur.type == "DIMSTYLE"):
                        if cur.handle is None:
                            cur.handle = val.strip()
                    if cur.type == "BLOCK" and code == 2 and not named:
                        named = True
                        blk = val
                        cur.block = val
        close(n)

    def query(self, type_=None, section=None, block=None, layer=None):
        for e in self.entities:
            if type_ and e.type not in (type_ if isinstance(type_, (tuple, list, set)) else (type_,)):
                continue
            if section and e.section != section:
                continue
            if block is not None and e.block != block:
                continue
            if layer is not None and e.layer != layer:
                continue
            if self.tags[e.start] is None:
                continue
            yield e

    # --- modyfikacje ----------------------------------------------------------
    def set_value(self, idx: int, value):
        if isinstance(value, float):
            value = fmt_float(value)
        self.tags[idx][2] = str(value)

    def header_var(self, name: str):
        for i, t in enumerate(self.tags):
            if t and t[0] == 9 and t[2].strip() == name:
                return i + 1
        return None

    def new_handle(self) -> str:
        idx = self.header_var("$HANDSEED")
        seed = int(self.tags[idx][2].strip(), 16)
        self.tags[idx][2] = format(seed + 1, "X")
        return format(seed, "X")

    def add_entity_before(self, anchor: Entity, tags: list):
        """Wstawia encję (lista (kod, wartość)) przed podaną encją."""
        out = []
        for code, val in tags:
            raw = f"{code:>3}"
            out.append([code, raw, fmt_float(val) if isinstance(val, float) else str(val)])
        self.inserts.setdefault(anchor.start, []).extend(out)

    def delete_entity(self, ent: Entity) -> list[str]:
        """Usuwa encję oraz odwołania do niej (SORTENTSTABLE, reaktory).

        Zwraca listę ostrzeżeń o nieobsłużonych odwołaniach.
        """
        warnings = []
        handles = {ent.handle.upper()} if ent.handle else set()
        to_delete = [ent]
        # obiekty posiadane "na twardo" (np. słownik rozszerzeń)
        for _, code, val in ent.items():
            if code == 360:
                child = self.by_handle.get(val.strip().upper())
                if child is not None:
                    to_delete.append(child)
                    handles.add(child.handle.upper())
        for e in to_delete:
            for i in range(e.start, e.end):
                self.tags[i] = None
        if not handles:
            return warnings
        # usuń odwołania
        for other in self.entities:
            if self.tags[other.start] is None:
                continue
            in_reactors = False
            i = other.start + 1
            while i < other.end:
                t = self.tags[i]
                if t is None:
                    i += 1
                    continue
                code, _, val = t
                if code == 102:
                    in_reactors = val.strip().startswith("{ACAD_REACTORS")
                    if val.strip() == "}":
                        in_reactors = False
                elif code in _POINTER_CODES and val.strip().upper() in handles:
                    if other.type == "SORTENTSTABLE" and code == 331:
                        self.tags[i] = None
                        if i + 1 < other.end and self.tags[i + 1] and self.tags[i + 1][0] == 5:
                            self.tags[i + 1] = None
                            i += 1
                    elif in_reactors and code == 330:
                        self.tags[i] = None
                    else:
                        warnings.append(f"{other.type} {other.handle}: odwołanie {code}={val.strip()}")
                i += 1
        return warnings

    # --- zapis -----------------------------------------------------------------
    def to_bytes(self) -> bytes:
        out = []
        for i, t in enumerate(self.tags):
            for ins in self.inserts.get(i, ()):
                out.append(ins[1])
                out.append(ins[2])
            if t is not None:
                out.append(t[1])
                out.append(t[2])
        text = self.newline.join(out) + self.newline
        return text.encode(self.encoding, errors="surrogateescape")

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())


# --- pomocnicze: tekst MTEXT ------------------------------------------------
def mtext_plain(s: str) -> str:
    """Zwraca tekst MTEXT bez kodów formatowania (\\P -> spacja)."""
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = re.sub(r"\\[A-OQ-Za-z][^;\\{}]*;", "", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def mtext_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


# ============================================================================
# ZAPIS XLSX
# ============================================================================
def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _xml_text(v) -> str:
    # usuń znaki niedozwolone w XML 1.0
    s = "".join(ch for ch in str(v) if ch in "\t\n\r" or ord(ch) >= 0x20)
    return escape(s)


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="2"><numFmt numFmtId="164" formatCode="0.00"/><numFmt numFmtId="165" formatCode="0.000000"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment wrapText="1" vertical="center"/></xf>
<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="165" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
<xf numFmtId="1" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

# formaty kolumn: None/"text", "int", "2", "6"
_FMT_STYLE = {"int": 4, "2": 2, "6": 3}


def write_xlsx(path, sheet_name: str, columns, rows):
    """columns: lista (nagłówek, szerokość, format); rows: lista list wartości."""
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
           '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
           'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>', '<cols>']
    for i, (_, width, _) in enumerate(columns, start=1):
        out.append(f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>')
    out.append('</cols><sheetData>')
    out.append('<row r="1" ht="30" customHeight="1">' + "".join(
        f'<c r="{_col(i)}1" t="inlineStr" s="1"><is><t>{_xml_text(h)}</t></is></c>'
        for i, (h, _, _) in enumerate(columns)) + '</row>')
    for r, row in enumerate(rows, start=2):
        cells = []
        for i, v in enumerate(row):
            ref = f"{_col(i)}{r}"
            fmt = columns[i][2] if i < len(columns) else None
            if v is None or v == "":
                cells.append(f'<c r="{ref}" s="0"/>')
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(f'<c r="{ref}" s="{_FMT_STYLE.get(fmt, 0)}"><v>{v!r}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr" s="0"><is><t xml:space="preserve">'
                             f'{_xml_text(v)}</t></is></c>')
        out.append(f'<row r="{r}">' + "".join(cells) + '</row>')
    last = f"{_col(len(columns) - 1)}{max(1, len(rows) + 1)}"
    out.append(f'</sheetData><autoFilter ref="A1:{last}"/>'
               '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
               '<pageSetup orientation="landscape" paperSize="9"/></worksheet>')

    name = _xml_text(sheet_name[:31])
    files = {
        "[Content_Types].xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            '</Types>',
        "_rels/.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        "xl/workbook.xml":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>'
            '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
            f"'{name}'!$A$1:${_col(len(columns) - 1)}${max(1, len(rows) + 1)}</definedName></definedNames>"
            '</workbook>',
        "xl/_rels/workbook.xml.rels":
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>',
        "xl/styles.xml": _STYLES,
        "xl/worksheets/sheet1.xml": "".join(out),
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for fn, content in files.items():
            z.writestr(fn, content)


# ============================================================================
# OPENSTREETMAP: NOMINATIM I KAFLE
# ============================================================================
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


# ============================================================================
# GENERATOR ARKUSZY
# ============================================================================
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
            self.geocoder = Geocoder(opt.cache_dir, opt.user_agent or DEFAULT_USER_AGENT)
        if opt.basemap:
            try:
                import PIL  # noqa: F401
            except ImportError:
                raise ValueError("Do pobierania podkładu OSM potrzebna jest biblioteka Pillow: "
                                 "pip install pillow  (albo uruchom z opcją --bez-podkladu)") from None
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


# ============================================================================
# WIERSZ POLECEŃ
# ============================================================================
def parse_args(argv=None):
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--szablon", type=Path, default=here / "szablon.dxf", help="plik szablonu DXF")
    p.add_argument("--wyniki", type=Path, default=here / "wyniki", help="katalog wynikowy")
    p.add_argument("--excel", type=Path, help="plik zestawienia (domyślnie <wyniki>/zestawienie_stacji.xlsx)")
    p.add_argument("--cache", type=Path, default=here / ".cache", help="katalog cache (kafle, geokodowanie)")
    p.add_argument("--stacje", help="lista numerów stacji do wygenerowania, np. 541,664,1233")

    g = p.add_argument_group("arkusz")
    g.add_argument("--skala", type=int, default=10000, help="skala arkusza 1:N (domyślnie 10000)")
    g.add_argument("--stala-skala", action="store_true",
                   help="nie zwiększaj skali, gdy zasięg stacji się nie mieści")
    g.add_argument("--zostaw-inne-stacje", action="store_true",
                   help="nie usuwaj odnośników i obrysów pozostałych stacji")

    g = p.add_argument_group("nazwy miejscowości")
    g.add_argument("--bez-geokodowania", action="store_true", help="nie pytaj OSM Nominatim o nazwy")
    g.add_argument("--nazwy", type=Path, help="CSV 'numer;miejscowość' - nadpisuje nazwy z OSM")

    g = p.add_argument_group("podkład mapowy OpenStreetMap")
    g.add_argument("--bez-podkladu", action="store_true",
                   help="nie pobieraj podkładu OSM (zostaje obraz z szablonu)")
    g.add_argument("--zoom", type=int, default=16, help="poziom kafli OSM (domyślnie 16, max 19)")
    g.add_argument("--rozdzielczosc", type=float, default=1.0, help="rozmiar piksela podkładu w m (domyślnie 1.0)")
    g.add_argument("--margines-podkladu", type=float, default=0.15,
                   help="zapas podkładu poza rzutnią, ułamek wymiaru (domyślnie 0.15)")
    g.add_argument("--serwer-kafli", help="URL kafli {z}/{x}/{y} (domyślnie tile.openstreetmap.org)")
    g.add_argument("--user-agent", help="User-Agent dla serwerów OSM (najlepiej z adresem e-mail)")

    g = p.add_argument_group("budowa szablonu")
    g.add_argument("--warstwa-obrysow", default="!trafo", help="warstwa obrysów zasięgu stacji")
    g.add_argument("--blok-tabelki", default="tabelka", help="nazwa bloku tabelki")
    g.add_argument("--znacznik-stacji", help="tekst w tabelce zamieniany na numer stacji (domyślnie wykrywany, np. 03-xx)")
    g.add_argument("--fraza-miejscowosci", default="w miejscowości",
                   help="fraza w tabelce, po której stoi nazwa miejscowości")
    g.add_argument("--nazwa-pliku", default="{nr}.dxf",
                   help="wzorzec nazwy pliku: {nr} {numer} {oznaczenie} {miejscowosc} (domyślnie {nr}.dxf)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):        # konsola Windows / przekierowanie do pliku
        try:
            stream.reconfigure(errors="replace")
        except AttributeError:
            pass
    a = parse_args(argv)
    only = None
    if a.stacje:
        only = set()
        for tok in a.stacje.replace(";", ",").split(","):
            tok = tok.strip().split("-")[-1]
            if tok:
                only.add(int(tok))
    opt = Options(
        template=a.szablon, out_dir=a.wyniki, excel=a.excel or a.wyniki / "zestawienie_stacji.xlsx",
        cache_dir=a.cache, scale=a.skala, fixed_scale=a.stala_skala, basemap=not a.bez_podkladu,
        zoom=a.zoom, resolution=a.rozdzielczosc, basemap_margin=a.margines_podkladu,
        tile_url=a.serwer_kafli, user_agent=a.user_agent, geocode=not a.bez_geokodowania,
        names_csv=a.nazwy, only=only, keep_other_stations=a.zostaw_inne_stacje,
        zone_layer=a.warstwa_obrysow, table_block=a.blok_tabelki,
        place_phrase=a.fraza_miejscowosci, station_placeholder=a.znacznik_stacji,
        file_pattern=a.nazwa_pliku,
    )
    try:
        Generator(opt).run()
    except (ValueError, OSError) as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
