"""Wariant bez CAD-a: kazdy plik DXF w folderze -> jeden PDF obok niego.

Kazdy arkusz (paperspace, w kolejnosci zakladek) = jedna strona PDF.
Uzycie:  py dxf2pdf.py "C:\\sciezka\\do\\folderu"
Wymaga:  pip install ezdxf pymupdf
"""
import pathlib
import re
import sys

import ezdxf
from ezdxf import recover
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

try:
    import pymupdf as fitz
except ImportError:  # starsze wersje PyMuPDF
    import fitz

# Rozmiar PDF-a. ezdxf wstawia podklad jako surowe, nieskompresowane piksele
# (~20 MB), wiec po wygenerowaniu zamieniamy go na JPG - tak jak robi to CAD.
DPI_PODKLADU = 200  # rozdzielczosc obrazow w PDF; wiecej = ostrzej, ale wiekszy plik
JAKOSC_JPG = 75  # 1-100
MIEJSCA_PO_PRZECINKU = 2  # dokladnosc wspolrzednych wektorow w punktach (0.01 pt = 0.004 mm)


def pomin_niedrukowalne(doc) -> list[str]:
    """Ukrywa warstwy z wylaczonym drukowaniem (oraz Defpoints) - tak jak CAD przy wydruku.

    Zmiana dotyczy tylko pamieci, plik DXF nie jest zapisywany.
    """
    niedrukowalne = {
        layer.dxf.name.lower()
        for layer in doc.layers
        if layer.dxf.get("plot", 1) == 0 or layer.dxf.name.lower() == "defpoints"
    }
    if not niedrukowalne:
        return []

    # CAD drukuje zawartosc rzutni nawet, gdy sama rzutnia lezy na warstwie
    # niedrukowalnej - wtedy znika tylko jej ramka. Przenosimy takie rzutnie
    # na warstwe 0, zeby po wylaczeniu warstwy nie zniknela ich zawartosc.
    for psp in doc.layouts:
        if psp.name == "Model":
            continue
        for vp in psp.query("VIEWPORT"):
            if vp.dxf.layer.lower() in niedrukowalne:
                vp.dxf.layer = "0"

    for layer in doc.layers:
        if layer.dxf.name.lower() in niedrukowalne:
            layer.off()
    return sorted(niedrukowalne)


def obrazy_do_jpg(pdf) -> None:
    """Zapisuje obrazy jako JPG i zmniejsza je do DPI_PODKLADU.

    Maska przyciecia obrazu (SMask) zostaje bez zmian, przy zapisie jest tylko kompresowana.
    """
    gotowe = set()
    for strona in pdf:
        for img in strona.get_images(full=True):
            xref, bpc, filtr = img[0], img[4], img[8]
            if xref in gotowe or bpc == 1 or filtr in ("DCTDecode", "JPXDecode"):
                continue
            gotowe.add(xref)

            pix = fitz.Pixmap(pdf, xref)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            if pix.n not in (1, 3):
                pix = fitz.Pixmap(fitz.csRGB, pix)

            # zmniejszenie do DPI_PODKLADU wg najwiekszego wystapienia obrazu na stronie
            ramki = strona.get_image_rects(xref)
            if ramki:
                szer_pt = max(r.width for r in ramki)
                wys_pt = max(r.height for r in ramki)
                skala = max(szer_pt * DPI_PODKLADU / 72 / pix.width, wys_pt * DPI_PODKLADU / 72 / pix.height)
                if skala < 0.9:
                    pix = fitz.Pixmap(pix, round(pix.width * skala), round(pix.height * skala))

            try:
                jpg = pix.tobytes("jpg", jpg_quality=JAKOSC_JPG)
            except TypeError:  # starsze PyMuPDF - przez Pillow
                jpg = pix.pil_tobytes(format="JPEG", quality=JAKOSC_JPG)

            pdf.update_stream(xref, jpg, compress=False)
            pdf.xref_set_key(xref, "Filter", "/DCTDecode")
            pdf.xref_set_key(xref, "DecodeParms", "null")
            pdf.xref_set_key(xref, "Width", str(pix.width))
            pdf.xref_set_key(xref, "Height", str(pix.height))
            pdf.xref_set_key(xref, "BitsPerComponent", "8")
            pdf.xref_set_key(xref, "ColorSpace", "/DeviceGray" if pix.n == 1 else "/DeviceRGB")


_LICZBA = re.compile(rb"(?<![\w/.])-?\d*\.\d+")


def zaokraglij_wektory(pdf) -> None:
    """Skraca wspolrzedne w tresci stron (ezdxf pisze 5 miejsc po przecinku)."""

    def krotsza(m: re.Match) -> bytes:
        s = f"{float(m.group(0)):.{MIEJSCA_PO_PRZECINKU}f}".rstrip("0").rstrip(".")
        return (s if s not in ("", "-", "-0") else "0").encode()

    gotowe = set()
    for strona in pdf:
        for xref in strona.get_contents():
            if xref in gotowe:
                continue
            gotowe.add(xref)
            tresc = pdf.xref_stream(xref)
            if not tresc or re.search(rb"\bBT\b", tresc):  # teksty z napisami - nie ruszamy
                continue
            pdf.update_stream(xref, _LICZBA.sub(krotsza, tresc))


def dxf_do_pdf(dxf: pathlib.Path) -> int:
    doc, _ = recover.readfile(dxf)

    # obrazy (np. "..\\orientacja.png") szukamy po nazwie w folderze DXF i folderze wyzej
    for imgdef in doc.objects.query("IMAGEDEF"):
        nazwa = pathlib.PureWindowsPath(imgdef.dxf.filename).name
        for kandydat in (dxf.parent / nazwa, dxf.parent.parent / nazwa):
            if kandydat.exists():
                imgdef.dxf.filename = str(kandydat)
                break

    pomin_niedrukowalne(doc)

    cfg = config.Configuration(image_policy=config.ImagePolicy.DISPLAY)
    wynik = fitz.open()
    for nazwa in doc.layout_names_in_taborder():
        if nazwa == "Model":
            continue
        psp = doc.paperspace(nazwa)
        backend = pymupdf.PyMuPdfBackend()
        Frontend(RenderContext(doc), backend, config=cfg).draw_layout(psp)
        strona = layout.Page.from_dxf_layout(psp)
        wynik.insert_pdf(fitz.open("pdf", backend.get_pdf_bytes(strona)))

    obrazy_do_jpg(wynik)
    zaokraglij_wektory(wynik)

    ile = wynik.page_count
    wynik.save(dxf.with_suffix(".pdf"), garbage=3, deflate=True, deflate_images=True, deflate_fonts=True)
    return ile


def main() -> None:
    folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    for dxf in sorted(folder.glob("*.dxf")):
        try:
            print(f"OK   {dxf.with_suffix('.pdf').name}  ({dxf_do_pdf(dxf)} ark.)")
        except Exception as exc:  # jeden zly plik nie zatrzymuje reszty
            print(f"BLAD {dxf.name}: {exc}")


if __name__ == "__main__":
    main()
