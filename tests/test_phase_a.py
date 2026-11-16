"""SPEC_V2 Phase A tests (A1-A13). Fake data only; databases live in tmp_path."""
import json

import pytest

from citecheck.db import connect, set_meta
from citecheck.extract import Lexicon, extract_full
from citecheck.ingest_judgments import ingest_judgments
from citecheck.ingest_laws import articles_in_note, ingest_laws
from citecheck.models import JudgmentStatus as JS
from citecheck.models import StatuteStatus as SS
from citecheck.normalize import normalize
from citecheck.report import build_report
from citecheck.verify import covered_years, verify_judgment, verify_statute

NAMES = ["民法", "消費者保護法", "中華民國刑法", "陸海空軍刑法", "金融控股公司法", "勞動基準法", "民事訴訟法"]


@pytest.fixture(scope="module")
def lex():
    return Lexicon({n: n for n in NAMES}, {"刑法": "中華民國刑法", "勞基法": "勞動基準法"}, {"台上", "台抗"})


def ex(text, lex):
    return extract_full(normalize(text), lex)


def one(text, lex):
    r = ex(text, lex)
    assert len(r.statutes) == 1, r.statutes
    return r.statutes[0]


# ---------------------------------------------------------------- A1
@pytest.mark.parametrize("text,law,status", [
    ("依民法第184條", "民法", None),
    ("依移民法第18條", None, SS.LAW_NAME_UNCERTAIN),
    ("數位消費者保護法第5條", None, SS.LAW_NAME_UNCERTAIN),
    ("違反民法第72條", "民法", None),
    ("我國民法第1條", "民法", None),
    ("日民法第920條", None, SS.LAW_NAME_UNCERTAIN),
    ("刑法第339條", "中華民國刑法", None),
    ("陸海空軍刑法第1條", "陸海空軍刑法", None),
    ("金融控股公司法第4條", "金融控股公司法", None),
])
def test_a1_law_name_left_boundary(lex, text, law, status):
    c = one(text, lex)
    assert c.law_name == law
    if status:
        assert c.status == status
    else:
        assert c.status != SS.LAW_NAME_UNCERTAIN


def test_a1_uncertain_raw_starts_at_matched_name(lex):
    assert one("依移民法第18條", lex).raw == "民法第18條"


# ---------------------------------------------------------------- A2
@pytest.mark.parametrize("text", ["系爭契約第16條", "工作規則第5條", "系爭租約第5條"])
def test_a2_contract_clauses_are_not_statutes(lex, text):
    assert ex(text, lex).statutes == []


def test_a2_backref_and_bare_article(lex):
    assert one("依本法第3條", lex).status == SS.UNRESOLVED_REF
    assert one("依第184條規定", lex).status == SS.UNRESOLVED_REF


def test_a2_order_is_out_of_scope_with_name_start(lex):
    c = one("依勞動基準法施行細則第7條", lex)
    assert c.status == SS.LAW_OUT_OF_SCOPE and c.raw == "勞動基準法施行細則第7條" and c.law_name is None
    assert one("臺北市道路挖掘管理自治條例第3條", lex).status == SS.LAW_OUT_OF_SCOPE


def test_a2_unknown_law_like_name_is_uncertain(lex):
    assert one("依寵物繼承保障法第3條", lex).status == SS.LAW_NAME_UNCERTAIN
    assert one("依公平法第25條", lex).status == SS.LAW_NAME_UNCERTAIN


def test_a2_uncertain_and_out_of_scope_survive_verify(lex, tmp_path):
    con = connect(tmp_path / "t.db")
    assert verify_statute(one("依公平法第25條", lex), con).status == SS.LAW_NAME_UNCERTAIN
    assert verify_statute(one("依勞動基準法施行細則第7條", lex), con).status == SS.LAW_OUT_OF_SCOPE


# ---------------------------------------------------------------- A3
def test_a3_covered_years():
    assert covered_years([]) == set()
    assert covered_years(["202305", "202602", "202603", "202604", "202605", "202606", "202607", "202608"]) == set()
    assert covered_years([f"2026{m:02d}" for m in range(1, 9)]) == {115}
    assert covered_years([f"2025{m:02d}" for m in range(1, 13)] + [f"2026{m:02d}" for m in range(1, 9)]) == {114, 115}


def _db(path, months, rows=()):
    con = connect(path)
    con.execute("INSERT INTO laws(law_name, modified_date) VALUES('民法','20250101')")
    for r in rows:
        con.execute("INSERT INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, month) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", (*r, "20260101", "fake", "fake", "202601"))
    set_meta(con, "months_imported", json.dumps(months))
    con.commit()
    return con


def _j(lex, text):
    return ex(text, lex).judgments[0]


def test_a3_found_wins_even_when_year_not_covered(lex, tmp_path):
    con = _db(tmp_path / "a.db", ["202602"], [("TPSV,115,台上,7,20260101,1", 115, "台上", 7, "判決")])
    assert verify_judgment(_j(lex, "最高法院115年度台上字第7號判決"), con, lex).status == JS.FOUND


def test_a3_not_covered_vs_not_found(lex, tmp_path):
    con = _db(tmp_path / "b.db", ["202602"])          # 115 is not fully covered
    assert verify_judgment(_j(lex, "最高法院115年度台上字第8號民事判決"), con, lex).status == JS.DATA_NOT_COVERED
    con = _db(tmp_path / "c.db", [f"2026{m:02d}" for m in range(1, 4)])
    c = verify_judgment(_j(lex, "最高法院115年度台上字第8號民事判決"), con, lex)
    assert c.status == JS.NOT_FOUND_IN_SCOPE and c.latest_year_note
    c = verify_judgment(_j(lex, "最高法院115年度台上字第8號判決"), con, lex)
    assert c.status == JS.NOT_FOUND_CIVIL_ONLY


def test_a3_ingest_records_month_and_months_imported(tmp_path):
    for m in ("202301", "202603"):
        d = tmp_path / f"{m}--(Update)" / m / "最高法院民事"
        d.mkdir(parents=True)
        jid = f"TPSV,115,台上,{m[-2:]},{m}15,1"
        (d / f"{jid}.json").write_text(json.dumps({
            "JID": jid, "JYEAR": "115", "JCASE": "台上", "JNO": m[-2:], "JDATE": f"{m}15",
            "JTITLE": "fake", "JFULL": "最高法院民事判決  fake", "JPDF": "https://example.invalid/x.pdf"},
            ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "t.db"
    ingest_judgments(tmp_path, db)
    con = connect(db)
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    assert json.loads(meta["months_imported"]) == ["202301", "202603"]
    assert con.execute("SELECT month, pdf_url FROM judgments WHERE month='202603'").fetchone() == (
        "202603", "https://example.invalid/x.pdf")


# ---------------------------------------------------------------- A4 / A5 / A6
def test_a4_domain_stated(lex):
    assert _j(lex, "最高法院109年度台上字第123號民事判決").domain_stated == "民事"
    c = _j(lex, "最高法院109年度台上字第123號刑事判決")
    assert c.domain_stated == "刑事" and c.status == JS.OUT_OF_SCOPE
    assert _j(lex, "最高法院109年度台上字第123號判決").domain_stated is None


def test_a5_precedent_and_resolution_are_out_of_scope(lex):
    c = _j(lex, "最高法院86年台上字第1234號判例")
    assert c.status == JS.OUT_OF_SCOPE and c.doc_kind == "判例"


def test_a6_court_inferred_or_out_of_scope(lex):
    r = ex("（109年度台上字第123號判決參照）", lex)
    c = r.judgments[0]
    assert c.court == "最高法院" and c.court_inferred and r.warnings == []
    r = ex("見109年度上字第10號判決", lex)
    c = r.judgments[0]
    assert c.court is None and c.status == JS.OUT_OF_SCOPE and r.warnings == []


# ---------------------------------------------------------------- A8
def test_a8_original_spans_end_to_end(lex, tmp_path):
    con = _db(tmp_path / "d.db", ["202602"])
    text = "依 **民法** 第一百八十四條、最高法院 １０９ 年度臺上字第 123 號判決"
    r = build_report(text, "x", con, lex)
    assert r.original_text == text
    s, j = r.statutes[0], r.judgments[0]
    o = r.original_text
    assert o[s.original_span.start:s.original_span.end].startswith("民法")
    assert o[s.original_span.start:s.original_span.end].endswith("條")
    got = o[j.original_span.start:j.original_span.end]
    assert got.startswith("最高法院") and got.endswith("判決") and "１０９" in got and "臺" in got


# ---------------------------------------------------------------- A9 / A10 / A11
LAW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LAWS UpdateDate="2026/1/1">
 <法規><法規名稱>民法</法規名稱><法規網址>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001</法規網址>
  <最新異動日期>20250101</最新異動日期><生效日期>99991231</生效日期>
  <生效內容><![CDATA[1.八十八年四月二十一日增訂之第 166-1  條條文施行日期，由行政院會
  同司法院另定之。
2.一百十五年八月十七日修正公布第 1223 條條文，自公布六個月後施行
  。]]></生效內容><廢止註記></廢止註記>
  <法規內容><條文><條號>第 184 條</條號><條文內容><![CDATA[fake 條文。]]></條文內容></條文>
   <條文><條號>第 166-1 條</條號><條文內容><![CDATA[fake 條文之一。]]></條文內容></條文>
   <條文><條號>第 9 條</條號><條文內容><![CDATA[（刪除）]]></條文內容></條文></法規內容></法規>
 <法規><法規名稱>舊法</法規名稱><法規網址>https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=Z0000001</法規網址>
  <最新異動日期>20000101</最新異動日期><廢止註記>廢</廢止註記>
  <法規內容><條文><條號>第 1 條</條號><條文內容><![CDATA[x]]></條文內容></條文></法規內容></法規>
</LAWS>"""


def _laws_db(tmp_path):
    (tmp_path / "laws").mkdir()
    (tmp_path / "laws" / "fake_FalV.xml").write_text(LAW_XML, encoding="utf-8")
    db = tmp_path / "l.db"
    ingest_laws(tmp_path / "laws", db)
    return connect(db)


def test_a9_links(lex, tmp_path):
    con = _laws_db(tmp_path)
    c = verify_statute(one("民法第184條", lex), con)
    assert c.status == SS.FOUND
    assert c.law_url == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001"
    assert c.article_url == "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=B0000001&flno=184"
    c = verify_statute(one("民法第166條之1", lex), con)
    assert c.article_url.endswith("flno=166-1")
    c = verify_statute(one("民法第9條", lex), con)
    assert c.status == SS.ARTICLE_DELETED and c.article_url and c.law_url
    c = verify_statute(one("民法第999條", lex), con)
    assert c.status == SS.ARTICLE_NOT_FOUND and c.law_url and c.article_url is None


def test_a10_articles_in_note():
    note = ("1.八十八年四月二十一日增訂之第 166-1  條條文施行日期，由行政院會\n  同司法院另定之。\n"
            "2.一百十五年八月十七日修正公布第 1223 條條文，自公布六個月後施行\n  。")
    assert articles_in_note(note) == {"166-1", "1223"}
    assert articles_in_note("第 4、5、7～11、17  條") == {"4", "5", "7", "8", "9", "10", "11", "17"}


def test_a10_not_in_force_flag(lex, tmp_path):
    con = _laws_db(tmp_path)
    c = verify_statute(one("民法第166條之1", lex), con)
    assert c.status == SS.FOUND and c.not_in_force_note and c.structure_unchecked
    assert not verify_statute(one("民法第184條", lex), con).not_in_force_note


def test_a11_repealed_law(tmp_path):
    con = _laws_db(tmp_path)
    assert con.execute("SELECT repealed FROM laws WHERE law_name='舊法'").fetchone()[0] == 1
    lex2 = Lexicon({"舊法": "舊法"}, {}, set())
    c = verify_statute(one("依舊法第1條", lex2), con)
    assert c.status == SS.LAW_REPEALED and c.law_url and c.article_text is None


# ---------------------------------------------------------------- A12 / A13
@pytest.mark.parametrize("text,kind,raw", [
    ("依司法院釋字第七四八號解釋意旨", "釋字", "司法院釋字第748號解釋"),
    ("見憲法法庭112年憲判字第8號判決", "憲判字", "憲法法庭112年憲判字第8號判決"),
    ("最高法院108年度第3次民事庭會議決議(一)", "決議", "最高法院108年度第3次民事庭會議決議(一)"),
    ("依109年臺灣高等法院暨所屬法院民事庭法律座談會結論", "座談會", "109年台灣高等法院暨所屬法院民事庭法律座談會"),
])
def test_a12_other_authorities(lex, text, kind, raw):
    r = ex(text, lex)
    assert [(a.kind, a.raw) for a in r.other_authorities] == [(kind, raw)]


def test_a13_unrecognized_sentence(lex):
    r = ex("最高院（109）台上字第123號判決參照。", lex)
    assert len(r.unrecognized) == 1 and "123號" in r.unrecognized[0].text
    assert ex("最高法院109年度台上字第123號判決參照。", lex).unrecognized == []
    long = "最高院（109）台上字第123號判決參照" + "甲" * 300 + "。"
    assert len(ex(long, lex).unrecognized[0].text) == 200
