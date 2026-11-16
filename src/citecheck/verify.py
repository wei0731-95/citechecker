"""L1 verification (SPEC 6.5, SPEC_V2 A1-A3, A9-A11)."""
import json
import re
import sqlite3

from .extract import Lexicon, out_of_scope
from .ingest_laws import articles_in_note
from .models import (JudgmentCitation, JudgmentStatus, StatuteCitation,
                     StatuteStatus)
from .segment import segment

ARTICLE_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={article}"
_ARTICLE_KEY = re.compile(r"^\d+(-\d+)?$")

_PASS_THROUGH = (StatuteStatus.LAW_NAME_UNCERTAIN, StatuteStatus.LAW_OUT_OF_SCOPE)


def verify_statute(c: StatuteCitation, con: sqlite3.Connection) -> StatuteCitation:
    if c.status in _PASS_THROUGH:          # decided at extraction (A1, A2); never overwritten
        return c
    if c.law_name is None or c.article is None:
        c.status = StatuteStatus.UNRESOLVED_REF
        return c
    law = con.execute("SELECT url, pcode, repealed, effective_note FROM laws WHERE law_name=?",
                      (c.law_name,)).fetchone()
    if law is None:
        c.status = StatuteStatus.LAW_NOT_FOUND
        return c
    url, pcode, repealed, effective_note = law
    if repealed:                            # A11
        c.status = StatuteStatus.LAW_REPEALED
        c.law_url = url
        return c
    row = con.execute("SELECT text FROM articles WHERE law_name=? AND article=?",
                      (c.law_name, c.article)).fetchone()
    if row is None:
        c.status = StatuteStatus.ARTICLE_NOT_FOUND
        c.law_url = url
        return c
    text = row[0]
    c.law_url = url
    if pcode and _ARTICLE_KEY.match(c.article):
        c.article_url = ARTICLE_URL.format(pcode=pcode, article=c.article)
    if "".join(text.split()) in ("（刪除）", "(刪除)"):
        c.status = StatuteStatus.ARTICLE_DELETED
        return c
    c.status = StatuteStatus.FOUND
    c.article_text = text
    if c.article in articles_in_note(effective_note or ""):     # A10
        c.not_in_force_note = True
        c.structure_unchecked = True
    return c


def _meta(con: sqlite3.Connection) -> dict:
    return dict(con.execute("SELECT key, value FROM meta").fetchall())


def covered_years(months: list[str]) -> set[int]:
    """A3: ROC year Y is covered iff every month from (Y+1911)01 to the latest imported month is."""
    if not months:
        return set()
    have = set(months)
    last = max(months)
    last_idx = int(last[:4]) * 12 + int(last[4:]) - 1
    out = set()
    for year_ad in range(int(min(months)[:4]), int(last[:4]) + 1):
        need = {f"{i // 12:04d}{i % 12 + 1:02d}" for i in range(year_ad * 12, last_idx + 1)}
        if need <= have:
            out.add(year_ad - 1911)
    return out


def imported_months(con: sqlite3.Connection) -> list[str]:
    raw = _meta(con).get("months_imported")
    return json.loads(raw) if raw else []


def verify_judgment(c: JudgmentCitation, con: sqlite3.Connection, lex: Lexicon) -> JudgmentCitation:
    # 1. scope (extract already folded in the written 民事/刑事/行政 domain and 判例/決議)
    if c.status == JudgmentStatus.OUT_OF_SCOPE or out_of_scope(
            c.court, c.domain_stated, c.case_word, lex.case_words):
        c.status = JudgmentStatus.OUT_OF_SCOPE
        return c
    # 2. look the number up first: whatever the database has is found
    rows = con.execute(
        "SELECT jid, doc_kind, pdf_url, full_text, procedural_dismissal FROM judgments "
        "WHERE year=? AND case_word=? AND case_no=?", (c.year, c.case_word, c.case_no)).fetchall()
    many = len(rows) > 1
    if many and c.doc_kind:
        rows = [r for r in rows if r[1] == c.doc_kind]
    if len(rows) == 1:                                      # 3
        jid, _, pdf_url, full_text, dismissal = rows[0]
        c.status = JudgmentStatus.FOUND
        c.jid, c.pdf_url, c.procedural_dismissal = jid, pdf_url, bool(dismissal)
        main = "\n".join(s.text for s in segment(full_text) if s.part == "main")
        c.main_text = main or None
        return c
    if many:                                                # 4 (also: doc_kind matched none of several)
        c.status = JudgmentStatus.AMBIGUOUS
        return c
    # 0 rows: only now look at what the data covers
    years = covered_years(imported_months(con))
    if c.year not in years:                                 # 5
        c.status = JudgmentStatus.DATA_NOT_COVERED
        return c
    c.status = (JudgmentStatus.NOT_FOUND_IN_SCOPE if c.domain_stated == "民事"
                else JudgmentStatus.NOT_FOUND_CIVIL_ONLY)   # 6
    if c.year == max(years):
        c.latest_year_note = True
    return c
