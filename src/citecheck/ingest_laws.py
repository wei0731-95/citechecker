"""Ingest current (non-abolished) laws from the MOJ open-data XML (FalV.xml)."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .db import connect, set_meta

_ART_RE = re.compile(r"^第\s*(\d+(?:-\d+)?)\s*條$")


def _article_key(label: str) -> str:
    """'第 15-1 條' -> '15-1'; non-standard labels (e.g. '1') are kept as-is."""
    label = (label or "").strip()
    m = _ART_RE.match(label)
    return m.group(1) if m else label


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
    n_laws = n_arts = n_skipped = 0
    with con:
        con.execute("DELETE FROM articles")
        con.execute("DELETE FROM laws")
        for law in root.findall("法規"):
            if (law.findtext("廢止註記") or "").strip():
                n_skipped += 1
                continue
            name = (law.findtext("法規名稱") or "").strip()
            if not name:
                continue
            con.execute("INSERT OR REPLACE INTO laws VALUES(?,?)",
                        (name, (law.findtext("最新異動日期") or "").strip()))
            n_laws += 1
            for a in law.iter("條文"):
                key = _article_key(a.findtext("條號"))
                con.execute("INSERT OR REPLACE INTO articles VALUES(?,?,?)",
                            (name, key, (a.findtext("條文內容") or "").strip()))
                n_arts += 1
        set_meta(con, "laws_update_date", root.attrib.get("UpdateDate", ""))
    con.close()
    return {"laws": n_laws, "articles": n_arts, "abolished_skipped": n_skipped}
