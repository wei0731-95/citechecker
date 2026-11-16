import json
from pathlib import Path

import pytest

from citecheck.extract import Lexicon, extract_all
from citecheck.models import JudgmentStatus, StatuteStatus
from citecheck.normalize import normalize

ROOT = Path(__file__).parent.parent
NAMES = ["民法", "民事訴訟法", "刑事訴訟法", "公寓大廈管理條例"]


@pytest.fixture(scope="module")
def lex():
    raw = json.loads((ROOT / "data/law_aliases.json").read_text(encoding="utf-8"))
    names = {n: n for n in NAMES}
    aliases = {k: v for k, v in raw.items()}
    words = set(json.loads((ROOT / "data/case_words_tps_civil.json").read_text(encoding="utf-8"))["case_words"])
    return Lexicon(names, aliases, words)


def run(text, lex):
    return extract_all(normalize(text), lex)


def J(text, lex):
    return run(text, lex)[1]


def key(c):
    return (c.court, c.year, c.case_word, c.case_no, c.doc_kind)


# ---- judgments J1-J10
def test_j1(lex):
    (c,) = J("最高法院109年度台上字第123號判決", lex)
    assert key(c) == ("最高法院", 109, "台上", 123, "判決")
    assert c.status == JudgmentStatus.NOT_FOUND_IN_SCOPE  # pending verify


def test_j2(lex):
    (c,) = J("最高法院 109 年度臺上字第 123 號民事判決", lex)
    assert key(c) == ("最高法院", 109, "台上", 123, "判決")


def test_j3(lex):
    (c,) = J("最高法院一○九年度台上字第一二三號判決", lex)
    assert key(c) == ("最高法院", 109, "台上", 123, "判決")


def test_j4(lex):
    (c,) = J("最高法院109年台上字第123號", lex)
    assert key(c) == ("最高法院", 109, "台上", 123, None)


def test_j5(lex):
    a, b = J("最高法院109年度台上字第123號、110年度台抗字第45號裁定", lex)
    assert key(a) == ("最高法院", 109, "台上", 123, "裁定")
    assert key(b) == ("最高法院", 110, "台抗", 45, "裁定")


def test_j6(lex):
    a, b = J("最高法院109年度台上字第123號、第124號判決", lex)
    assert key(a) == ("最高法院", 109, "台上", 123, "判決")
    assert key(b) == ("最高法院", 109, "台上", 124, "判決")


def test_j7(lex):
    a, b = J("最高法院109年度台上字第123號判決，同院110年度台上字第5號判決", lex)
    assert key(b) == ("最高法院", 110, "台上", 5, "判決")


def test_j8(lex):
    (c,) = J("臺灣高等法院109年度上字第10號判決", lex)
    assert c.court == "台灣高等法院" and c.status == JudgmentStatus.OUT_OF_SCOPE


def test_j9(lex):
    (c,) = J("最高法院109年度台上字第123號刑事判決", lex)
    assert c.court == "最高法院" and c.status == JudgmentStatus.OUT_OF_SCOPE


def test_j10(lex):
    (c,) = J("最高法院109年度台上大字第2012號裁定", lex)
    assert c.case_word == "台上大" and c.status == JudgmentStatus.OUT_OF_SCOPE


def test_incomplete_goes_to_warnings(lex):
    _, js, warns = run("最高法院109年度台上字判決意旨參照。", lex)
    assert js == [] and len(warns) == 1


def test_court_missing_is_inferred_for_supreme_civil_case_word(lex):
    # SPEC_V2 A6 (was: skipped with a warning)
    _, js, warns = run("見109年度台上字第123號判決。", lex)
    assert len(js) == 1 and js[0].court == "最高法院" and js[0].court_inferred and not warns
    assert js[0].raw.startswith("109年度")


# ---- claim sentence
def test_claim_sentence_same_sentence_shared(lex):
    t = "按出賣人就買賣標的物有瑕疵時應負擔保責任，最高法院109年度台上字第1號、第2號判決意旨參照。"
    a, b = J(t, lex)
    assert a.claim_sentence == b.claim_sentence and "瑕疵" in a.claim_sentence


def test_claim_sentence_merges_previous_when_short(lex):
    t = "被告應負損害賠償責任並給付遲延利息。最高法院109年度台上字第1號判決意旨參照。"
    (c,) = J(t, lex)
    assert "損害賠償" in c.claim_sentence


# ---- statutes S1-S10
def S(text, lex):
    return run(text, lex)[0]


def sk(c):
    return (c.law_name, c.article, c.paragraph, c.subparagraph)


def test_s1(lex):
    (c,) = S("民法第184條第1項前段", lex)
    assert sk(c) == ("民法", "184", 1, None)


def test_s2(lex):
    (c,) = S("民法第一百八十四條", lex)
    assert sk(c) == ("民法", "184", None, None)


def test_s3(lex):
    (c,) = S("民事訴訟法第277條但書", lex)
    assert sk(c) == ("民事訴訟法", "277", None, None) and c.raw.endswith("但書")


def test_s4(lex):
    (c,) = S("公寓大廈管理條例第15條之1", lex)
    assert c.article == "15-1"


def test_s5(lex):
    (c,) = S("民訴第277條", lex)
    assert c.law_name == "民事訴訟法"


def test_s6(lex):
    a, b = S("民法第184條、第185條", lex)
    assert sk(a)[:2] == ("民法", "184") and sk(b)[:2] == ("民法", "185")


def test_s7(lex):
    a, b = S("民法第184條。同法第195條", lex)
    assert sk(b)[:2] == ("民法", "195")


def test_s8(lex):
    a, b = S("民法第184條第1項。同條第2項", lex)
    assert sk(b) == ("民法", "184", 2, None)


def test_s9(lex):
    a, b = S("民法第184條至第186條", lex)
    assert (a.article, b.article) == ("184", "186") and b.law_name == "民法"


def test_s10(lex):
    (c,) = S("依第184條規定", lex)
    assert c.status == StatuteStatus.UNRESOLVED_REF and c.law_name is None


def test_same_law_without_antecedent_unresolved(lex):
    (c,) = S("同法第195條", lex)
    assert c.status == StatuteStatus.UNRESOLVED_REF


def test_longest_match_prefers_full_name(lex):
    (c,) = S("刑事訴訟法第1條", lex)
    assert c.law_name == "刑事訴訟法"
