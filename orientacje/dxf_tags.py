"""Edycja plików DXF (ASCII) na poziomie par tagów.

Celowo nie przepisujemy całego rysunku przez zewnętrzną bibliotekę: zmieniamy
tylko potrzebne wartości, a resztę szablonu zostawiamy bajt w bajt
(obiekty proxy, style, tabelka, układy arkuszy itd.).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

# kody grupowe będące odwołaniami do uchwytów (handle)
_POINTER_CODES = set(range(320, 370)) | set(range(390, 400)) | {480, 481, 1005}

_CODEPAGES = {
    "ANSI_1250": "cp1250", "ANSI_1251": "cp1251", "ANSI_1252": "cp1252",
    "ANSI_874": "cp874", "ANSI_932": "cp932", "ANSI_936": "gbk",
}


def fmt_float(v: float) -> str:
    s = repr(float(v))
    return s if ("." in s or "e" in s or "n" in s) else s + ".0"


@dataclass
class Entity:
    doc: "DxfDocument"
    start: int          # indeks tagu (0, TYP)
    end: int            # indeks pierwszego tagu następnej encji (wyłącznie)
    type: str
    section: str | None
    block: str | None   # nazwa bloku (dla sekcji BLOCKS)
    handle: str | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    # --- odczyt -----------------------------------------------------------
    def tag_indices(self, code: int):
        tags = self.doc.tags
        return [i for i in range(self.start + 1, self.end)
                if tags[i] is not None and tags[i][0] == code]

    def get(self, code: int, n: int = 0, default=None):
        idx = self.tag_indices(code)
        return self.doc.tags[idx[n]][2] if len(idx) > n else default

    def values(self, code: int):
        return [self.doc.tags[i][2] for i in self.tag_indices(code)]

    def items(self):
        for i in range(self.start + 1, self.end):
            t = self.doc.tags[i]
            if t is not None:
                yield i, t[0], t[2]

    @property
    def layer(self):
        return (self.get(8) or "").strip()

    @property
    def paperspace(self) -> bool:
        return (self.get(67) or "0").strip() == "1"

    # --- zapis ------------------------------------------------------------
    def set(self, code: int, value, n: int = 0):
        idx = self.tag_indices(code)
        if len(idx) <= n:
            raise KeyError(f"{self.type} {self.handle}: brak tagu {code} (#{n})")
        self.doc.set_value(idx[n], value)


class DxfDocument:
    def __init__(self, data: bytes):
        self.newline = "\r\n" if b"\r\n" in data[:4096] else "\n"
        self.encoding = self._detect_encoding(data)
        text = data.decode(self.encoding, errors="surrogateescape")
        lines = text.split(self.newline)
        if lines and lines[-1] == "":
            lines.pop()
        if len(lines) % 2:
            raise ValueError("Uszkodzony DXF: nieparzysta liczba linii")
        # tag = [kod_int, kod_surowy, wartosc] ; None = usunięty
        self.tags: list = [[int(lines[i].strip()), lines[i], lines[i + 1]]
                           for i in range(0, len(lines), 2)]
        self.inserts: dict[int, list] = {}   # indeks -> lista tagów do wstawienia PRZED nim
        self._index()

    @classmethod
    def load(cls, path) -> "DxfDocument":
        with open(path, "rb") as f:
            return cls(f.read())

    def copy(self) -> "DxfDocument":
        new = object.__new__(DxfDocument)
        new.newline, new.encoding = self.newline, self.encoding
        new.tags = [None if t is None else list(t) for t in self.tags]
        new.inserts = copy.deepcopy(self.inserts)
        new._index()
        return new

    @staticmethod
    def _detect_encoding(data: bytes) -> str:
        head = data[:20000].decode("latin1")
        ver = re.search(r"\$ACADVER\s*\r?\n\s*1\s*\r?\n(\w+)", head)
        if ver and ver.group(1) >= "AC1021":       # AutoCAD 2007+ => UTF-8
            return "utf-8"
        cp = re.search(r"\$DWGCODEPAGE\s*\r?\n\s*3\s*\r?\n(\w+)", head)
        return _CODEPAGES.get(cp.group(1).upper(), "cp1252") if cp else "cp1252"

    # --- indeks encji -------------------------------------------------------
    def _index(self):
        self.entities: list[Entity] = []
        self.by_handle: dict[str, Entity] = {}
        sec = blk = None
        expect_sec = False
        cur = None
        named = False
        tags = self.tags
        n = len(tags)

        def close(end):
            if cur is not None:
                cur.end = end
                self.entities.append(cur)
                if cur.handle:
                    self.by_handle[cur.handle.upper()] = cur

        for i in range(n):
            t = tags[i]
            if t is None:
                continue
            code, _, val = t
            if code == 0:
                close(i)
                cur = None
                if val == "SECTION":
                    expect_sec = True
                    continue
                if val == "ENDSEC":
                    sec = None
                    continue
                if val == "EOF":
                    continue
                cur = Entity(self, i, n, val, sec, blk)
                named = False
            else:
                if expect_sec and code == 2:
                    sec, expect_sec = val, False
                    continue
                if cur is not None:
                    if code == 5 or (code == 105 and cur.type == "DIMSTYLE"):
                        if cur.handle is None:
                            cur.handle = val.strip()
                    if cur.type == "BLOCK" and code == 2 and not named:
                        named = True
                        blk = val
                        cur.block = val
        close(n)

    def query(self, type_=None, section=None, block=None, layer=None):
        for e in self.entities:
            if type_ and e.type not in (type_ if isinstance(type_, (tuple, list, set)) else (type_,)):
                continue
            if section and e.section != section:
                continue
            if block is not None and e.block != block:
                continue
            if layer is not None and e.layer != layer:
                continue
            if self.tags[e.start] is None:
                continue
            yield e

    # --- modyfikacje ----------------------------------------------------------
    def set_value(self, idx: int, value):
        if isinstance(value, float):
            value = fmt_float(value)
        self.tags[idx][2] = str(value)

    def header_var(self, name: str):
        for i, t in enumerate(self.tags):
            if t and t[0] == 9 and t[2].strip() == name:
                return i + 1
        return None

    def new_handle(self) -> str:
        idx = self.header_var("$HANDSEED")
        seed = int(self.tags[idx][2].strip(), 16)
        self.tags[idx][2] = format(seed + 1, "X")
        return format(seed, "X")

    def add_entity_before(self, anchor: Entity, tags: list):
        """Wstawia encję (lista (kod, wartość)) przed podaną encją."""
        out = []
        for code, val in tags:
            raw = f"{code:>3}"
            out.append([code, raw, fmt_float(val) if isinstance(val, float) else str(val)])
        self.inserts.setdefault(anchor.start, []).extend(out)

    def delete_entity(self, ent: Entity) -> list[str]:
        """Usuwa encję oraz odwołania do niej (SORTENTSTABLE, reaktory).

        Zwraca listę ostrzeżeń o nieobsłużonych odwołaniach.
        """
        warnings = []
        handles = {ent.handle.upper()} if ent.handle else set()
        to_delete = [ent]
        # obiekty posiadane "na twardo" (np. słownik rozszerzeń)
        for _, code, val in ent.items():
            if code == 360:
                child = self.by_handle.get(val.strip().upper())
                if child is not None:
                    to_delete.append(child)
                    handles.add(child.handle.upper())
        for e in to_delete:
            for i in range(e.start, e.end):
                self.tags[i] = None
        if not handles:
            return warnings
        # usuń odwołania
        for other in self.entities:
            if self.tags[other.start] is None:
                continue
            in_reactors = False
            i = other.start + 1
            while i < other.end:
                t = self.tags[i]
                if t is None:
                    i += 1
                    continue
                code, _, val = t
                if code == 102:
                    in_reactors = val.strip().startswith("{ACAD_REACTORS")
                    if val.strip() == "}":
                        in_reactors = False
                elif code in _POINTER_CODES and val.strip().upper() in handles:
                    if other.type == "SORTENTSTABLE" and code == 331:
                        self.tags[i] = None
                        if i + 1 < other.end and self.tags[i + 1] and self.tags[i + 1][0] == 5:
                            self.tags[i + 1] = None
                            i += 1
                    elif in_reactors and code == 330:
                        self.tags[i] = None
                    else:
                        warnings.append(f"{other.type} {other.handle}: odwołanie {code}={val.strip()}")
                i += 1
        return warnings

    # --- zapis -----------------------------------------------------------------
    def to_bytes(self) -> bytes:
        out = []
        for i, t in enumerate(self.tags):
            for ins in self.inserts.get(i, ()):
                out.append(ins[1])
                out.append(ins[2])
            if t is not None:
                out.append(t[1])
                out.append(t[2])
        text = self.newline.join(out) + self.newline
        return text.encode(self.encoding, errors="surrogateescape")

    def save(self, path):
        with open(path, "wb") as f:
            f.write(self.to_bytes())


# --- pomocnicze: tekst MTEXT ------------------------------------------------
def mtext_plain(s: str) -> str:
    """Zwraca tekst MTEXT bez kodów formatowania (\\P -> spacja)."""
    s = s.replace("\\P", " ").replace("\\~", " ")
    s = re.sub(r"\\[A-OQ-Za-z][^;\\{}]*;", "", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def mtext_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
