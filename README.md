# Orientacje – generator planów orientacyjnych stacji trafo

Program bierze `szablon.dxf` (wszystkie stacje na jednym rysunku) i dla każdej
stacji trafo tworzy osobny plik DXF – tak jak ręcznie przygotowane `541.dxf`,
`664.dxf` itd. Do tego robi zestawienie w Excelu.

Dla każdej stacji program:

1. **Wykrywa stację** – odnośnik `STACJA TRAFO\P03-0541` (grot strzałki to
   położenie stacji) i jej obrys zasięgu (zamknięta polilinia na warstwie `!trafo`).
2. **Ustala miejscowość** z OpenStreetMap (Nominatim) na podstawie współrzędnych
   stacji (PL-2000, strefa wykrywana automatycznie).
3. **Uzupełnia tabelkę**: `03-xx` → `03-0541`, a w opisie `…w miejscowości Cisie` → `…w miejscowości Halinów`.
4. **Ustawia rzutnię** na obrys stacji w skali 1:10 000. Jeśli zasięg się nie
   mieści, program przechodzi na kolejną skalę standardową i zmienia napis skali.
5. **Usuwa odnośniki i obrysy pozostałych stacji** razem z odwołaniami
   w SORTENTSTABLE i reaktorach, żeby w pliku nie zostały wiszące uchwyty.
6. **Pobiera podkład OpenStreetMap**: skleja kafle, przelicza je do PL-2000
   i zapisuje jako `541_podklad.png` + `.pgw`, potem podpina ten obraz w miejsce
   `orientacja.png`. Na arkuszu dopisuje „© autorzy OpenStreetMap”.
7. Zapisuje `wyniki/541.dxf` i dopisuje wiersz do `wyniki/zestawienie_stacji.xlsx`.

Plik DXF jest edytowany na poziomie tagów, więc reszta szablonu (style,
tabelka, układy, obiekty proxy) zostaje bez zmian.

## Instalacja

Cały program to jeden plik `generuj_arkusze.py`. Wystarczy go położyć obok
`szablon.dxf` (i `orientacja.png`). Wymagany Python 3.10+. Jedyna zależność
to Pillow (do podkładu mapowego):

```
pip install -r requirements.txt
```

## Użycie

```
python generuj_arkusze.py
```

Pod Windows można też dwukrotnie kliknąć `generuj.bat`.

Wyniki trafiają do katalogu `wyniki/`:

```
wyniki/
  541.dxf, 664.dxf, ...           plany orientacyjne
  541_podklad.png / .pgw, ...     podkład OSM dla każdego arkusza (trzymaj obok DXF)
  zestawienie_stacji.xlsx         zestawienie stacji
```

Kafle i odpowiedzi Nominatim są zapisywane w `.cache/`, więc kolejne
uruchomienia działają szybko i nie obciążają serwerów OSM.

### Najważniejsze opcje

| Opcja | Opis |
|---|---|
| `--stacje 541,664` | tylko wybrane stacje |
| `--nazwy nazwy.csv` | ręczne nazwy miejscowości (`numer;miejscowość`), mają pierwszeństwo przed OSM – patrz `nazwy_przyklad.csv` |
| `--skala 10000` / `--stala-skala` | skala arkusza / bez automatycznego zwiększania skali |
| `--zoom 16` / `--rozdzielczosc 1.0` | szczegółowość kafli OSM / rozmiar piksela podkładu w metrach |
| `--bez-podkladu` | bez pobierania mapy (zostaje `orientacja.png` z szablonu, ścieżka jest poprawiana) |
| `--bez-geokodowania` | bez pytania OSM o nazwy (wtedy potrzebny jest `--nazwy`) |
| `--user-agent "Firma, email@domena.pl"` | identyfikacja wymagana przez zasady OSM |
| `--serwer-kafli URL` | inny serwer kafli `{z}/{x}/{y}` |
| `--zostaw-inne-stacje` | nie usuwaj odnośników i obrysów sąsiednich stacji |
| `--nazwa-pliku "{nr}_{miejscowosc}.dxf"` | wzorzec nazwy pliku |

Pełna lista: `python generuj_arkusze.py --help`.

## Kolumny zestawienia

Lp., oznaczenie stacji, nr, miejscowość, gmina, powiat, województwo, źródło
nazwy, X/Y PL-2000 (konwencja geodezyjna: X = północ), szerokość/długość
geograficzna, informacja o obrysie, skala, plik DXF, plik podkładu, uwagi.

## Na co uważać

* **Sprawdź nazwy miejscowości.** OSM podaje miejscowość, w której leży
  punkt stacji. Na granicy wsi może podać sąsiednią albo przysiółek. Wtedy
  wpisz poprawną nazwę do pliku `--nazwy`. Gdy nazwy nie da się ustalić,
  w tabelce pojawia się `????`, a w uwagach w Excelu jest ostrzeżenie.
* Rzutnia jest środkowana na obrysie stacji. Jeśli chcesz inny kadr, przesuń
  ją ręcznie po wygenerowaniu.
* Zasady OSM: Nominatim pozwala na najwyżej 1 zapytanie/s (program tego
  pilnuje), a serwer kafli tylko na rozsądne użycie. Przy dużej liczbie stacji
  rozważ własny serwer kafli (`--serwer-kafli`).
