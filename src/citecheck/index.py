"""build-index: pre-segment every judgment into `segments` / `passages` (SPEC_V2 F1, C3).

No LLM is used here. Safe to interrupt and re-run (already-indexed judgments are skipped).
"""
import re
import sqlite3

from .models import SegmentRole
from .retrieve import Passage, split_passages
from .segment import SEGMENT_RULES_VERSION, Segment, segment

COMMIT_EVERY = 500
_DISMISSAL = re.compile(r"據上論結[，,]?本件[^。]{0,30}不合法")


class IndexNotReady(RuntimeError):
    MESSAGE = "請先執行 citecheck build-index"

    def __init__(self, detail: str = ""):
        super().__init__(self.MESSAGE + (f"（{detail}）" if detail else ""))


def is_procedural_dismissal(doc_kind: str | None, full_text: str) -> bool:
    """C3: a 裁定 whose conclusion says the appeal / petition is 不合法."""
    return doc_kind == "裁定" and bool(_DISMISSAL.search(re.sub(r"\s+", "", full_text)))


def _clear(con: sqlite3.Connection) -> None:
    for t in ("quoted_sentences", "judgment_prep", "passages", "segments"):
        con.execute(f"DELETE FROM {t}")


def indexed_version(con: sqlite3.Connection) -> int | None:
    row = con.execute("SELECT value FROM meta WHERE key='segment_rules_version'").fetchone()
    return int(row[0]) if row and row[0] else None


def build_index(con: sqlite3.Connection, rebuild: bool = False) -> dict:
    version = indexed_version(con)
    reset = rebuild or (version is not None and version != SEGMENT_RULES_VERSION)
    if reset:
        _clear(con)
        con.execute("UPDATE judgments SET procedural_dismissal=0")
        con.execute("DELETE FROM meta WHERE key='segment_rules_version'")
        con.commit()
    todo = con.execute(
        "SELECT jid, doc_kind, full_text FROM judgments "
        "WHERE jid NOT IN (SELECT DISTINCT jid FROM segments) ORDER BY jid").fetchall()
    done = 0
    for jid, doc_kind, full_text in todo:
        segs = segment(full_text)
        con.executemany(
            "INSERT OR REPLACE INTO segments(jid, idx, role, part, marker, text) VALUES(?,?,?,?,?,?)",
            [(jid, s.index, s.role.value, s.part, s.marker, s.text) for s in segs])
        con.executemany(
            "INSERT OR REPLACE INTO passages(jid, seq, segment_index, role, text) VALUES(?,?,?,?,?)",
            [(jid, seq, p.segment_index, p.role.value, p.text) for seq, p in enumerate(split_passages(segs))])
        con.execute("UPDATE judgments SET procedural_dismissal=? WHERE jid=?",
                    (int(is_procedural_dismissal(doc_kind, full_text)), jid))
        done += 1
        if done % COMMIT_EVERY == 0:
            con.commit()
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('segment_rules_version', ?)",
                (str(SEGMENT_RULES_VERSION),))
    con.commit()
    fts_rows = build_fts(con)
    return {"indexed_now": done, "already_indexed": con.execute(
        "SELECT COUNT(DISTINCT jid) FROM segments").fetchone()[0] - done, "rebuilt": reset,
        "fts_passages": fts_rows}


def build_fts(con: sqlite3.Connection) -> int:
    """SPEC_V2 G1.1: rebuild the whole trigram full-text table (本院 passages of non-dismissal 判決/裁定)."""
    con.execute("DROP TABLE IF EXISTS passages_fts")
    con.execute("CREATE VIRTUAL TABLE passages_fts USING fts5(text, jid UNINDEXED, seq UNINDEXED, "
                "tokenize='trigram')")
    con.execute(
        "INSERT INTO passages_fts(text, jid, seq) "
        "SELECT p.text, p.jid, p.seq FROM passages p JOIN judgments j ON j.jid = p.jid "
        "WHERE p.role='SUPREME_COURT' AND j.doc_kind IN ('判決','裁定') AND j.procedural_dismissal=0 "
        "ORDER BY p.jid, p.seq")
    con.commit()
    return con.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0]


def has_fts(con: sqlite3.Connection) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name='passages_fts'").fetchone() is not None


FTS_MAX_TERMS = 60


def fts_query(text: str) -> str:
    """SPEC_V2 G1.2: all 3-character windows of every run of CJK/alphanumeric characters, de-duplicated
    in first-seen order, first 60, each double-quoted, joined by OR. '' when there is nothing to search."""
    seen: dict[str, None] = {}
    for run in re.findall(r"[0-9A-Za-z㐀-䶿一-鿿豈-﫿]+", text or ""):
        for i in range(len(run) - 2):
            seen.setdefault(run[i:i + 3], None)
    return " OR ".join(f'"{t}"' for t in list(seen)[:FTS_MAX_TERMS])


def fts_search(con: sqlite3.Connection, text: str, limit: int = 200) -> list[tuple[str, int, str]]:
    """SPEC_V2 G1.3: [(jid, seq, text)] best match first; [] when the query is empty."""
    q = fts_query(text)
    if not q:
        return []
    return con.execute("SELECT jid, seq, text FROM passages_fts WHERE passages_fts MATCH ? "
                       "ORDER BY bm25(passages_fts) LIMIT ?", (q, limit)).fetchall()


def load_passages(con: sqlite3.Connection, jid: str) -> list[Passage]:
    rows = con.execute("SELECT segment_index, role, text FROM passages WHERE jid=? ORDER BY seq",
                       (jid,)).fetchall()
    return [Passage(si, SegmentRole(role), text) for si, role, text in rows]


def main_text_of(con: sqlite3.Connection, jid: str) -> str | None:
    rows = con.execute("SELECT text FROM segments WHERE jid=? AND part='main' ORDER BY idx", (jid,)).fetchall()
    return "\n".join(r[0] for r in rows) or None


def segments_of(con: sqlite3.Connection, jid: str) -> list[Segment]:
    rows = con.execute("SELECT idx, role, part, marker, text FROM segments WHERE jid=? ORDER BY idx",
                       (jid,)).fetchall()
    return [Segment(i, SegmentRole(r), t, part, m) for i, r, part, m, t in rows]


def check_index_ready(con: sqlite3.Connection, jids) -> None:
    """Raise IndexNotReady unless the index matches SEGMENT_RULES_VERSION and holds every jid."""
    if indexed_version(con) != SEGMENT_RULES_VERSION:
        raise IndexNotReady("索引版本與切段規則版本不同或尚未建立")
    for jid in jids:
        if con.execute("SELECT 1 FROM passages WHERE jid=? LIMIT 1", (jid,)).fetchone() is None:
            raise IndexNotReady(f"{jid} 沒有段落資料")
