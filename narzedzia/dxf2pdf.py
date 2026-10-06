"""Wariant bez CAD-a: kazdy plik DXF w folderze -> jeden PDF.

Kazdy arkusz (paperspace, w kolejnosci zakladek) = jedna strona PDF.
Uzycie:  py dxf2pdf.py                      - okno z wyborem folderow
         py dxf2pdf.py <folder_dxf> [<folder_pdf>]   - bez okna (domyslnie PDF obok DXF)
Wymaga:  pip install ezdxf pymupdf
"""
import os
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


def dxf_do_pdf(dxf: pathlib.Path, pdf: pathlib.Path) -> int:
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
    wynik.save(pdf, garbage=3, deflate=True, deflate_images=True, deflate_fonts=True)
    return ile


def konwertuj_folder(wejscie: pathlib.Path, wyjscie: pathlib.Path, log=print) -> tuple[int, int]:
    """Kazdy DXF z folderu `wejscie` -> PDF w folderze `wyjscie`. Zwraca (udane, bledy)."""
    pliki = sorted(wejscie.glob("*.dxf"))
    if not pliki:
        log(f"Brak plikow DXF w: {wejscie}")
        return 0, 0
    wyjscie.mkdir(parents=True, exist_ok=True)
    ok = bledy = 0
    for i, dxf in enumerate(pliki, 1):
        pdf = wyjscie / (dxf.stem + ".pdf")
        try:
            ark = dxf_do_pdf(dxf, pdf)
            log(f"[{i}/{len(pliki)}] OK   {pdf.name}  ({ark} ark., {pdf.stat().st_size / 1e6:.1f} MB)")
            ok += 1
        except Exception as exc:  # jeden zly plik nie zatrzymuje reszty
            log(f"[{i}/{len(pliki)}] BLAD {dxf.name}: {exc}")
            bledy += 1
    log(f"Gotowe: {ok} PDF, bledy: {bledy}")
    return ok, bledy


# ---------------------------------------------------------------- okno

USTAWIENIA = pathlib.Path(os.environ.get("APPDATA") or pathlib.Path.home()) / "dxf2pdf.json"


def okno() -> None:
    import json
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext

    try:
        zapisane = json.loads(USTAWIENIA.read_text(encoding="utf-8"))
    except Exception:
        zapisane = {}

    root = tk.Tk()
    root.title("DXF -> PDF")
    root.minsize(640, 400)
    var_wej = tk.StringVar(value=zapisane.get("wejscie", ""))
    var_wyj = tk.StringVar(value=zapisane.get("wyjscie", ""))
    kolejka: queue.Queue = queue.Queue()

    def wybierz(var: tk.StringVar, tytul: str) -> None:
        folder = filedialog.askdirectory(title=tytul, initialdir=var.get() or var_wej.get() or None)
        if folder:
            var.set(str(pathlib.Path(folder)))
            if var is var_wej and not var_wyj.get():
                var_wyj.set(var.get())

    ramka = tk.Frame(root, padx=10, pady=10)
    ramka.pack(fill="both", expand=True)
    ramka.columnconfigure(1, weight=1)
    for wiersz, (opis, var, tytul) in enumerate(
        [
            ("Folder z plikami DXF:", var_wej, "Wybierz folder z plikami DXF"),
            ("Folder na pliki PDF:", var_wyj, "Wybierz folder na pliki PDF"),
        ]
    ):
        tk.Label(ramka, text=opis).grid(row=wiersz, column=0, sticky="w", pady=3)
        tk.Entry(ramka, textvariable=var).grid(row=wiersz, column=1, sticky="ew", padx=5)
        tk.Button(ramka, text="Wybierz...", command=lambda v=var, t=tytul: wybierz(v, t)).grid(row=wiersz, column=2)

    przyciski = tk.Frame(ramka)
    przyciski.grid(row=2, column=0, columnspan=3, sticky="w", pady=8)
    btn_start = tk.Button(przyciski, text="Konwertuj", width=14)
    btn_start.pack(side="left")
    btn_otworz = tk.Button(przyciski, text="Otworz folder PDF", state="disabled",
                           command=lambda: os.startfile(var_wyj.get()))
    btn_otworz.pack(side="left", padx=8)

    dziennik = scrolledtext.ScrolledText(ramka, height=15, state="disabled")
    dziennik.grid(row=3, column=0, columnspan=3, sticky="nsew")
    ramka.rowconfigure(3, weight=1)

    def dopisz(tekst: str) -> None:
        dziennik.configure(state="normal")
        dziennik.insert("end", tekst + "\n")
        dziennik.see("end")
        dziennik.configure(state="disabled")

    def odbieraj() -> None:
        while not kolejka.empty():
            wpis = kolejka.get()
            if wpis is None:  # koniec pracy
                btn_start.configure(state="normal")
                btn_otworz.configure(state="normal")
            else:
                dopisz(wpis)
        root.after(100, odbieraj)

    def start() -> None:
        wej = pathlib.Path(var_wej.get().strip())
        wyj = pathlib.Path(var_wyj.get().strip() or var_wej.get().strip())
        if not var_wej.get().strip() or not wej.is_dir():
            messagebox.showerror("DXF -> PDF", "Wybierz istniejacy folder z plikami DXF.")
            return
        var_wyj.set(str(wyj))
        try:
            USTAWIENIA.write_text(json.dumps({"wejscie": str(wej), "wyjscie": str(wyj)}), encoding="utf-8")
        except OSError:
            pass
        btn_start.configure(state="disabled")
        dopisz(f"--- {wej}  ->  {wyj}")

        def praca() -> None:
            try:
                konwertuj_folder(wej, wyj, log=kolejka.put)
            except Exception as exc:
                kolejka.put(f"BLAD: {exc}")
            kolejka.put(None)

        threading.Thread(target=praca, daemon=True).start()

    btn_start.configure(command=start)
    root.after(100, odbieraj)
    root.mainloop()


def main() -> None:
    if len(sys.argv) == 1:
        okno()
        return
    # tryb wiersza polecen: dxf2pdf <folder_dxf> [<folder_pdf>]
    wejscie = pathlib.Path(sys.argv[1])
    wyjscie = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else wejscie
    log = print if sys.stdout else (lambda *_: None)  # exe okienkowy nie ma konsoli
    _, bledy = konwertuj_folder(wejscie, wyjscie, log=log)
    sys.exit(1 if bledy else 0)


if __name__ == "__main__":
    main()
