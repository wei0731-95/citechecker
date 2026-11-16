import sqlite3
from pathlib import Path

DEFAULT_DB = Path("data/citecheck.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS laws(law_name TEXT PRIMARY KEY, modified_date TEXT);
CREATE TABLE IF NOT EXISTS articles(
  law_name TEXT, article TEXT, text TEXT, PRIMARY KEY(law_name, article));
CREATE TABLE IF NOT EXISTS judgments(
  jid TEXT PRIMARY KEY, year INTEGER, case_word TEXT, case_no INTEGER,
  doc_kind TEXT, date TEXT, title TEXT, full_text TEXT);
CREATE INDEX IF NOT EXISTS idx_judgments_case ON judgments(year, case_word, case_no);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.executescript(SCHEMA)
    return con


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, value))
