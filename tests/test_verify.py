import sqlite3

import pytest

from citecheck.db import SCHEMA, set_meta
from citecheck.extract import Lexicon
from citecheck.models import (JudgmentCitation, JudgmentStatus, Span,
                              StatuteCitation, StatuteStatus)
from citecheck.report import build_report
from citecheck.verify import verify_judgment, verify_statute

LEX = Lexicon({}, {}, {"台上", "台抗", "台聲"})


@pytest.fixture()
def con():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO laws VALUES('假法','20250101')")
    c.executemany("INSERT INTO articles VALUES('假法',?,?)",
                  [("1", "fake 條文一"), ("2", "（刪除）"), ("15-1", "fake 條文之一")])
    rows = [("TPSV,110,台上,5,20260101,1", 110, "台上", 5, "判決"),
            ("TPSV,115,台上,7,20260101,1", 115, "台上", 7, "判決"),
            ("TPSV,115,台上,7,20260101,2", 115, "台上", 7, "裁定"),
            ("TPSV,115,台聲,9,20260101,1", 115, "台聲", 9, "裁定"),
            ("TPSV,115,台聲,9,20260101,2", 115, "台聲", 9, "裁定")]
    c.executemany("INSERT INTO judgments VALUES(?,?,?,?,?,?,?,?)",
                  [(*r[:5], "20260101", "fake", "fake") for r in rows])
    set_meta(c, "year_min", "110")
    set_meta(c, "year_max", "115")
    return c


def st(law, art):
    return StatuteCitation(raw="x", span=Span(start=0, end=1), law_name=law, article=art)


def jd(year, word, no, kind=None, court="最高法院"):
    return JudgmentCitation(raw="x", span=Span(start=0, end=1), court=court, year=year,
                            case_word=word, case_no=no, doc_kind=kind)


def test_statute_found(con):
    c = verify_statute(st("假法", "15-1"), con)
    assert c.status == StatuteStatus.FOUND and c.article_text == "fake 條文之一"


def test_statute_law_not_found(con):
    assert verify_statute(st("無此法", "1"), con).status == StatuteStatus.LAW_NOT_FOUND


def test_statute_article_not_found(con):
    assert verify_statute(st("假法", "99"), con).status == StatuteStatus.ARTICLE_NOT_FOUND


def test_statute_deleted(con):
    assert verify_statute(st("假法", "2"), con).status == StatuteStatus.ARTICLE_DELETED


def test_statute_unresolved(con):
    assert verify_statute(st(None, "1"), con).status == StatuteStatus.UNRESOLVED_REF


def test_judgment_found(con):
    c = verify_judgment(jd(110, "台上", 5), con, LEX)
    assert c.status == JudgmentStatus.FOUND and c.jid == "TPSV,110,台上,5,20260101,1"


def test_judgment_not_found_in_scope(con):
    assert verify_judgment(jd(115, "台上", 8888), con, LEX).status == JudgmentStatus.NOT_FOUND_IN_SCOPE


def test_judgment_out_of_scope(con):
    assert verify_judgment(jd(115, "上", 1, court="台灣高等法院"), con, LEX).status == JudgmentStatus.OUT_OF_SCOPE
    assert verify_judgment(jd(115, "台非", 1), con, LEX).status == JudgmentStatus.OUT_OF_SCOPE


def test_judgment_data_not_covered(con):
    assert verify_judgment(jd(109, "台上", 5), con, LEX).status == JudgmentStatus.DATA_NOT_COVERED


def test_judgment_multi_filtered_by_kind(con):
    c = verify_judgment(jd(115, "台上", 7, "裁定"), con, LEX)
    assert c.status == JudgmentStatus.FOUND and c.jid.endswith(",2")


def test_judgment_multi_without_kind_ambiguous(con):
    assert verify_judgment(jd(115, "台上", 7), con, LEX).status == JudgmentStatus.AMBIGUOUS


def test_judgment_multi_same_kind_ambiguous(con):
    assert verify_judgment(jd(115, "台聲", 9, "裁定"), con, LEX).status == JudgmentStatus.AMBIGUOUS


def test_build_report_end_to_end(con):
    lex = Lexicon({"假法": "假法"}, {}, {"台上"})
    text = "依假法第1條，最高法院１１０年度台上字第５號判決意旨參照，另依假法第二條規定。"
    r = build_report(text, "fake.txt", con, lex)
    assert [s.status for s in r.statutes] == [StatuteStatus.FOUND, StatuteStatus.ARTICLE_DELETED]
    assert r.judgments[0].status == JudgmentStatus.FOUND
    assert r.data_coverage.judgment_year_min == 110
    assert all(r.normalized_text[s.span.start:s.span.end] == s.raw for s in r.statutes)
