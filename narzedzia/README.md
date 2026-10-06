# DXF → PDF (jeden PDF na każdy plik DXF)

Każdy plik `*.dxf` w folderze zostaje zamieniony na `<nazwa>.pdf` w **tym samym folderze**.
Wszystkie arkusze trafiają do jednego pliku jako kolejne strony, w kolejności zakładek (`1`, `3.1`, `3.2`, `3.3`, `3.4`).

## Wariant A: GstarCAD (wydruk tak jak z CAD-a)

Jednorazowo:
1. Skopiuj `pdf_dxf.lsp` i `scal_pdf.py` do `C:\narzedzia\`. Jeśli wybierzesz inny folder, zmień `*pdfdxf-narzedzia*` w `pdf_dxf.lsp`.
2. W `pdf_dxf.lsp` wpisz w `*pdfdxf-drukarka*` dokładną nazwę drukarki PDF z okna **Drukuj** w GstarCAD.
3. Zainstaluj Pythona i wykonaj: `py -m pip install pypdf`.
4. W szablonie przez `PAGESETUP` ustaw każdemu arkuszowi tę drukarkę i papier A4. Domyślnie arkusze mają ustawienia z AutoCAD-a (`DWG To PDF.pc3`, `User4100`).

Każdy kolejny raz:
1. Otwórz nowy, pusty rysunek.
2. Wpisz `APPLOAD` i wczytaj `pdf_dxf.lsp`. Możesz go dodać do „Autoładowania”, żeby wczytywał się sam.
3. Wpisz `PDFDXF` i wskaż dowolny plik DXF w folderze.

GstarCAD otworzy każdy plik, wydrukuje arkusze do `_strony\` i zamknie go. Na końcu `scal_pdf.py` połączy strony w jeden plik `<nazwa>.pdf` i usunie `_strony\`.

## Wariant B: bez CAD-a (Python)

```
py -m pip install ezdxf pymupdf
py dxf2pdf.py "C:\sciezka\do\folderu\z\dxf"
```
Warstwy z wyłączonym drukowaniem oraz `Defpoints` nie trafiają do PDF-a, tak samo jak przy wydruku z CAD-a.
Ten wariant jest szybszy, ale odwzorowanie może być trochę słabsze niż w CAD-zie, np. fonty SHX i grubości linii z CTB.

## Uwaga: `orientacja.png`
Rysunki szukają podkładu pod ścieżką `..\orientacja.png`, czyli w folderze **wyżej** niż DXF.
W wariancie A plik PNG musi tam leżeć albo trzeba poprawić ścieżkę obrazu w szablonie.
Wariant B sam znajduje PNG obok pliku DXF albo folder wyżej.
