from citecheck.statute_check import parse_article


def test_simple_one_paragraph():
    assert parse_article("因故意或過失，不法侵害他人之權利者，負損害賠償責任。") == [[]]


def test_two_paragraphs_no_items():
    assert parse_article("第一項本文。\n第二項本文。") == [[], []]


def test_items_belong_to_previous_paragraph():
    text = "有下列各款情形之一者：\n一、甲。\n二、乙。\n三、丙。"
    assert parse_article(text) == [[1, 2, 3]]
    assert parse_article("前項規定如下：\n一、甲；\n二、乙。\n後項為本文。") == [[1, 2], []]


def test_sub_items_are_ignored():
    text = "有下列各款情形之一者：\n一、甲，包括：\n（一）子；\n（二）丑。\n二、乙。"
    assert parse_article(text) == [[1, 2]]
    assert parse_article("下列情形：\n一、甲：\n(一)子。\n二、乙。") == [[1, 2]]


def test_chinese_item_numbers_with_tens():
    items = "\n".join(f"{n}、款{i}；" for i, n in enumerate("一二三四五六七八九十", 1))
    assert parse_article("有下列各款：\n" + items.rstrip("；") + "。")[0] == list(range(1, 11))


def test_first_line_is_item_or_sub_item_is_none():
    assert parse_article("一、甲。\n二、乙。") is None
    assert parse_article("（一）甲。") is None


def test_item_numbers_must_start_at_one_and_be_consecutive():
    assert parse_article("下列各款：\n二、乙。\n三、丙。") is None
    assert parse_article("下列各款：\n一、甲；\n三、丙。") is None


def test_paragraph_line_must_end_with_a_period_or_colon():
    assert parse_article("這一項沒有句號") is None
    assert parse_article("第一項。\n第二項沒有句號") is None
    assert parse_article("說明如下：\n一、甲。") == [[1]]
    assert parse_article("說明如下︰\n一、甲。") == [[1]]


def test_empty_or_deleted_text():
    assert parse_article("") is None
    assert parse_article("（刪除）") is None


# ------------------------------------------------------------------ SPEC_V3 H5
import json  # noqa: E402

import pytest  # noqa: E402

from citecheck.db import connect  # noqa: E402
from citecheck.extract import Lexicon, extract_full  # noqa: E402
from citecheck.llm import MockClient  # noqa: E402
from citecheck.models import StatuteL2Label, StatuteStatus  # noqa: E402
from citecheck.normalize import normalize  # noqa: E402
from citecheck.statute_check import apply_statute_l2  # noqa: E402
from citecheck.verify import verify_statute  # noqa: E402


@pytest.fixture()
def h5_con(tmp_path):
    c = connect(tmp_path / "fake_h5.db")
    c.execute("INSERT INTO laws(law_name, modified_date) VALUES('民法','2025')")
    c.execute("INSERT INTO articles VALUES('民法','195','第一項本文。\n第二項本文。')")
    c.commit()
    return c


def _h5_cites(text, con):
    lex = Lexicon({"民法": "民法"}, {}, set())
    return [verify_statute(s, con) for s in extract_full(normalize(text), lex).statutes]


def test_h5_continuation_is_checked_for_paragraph(h5_con):
    a, b = _h5_cites("依民法第195條第1項、第5項規定。", h5_con)
    assert a.status == StatuteStatus.FOUND
    assert b.is_continuation and b.status == StatuteStatus.PARAGRAPH_NOT_FOUND


def test_h5_continuation_makes_no_llm_call(h5_con):
    a, b = _h5_cites("依民法第195條第1項、第2項規定。", h5_con)
    assert a.status == b.status == StatuteStatus.FOUND and b.claim_sentence
    m = MockClient(json.dumps({"relation": "CONSISTENT", "confidence": 0.9}))
    apply_statute_l2([a, b], m)
    assert len(m.calls) == 1
    assert a.l2_label == StatuteL2Label.MATCHES and b.l2_label == StatuteL2Label.NOT_RUN
