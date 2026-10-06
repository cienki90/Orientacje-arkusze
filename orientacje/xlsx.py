"""Minimalny zapis pliku .xlsx (jeden arkusz) - tylko biblioteka standardowa."""
from __future__ import annotations

import zipfile
from xml.sax.saxutils import escape


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
