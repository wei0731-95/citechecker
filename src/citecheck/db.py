import sqlite3
from pathlib import Path

DEFAULT_DB = Path("data/citecheck.db")
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS laws(
  law_name TEXT PRIMARY KEY, modified_date TEXT,
  url TEXT, pcode TEXT, repealed INTEGER NOT NULL DEFAULT 0,
  effective_date TEXT, effective_note TEXT);
CREATE TABLE IF NOT EXISTS articles(
  law_name TEXT, article TEXT, text TEXT, PRIMARY KEY(law_name, article));
CREATE TABLE IF NOT EXISTS judgments(
  jid TEXT PRIMARY KEY, year INTEGER, case_word TEXT, case_no INTEGER,
  doc_kind TEXT, date TEXT, title TEXT, full_text TEXT,
  pdf_url TEXT, month TEXT, procedural_dismissal INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_judgments_case ON judgments(year, case_word, case_no);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS segments(
  jid TEXT, idx INTEGER, role TEXT, part TEXT, marker TEXT, text TEXT, PRIMARY KEY(jid, idx));
CREATE TABLE IF NOT EXISTS passages(
  jid TEXT, seq INTEGER, segment_index INTEGER, role TEXT, text TEXT, PRIMARY KEY(jid, seq));
CREATE TABLE IF NOT EXISTS quoted_sentences(
  jid TEXT, passage_seq INTEGER, sent_idx INTEGER, speaker TEXT,
  PRIMARY KEY(jid, passage_seq, sent_idx));
CREATE TABLE IF NOT EXISTS judgment_prep(
  jid TEXT PRIMARY KEY, status TEXT, model TEXT, prompt_version INTEGER,
  segment_rules_version INTEGER, created_at TEXT);
"""

OLD_SCHEMA_MESSAGE = "資料庫是舊版結構，請刪除後重新匯入"


class OldSchemaError(RuntimeError):
    pass


def _check_not_old(con: sqlite3.Connection) -> None:
    cols = [r[1] for r in con.execute("PRAGMA table_info(judgments)")]
    if cols and "pdf_url" not in cols:
        raise OldSchemaError(OLD_SCHEMA_MESSAGE)


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    try:
        _check_not_old(con)
    except OldSchemaError:
        con.close()
        raise
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),))
    con.commit()
    return con


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
