"""Ingest Supreme Court civil judgments (directory 最高法院民事, JID prefix TPSV)."""
import json
import re
from pathlib import Path

from .db import connect, set_meta

CIVIL_DIR = "最高法院民事"
CIVIL_PREFIX = "TPSV"
_KIND_RE = re.compile(r"(判決|裁定)")
_MONTH_RE = re.compile(r"^(\d{6})(--.*)?$")


def month_of(path: Path) -> str | None:
    """A3: first 6 digits of the nearest ancestor folder named YYYYMM or YYYYMM--..."""
    for part in reversed(path.parts[:-1]):
        m = _MONTH_RE.match(part)
        if m:
            return m.group(1)
    return None


def doc_kind(full_text: str) -> str | None:
    """First token of JFULL, e.g. '最高法院民事大法庭裁定' -> '裁定'."""
    toks = full_text.split(None, 1)
    if not toks:
        return None
    m = _KIND_RE.search(toks[0])
    return m.group(1) if m else None


def iter_files(root: Path):
    for p in sorted(root.rglob("*.json")):
        if p.parent.name == CIVIL_DIR:
            yield p


def ingest_judgments(src: Path, db_path=None) -> dict:
    con = connect(db_path) if db_path else connect()
    n = skipped = 0
    with con:
        for p in iter_files(Path(src)):
            j = json.loads(p.read_text(encoding="utf-8"))
            jid = j["JID"]
            if jid.split(",")[0] != CIVIL_PREFIX:
                skipped += 1
                continue
            con.execute(
                "INSERT OR REPLACE INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, "
                "full_text, pdf_url, month) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (jid, int(j["JYEAR"]), j["JCASE"], int(j["JNO"]), doc_kind(j["JFULL"]),
                 j["JDATE"], j["JTITLE"], j["JFULL"], (j.get("JPDF") or "").strip() or None,
                 month_of(p)))
            n += 1
        row = con.execute("SELECT MIN(year), MAX(year), MIN(date), MAX(date) FROM judgments").fetchone()
        for k, v in zip(("year_min", "year_max", "date_min", "date_max"), row):
            set_meta(con, k, "" if v is None else str(v))
        months = [r[0] for r in con.execute(
            "SELECT DISTINCT month FROM judgments WHERE month IS NOT NULL ORDER BY month")]
        set_meta(con, "months_imported", json.dumps(months))
    con.close()
    return {"judgments": n, "skipped_non_civil": skipped}
