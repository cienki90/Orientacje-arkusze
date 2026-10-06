# Orientacje – generator planów orientacyjnych stacji trafo

Program bierze `szablon.dxf` (wszystkie stacje na jednym rysunku) i dla każdej
stacji trafo tworzy osobny plik DXF – tak jak ręcznie przygotowane `541.dxf`,
`664.dxf` itd. Do tego robi zestawienie w Excelu.

Dla każdej stacji program:

1. **Wykrywa stację** – odnośnik `STACJA TRAFO\P03-0541` (grot strzałki to
   położenie stacji) i jej obrys zasięgu (zamknięta polilinia na warstwie `!trafo`).
2. **Ustala miejscowość** na podstawie współrzędnych stacji (PL-2000, strefa
   wykrywana automatycznie) i **sprawdza ją w kilku źródłach** – patrz niżej.
3. **Uzupełnia tabelkę**: `03-xx` → `03-0541`, a w opisie `…w miejscowości Cisie` → `…w miejscowości Halinów`.
4. **Ustawia rzutnię** na obrys stacji w skali 1:10 000. Jeśli zasięg się nie
   mieści, program przechodzi na kolejną skalę standardową i zmienia napis skali.
5. **Usuwa odnośniki i obrysy pozostałych stacji** razem z odwołaniami
   w SORTENTSTABLE i reaktorach, żeby w pliku nie zostały wiszące uchwyty.
6. **Pobiera podkład mapowy** (najpierw OpenStreetMap, a gdy nie działa – kolejne
   źródła), przelicza go do PL-2000 i zapisuje jako `541_podklad.png` + `.pgw`,
   potem podpina ten obraz w miejsce `orientacja.png`. Na arkuszu dopisuje
   atrybucję właściwą dla użytego źródła.
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

Kafle i odpowiedzi serwisów są zapisywane w cache – domyślnie poza folderem projektu
(Windows: `%LOCALAPPDATA%\Orientacje-arkusze\cache`), żeby OneDrive nie synchronizował
ani nie blokował tysięcy kafli. Inny katalog: `--cache`. Gdy zapis cache się nie uda,
program pobiera podkład dalej, tylko bez zapisywania kafli.

### Najważniejsze opcje

| Opcja | Opis |
|---|---|
| `--stacje 541,664` | tylko wybrane stacje |
| `--nazwy nazwy.csv` | ręczne nazwy miejscowości (`numer;miejscowość`), mają pierwszeństwo przed OSM – patrz `nazwy_przyklad.csv` |
| `--skala 10000` / `--stala-skala` | skala arkusza / bez automatycznego zwiększania skali |
| `--zoom 16` / `--rozdzielczosc 1.0` | szczegółowość kafli OSM / rozmiar piksela podkładu w metrach |
| `--bez-podkladu` | bez pobierania mapy (zostaje `orientacja.png` z szablonu, ścieżka jest poprawiana) |
| `--bez-geokodowania` | bez pytania serwisów o nazwy (wtedy potrzebny jest `--nazwy`) |
| `--zrodla-nazw` / `--zrodla-podkladu` | kolejność źródeł (patrz niżej) |
| `--user-agent "Firma, email@domena.pl"` | identyfikacja wymagana przez zasady OSM |
| `--serwer-kafli URL` | inny serwer kafli `{z}/{x}/{y}` |
| `--zostaw-inne-stacje` | nie usuwaj odnośników i obrysów sąsiednich stacji |
| `--nazwa-pliku "{nr}_{miejscowosc}.dxf"` | wzorzec nazwy pliku |

Pełna lista: `python generuj_arkusze.py --help`.

## Nazwy miejscowości – źródła i weryfikacja

| Klucz | Źródło | Co zwraca |
|---|---|---|
| `nominatim` | OSM Nominatim | miejscowość z adresu punktu |
| `overpass` | OSM Overpass | najbliższa miejscowość (`place=*`, do 3 km) + gmina z granic |
| `photon` | Photon (komoot) | niezależny geokoder na danych OSM |
| `uldk` | GUGiK ULDK | obręb ewidencyjny działki pod punktem (dane urzędowe) |

Nazwa jest brana z pierwszego źródła, które ją zwróci (kolejność: `--zrodla-nazw`).
Domyślnie program pyta też pozostałe źródła i porównuje wyniki. W Excelu
pojawia się kolumna **Weryfikacja nazwy** (`zgodne (4/4)` albo `ROZBIEŻNOŚĆ (2/4)`)
oraz **Nazwy ze wszystkich źródeł**. Przy rozbieżności w uwagach jest „SPRAWDŹ nazwę…”.
Źródło, które nie odpowiada, jest pomijane przy kolejnych stacjach.

* `--nazwa-wg-wiekszosci` – przy rozbieżności bierze nazwę podaną przez większość źródeł,
* `--bez-weryfikacji-nazw` – pyta tylko do pierwszej udanej odpowiedzi (szybciej),
* plik `--nazwy` zawsze ma pierwszeństwo; gdy różni się od źródeł, jest to odnotowane w uwagach.

## Podkład mapowy – źródła zapasowe

Źródła są próbowane po kolei (`--zrodla-podkladu`, lista: `--lista-zrodel`):
`osm` → `osm-de` → `osm-fr` → `carto` → `opentopomap` → `esri` → `geoportal-orto`
(ortofotomapa GUGiK z WMS, od razu w PL-2000). Jeden arkusz zawsze pochodzi
z jednego źródła. Źródło, które zawiedzie (błąd sieci, odmowa, odpowiedź
niebędąca obrazem), jest pomijane przy kolejnych arkuszach. Użyte źródło
widać w kolumnie **Źródło podkładu**. Gdy nie działa żadne, w arkuszu zostaje
`orientacja.png` z szablonu. `--serwer-kafli URL` dodaje własny serwer na początek listy.

### Gdy podkład się nie pobiera

1. Sprawdź wersję – na początku program wypisuje `Generator arkuszy v1.2.2`
   i listę źródeł podkładu. Jeśli tego nie widać, uruchamiasz starą wersję pliku.
2. Uruchom diagnostykę: `python generuj_arkusze.py --test-zrodel`. Program pobierze po jednym
   kaflu z każdego źródła i wypisze `[OK]` / `[BŁĄD]` z przyczyną (także dla źródeł nazw i Pillow).
3. W sieci firmowej z inspekcją SSL spróbuj `--bez-weryfikacji-ssl`.
4. Na końcu program wypisuje blok `PODKŁAD MAPOWY: N/M arkuszy z nowym podkładem` – ile
   fragmentów pobrano z internetu, ile wzięto z cache, które źródła wyłączono
   i dlaczego każdy arkusz bez podkładu go nie dostał. `--odswiez-podklad` ignoruje cache.

Źródło jest wyłączane do końca przebiegu tylko przy odmowie serwera (HTTP 403/404…),
odpowiedzi niebędącej obrazem albo gdy wszystkie kafle są identyczne (kafel „Access blocked”).
Chwilowy błąd (np. przekroczony czas) daje drugą próbę jeszcze w tym samym arkuszu
i przy następnym. Błędy przetwarzania obrazu trafiają do `wyniki/bledy_podkladu.log`.

## Kolumny zestawienia

Lp., oznaczenie stacji, nr, miejscowość, gmina, powiat, województwo, źródło
nazwy, weryfikacja nazwy, nazwy ze wszystkich źródeł, X/Y PL-2000 (konwencja geodezyjna: X = północ), szerokość/długość
geograficzna, informacja o obrysie, skala, plik DXF, plik podkładu, źródło podkładu, uwagi.

## Na co uważać

* **Sprawdź nazwy miejscowości** – szczególnie wiersze z `ROZBIEŻNOŚĆ`. Na granicy
  wsi źródła mogą podać sąsiednią wieś, przysiółek albo nazwę obrębu. Wtedy
  wpisz poprawną nazwę do pliku `--nazwy`. Gdy nazwy nie da się ustalić,
  w tabelce pojawia się `????`, a w uwagach w Excelu jest ostrzeżenie.
* Rzutnia jest środkowana na obrysie stacji. Jeśli chcesz inny kadr, przesuń
  ją ręcznie po wygenerowaniu.
* Zasady OSM: Nominatim pozwala na najwyżej 1 zapytanie/s (program tego
  pilnuje), a serwer kafli tylko na rozsądne użycie. Przy dużej liczbie stacji
  rozważ własny serwer kafli (`--serwer-kafli`).
