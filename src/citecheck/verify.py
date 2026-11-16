"""L1 verification (SPEC 6.5)."""
import sqlite3

from .extract import Lexicon, out_of_scope
from .models import (JudgmentCitation, JudgmentStatus, StatuteCitation,
                     StatuteStatus)


def verify_statute(c: StatuteCitation, con: sqlite3.Connection) -> StatuteCitation:
    if c.law_name is None or c.article is None:
        c.status = StatuteStatus.UNRESOLVED_REF
        return c
    if con.execute("SELECT 1 FROM laws WHERE law_name=?", (c.law_name,)).fetchone() is None:
        c.status = StatuteStatus.LAW_NOT_FOUND
        return c
    row = con.execute("SELECT text FROM articles WHERE law_name=? AND article=?",
                      (c.law_name, c.article)).fetchone()
    if row is None:
        c.status = StatuteStatus.ARTICLE_NOT_FOUND
        return c
    text = row[0]
    if "".join(text.split()) in ("（刪除）", "(刪除)"):
        c.status = StatuteStatus.ARTICLE_DELETED
        return c
    c.status = StatuteStatus.FOUND
    c.article_text = text
    return c


def _meta(con: sqlite3.Connection) -> dict:
    return dict(con.execute("SELECT key, value FROM meta").fetchall())


def verify_judgment(c: JudgmentCitation, con: sqlite3.Connection, lex: Lexicon) -> JudgmentCitation:
    # 1. scope (extract already folded in the written 民事/刑事/行政 domain)
    if c.status == JudgmentStatus.OUT_OF_SCOPE or out_of_scope(c.court, None, c.case_word, lex.case_words):
        c.status = JudgmentStatus.OUT_OF_SCOPE
        return c
    # 2. coverage (case-year range of imported data; decided in Phase 1)
    meta = _meta(con)
    ymin, ymax = meta.get("year_min"), meta.get("year_max")
    if not ymin or not ymax or c.year is None or not (int(ymin) <= c.year <= int(ymax)):
        c.status = JudgmentStatus.DATA_NOT_COVERED
        return c
    # 3. lookup
    rows = con.execute(
        "SELECT jid, doc_kind FROM judgments WHERE year=? AND case_word=? AND case_no=?",
        (c.year, c.case_word, c.case_no)).fetchall()
    if len(rows) > 1:
        if c.doc_kind:
            rows = [r for r in rows if r[1] == c.doc_kind]
        if len(rows) != 1:
            c.status = JudgmentStatus.AMBIGUOUS
            return c
    if not rows:
        c.status = JudgmentStatus.NOT_FOUND_IN_SCOPE
    elif len(rows) == 1:
        c.status = JudgmentStatus.FOUND
        c.jid = rows[0][0]
    else:
        c.status = JudgmentStatus.AMBIGUOUS
    return c
