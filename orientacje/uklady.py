"""Przeliczenia współrzędnych: PL-2000 (EPSG:2176-2179) <-> WGS84 <-> Web Mercator.

Odwzorowanie Gaussa-Krügera liczone szeregami Krügera (6. rząd, dokładność
sub-milimetrowa), elipsoida GRS80. Bez zależności zewnętrznych.

Konwencja w DXF: X rysunku = easting (np. 7 525 141), Y rysunku = northing.
"""
from __future__ import annotations

import math

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
