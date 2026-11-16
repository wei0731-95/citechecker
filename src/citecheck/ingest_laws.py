"""Ingest laws from the MOJ open-data XML (FalV.xml). Repealed laws are kept (SPEC_V2 A11)."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .db import connect, set_meta

_ART_RE = re.compile(r"^第\s*(\d+(?:-\d+)?)\s*條$")
_PCODE_RE = re.compile(r"pcode=([A-Za-z0-9]+)")
_NOTE_ART_RE = re.compile(r"第([0-9\-、，,～~至及]+)條")


def _article_key(label: str) -> str:
    """'第 15-1 條' -> '15-1'; non-standard labels (e.g. '1') are kept as-is."""
    label = (label or "").strip()
    m = _ART_RE.match(label)
    return m.group(1) if m else label


def articles_in_note(note: str) -> set[str]:
    """A10: article numbers named in a law's 生效內容 (promulgated but not yet in force)."""
    text = re.sub(r"\s+", "", note or "")
    out: set[str] = set()
    for m in _NOTE_ART_RE.finditer(text):
        for piece in re.split(r"[、，,及]", m.group(1)):
            if not piece:
                continue
            rng = re.split(r"[～~至]", piece)
            if len(rng) >= 2:
                a, b = rng[0], rng[-1]
                if a.isdigit() and b.isdigit() and int(a) <= int(b):
                    out.update(str(i) for i in range(int(a), int(b) + 1))
                else:
                    out.update(x for x in (a, b) if x)
            else:
                out.add(piece)
    return out


def find_xml(path: Path) -> Path:
    if path.is_file():
        return path
    hits = sorted(path.rglob("*.xml"))
    if not hits:
        raise FileNotFoundError(f"no .xml under {path}")
    return hits[0]


def ingest_laws(src: Path, db_path=None) -> dict:
    xml_path = find_xml(Path(src))
    root = ET.parse(xml_path).getroot()
    con = connect(db_path) if db_path else connect()
    n_laws = n_repealed = n_arts = 0
    with con:
        con.execute("DELETE FROM articles")
        con.execute("DELETE FROM laws")
        for law in root.findall("法規"):
            repealed = 1 if (law.findtext("廢止註記") or "").strip() else 0
            name = (law.findtext("法規名稱") or "").strip()
            if not name:
                continue
            url = (law.findtext("法規網址") or "").strip() or None
            m = _PCODE_RE.search(url or "")
            con.execute(
                "INSERT OR REPLACE INTO laws(law_name, modified_date, url, pcode, repealed, "
                "effective_date, effective_note) VALUES(?,?,?,?,?,?,?)",
                (name, (law.findtext("最新異動日期") or "").strip(), url, m.group(1) if m else None,
                 repealed, (law.findtext("生效日期") or "").strip(), law.findtext("生效內容") or ""))
            if repealed:
                n_repealed += 1
            else:
                n_laws += 1
            for a in law.iter("條文"):
                key = _article_key(a.findtext("條號"))
                con.execute("INSERT OR REPLACE INTO articles VALUES(?,?,?)",
                            (name, key, (a.findtext("條文內容") or "").strip()))
                n_arts += 1
        set_meta(con, "laws_update_date", root.attrib.get("UpdateDate", ""))
    con.close()
    return {"laws": n_laws, "repealed": n_repealed, "articles": n_arts}
