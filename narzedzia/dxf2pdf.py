"""Wariant bez CAD-a: kazdy plik DXF w folderze -> jeden PDF obok niego.

Kazdy arkusz (paperspace, w kolejnosci zakladek) = jedna strona PDF.
Uzycie:  py dxf2pdf.py "C:\\sciezka\\do\\folderu"
Wymaga:  pip install ezdxf pymupdf
"""
import pathlib
import sys

import ezdxf
from ezdxf import recover
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, pymupdf

try:
    import pymupdf as fitz
except ImportError:  # starsze wersje PyMuPDF
    import fitz


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

    ile = wynik.page_count
    wynik.save(dxf.with_suffix(".pdf"))
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
