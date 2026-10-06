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
import sys
from pathlib import Path

from orientacje.generator import Generator, Options


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
