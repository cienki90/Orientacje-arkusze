"""Scala strony wydrukowane przez PDFDXF (GstarCAD) w jeden PDF na kazdy plik DXF.

<folder>/_strony/1233__01.pdf, 1233__02.pdf ...  ->  <folder>/1233.pdf
Wymaga: pip install pypdf
"""
import pathlib
import shutil
import sys
import traceback


def main(folder: pathlib.Path) -> None:
    from pypdf import PdfWriter

    tmp = folder / "_strony"
    grupy: dict[str, list[pathlib.Path]] = {}
    for strona in sorted(tmp.glob("*__*.pdf")):
        grupy.setdefault(strona.stem.rsplit("__", 1)[0], []).append(strona)

    if not grupy:
        raise SystemExit(f"Brak wydrukowanych stron w {tmp}")

    for nazwa, strony in grupy.items():
        writer = PdfWriter()
        for strona in strony:
            writer.append(str(strona))
        cel = folder / f"{nazwa}.pdf"
        with open(cel, "wb") as fh:
            writer.write(fh)
        print(f"OK  {cel.name}  ({len(strony)} ark.)")

    shutil.rmtree(tmp)


if __name__ == "__main__":
    try:
        main(pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    except Exception:
        traceback.print_exc()
        input("\nBlad - nacisnij Enter...")
