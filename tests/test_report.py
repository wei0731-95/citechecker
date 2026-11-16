import json
import re
import sqlite3

import pytest

from citecheck.db import SCHEMA, set_meta
from citecheck.extract import Lexicon
from citecheck.llm import MockClient
from citecheck.report import DISCLAIMER, ZH, build_report, color_of, render_html


@pytest.fixture()
def con():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO laws(law_name, modified_date) VALUES('假法','20250101')")
    c.executemany("INSERT INTO articles VALUES('假法',?,?)", [("1", "fake 條文一"), ("2", "（刪除）")])
    full = ("最高法院民事判決\n115年度台上字第7號\n上  訴  人  甲\n    主  文\n上訴駁回。\n    理  由\n"
            + "一、按出賣人應負瑕疵擔保責任。\n" * 6 + "中　　華　　民　　國　　115 　年　　3 　　月　　31　　日\n")
    c.execute("INSERT INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, month) "
              "VALUES('TPSV,115,台上,7,20260101,1',115,'台上',7,'判決','20260101','fake',?, '202601')", (full,))
    set_meta(c, "year_min", "110")
    set_meta(c, "year_max", "115")
    set_meta(c, "months_imported", '["202601", "202602"]')
    from citecheck.index import build_index   # SPEC_V2 F1: judge reads the passages table
    build_index(c)
    return c


LEX = Lexicon({"假法": "假法"}, {}, {"台上"})
BRIEF = ("依假法第1條及假法第2條、假法第9條規定。<script>x</script>\n"
         "按出賣人應負瑕疵擔保責任，最高法院115年度台上字第7號判決意旨參照。"
         "又最高法院115年度台上字第99號民事判決、臺灣高等法院115年度上字第1號判決。")


def test_html_is_self_contained_and_escaped(con):
    ok = json.dumps({"results": [{"id": i, "relation": "SUPPORTS", "confidence": 0.9} for i in range(1, 6)]})
    r = build_report(BRIEF, "fake.txt", con, LEX, MockClient(ok))
    html = render_html(r)
    assert DISCLAIMER in html and "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert not re.search(r"https?://|<script|<link|<img", html)
    for banned in ("不存在", "偽造", "虛構"):
        assert banned not in html
    assert "查無公開裁判" in html
    for cls in ("green", "red", "orange", "gray"):
        assert f'class="hl {cls}"' in html or cls != "green"
    assert html.count('class="card"') == len(r.statutes) + len(r.judgments)


def test_colors_follow_spec(con):
    r = build_report(BRIEF, "fake.txt", con, LEX, MockClient(
        json.dumps({"results": [{"id": i, "relation": "SUPPORTS", "confidence": 0.9} for i in range(1, 6)]})))
    by = {s.article: color_of(s) for s in r.statutes}
    assert by == {"1": "green", "2": "orange", "9": "red"}
    j = {c.case_no: color_of(c) for c in r.judgments}
    assert j[99] == "red" and j[1] == "gray"  # 查無公開裁判 / 範圍外


def test_zh_wording_has_no_banned_terms():
    flat = json.dumps(ZH, ensure_ascii=False)
    assert not any(b in flat for b in ("不存在", "偽造", "虛構"))
    assert ZH["JUDGMENT"]["NOT_FOUND_IN_SCOPE"] == "查無公開裁判"
