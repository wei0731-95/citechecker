"""SPEC_V2 Phase C tests: decide rules (C1), apply_quotes and failure path (C2), procedural rulings (C3)."""
import json
import re

import pytest

from citecheck.db import connect, set_meta
from citecheck.extract import Lexicon
from citecheck.index import build_index
from citecheck.judge import judge_passages
from citecheck.llm import MockClient
from citecheck.models import L2Label
from citecheck.models import SegmentRole as R
from citecheck.report import build_report
from citecheck.retrieve import Passage, apply_quotes

S, C, U = "SUPPORTS", "CONTRADICTS", "UNRELATED"


def P(i, role):
    return Passage(i, role, f"假段落{i}")


def run(roles, pairs):
    passages = [P(i, r) for i, r in enumerate(roles)]
    reply = json.dumps({"results": [{"id": i + 1, "relation": r, "confidence": c} for i, (r, c) in enumerate(pairs)]})
    return judge_passages("假主張句", passages, MockClient(reply))


# ---------------------------------------------------------------- C1: the seven rules
def test_c1_rule1_court_support_without_court_contradiction():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(C, 0.9), (S, 0.8)])
    assert label == L2Label.COURT_HOLDING and ev.segment_role == R.SUPREME_COURT


def test_c1_rule2_court_support_and_court_contradiction_is_uncertain():
    label, ev = run([R.SUPREME_COURT, R.SUPREME_COURT], [(S, 0.7), (C, 0.9)])
    assert label == L2Label.UNCERTAIN and ev.relation.value == C and ev.confidence == 0.9
    label, ev = run([R.SUPREME_COURT, R.SUPREME_COURT], [(S, 0.95), (C, 0.7)])
    assert label == L2Label.UNCERTAIN and ev.relation.value == S


def test_c1_rule3_court_contradiction_only():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(S, 0.9), (C, 0.8)])
    assert label == L2Label.CONTRADICTED and ev.segment_role == R.SUPREME_COURT


def test_c1_rule4_lower_court_support():
    label, _ = run([R.PARTY_CLAIM, R.LOWER_COURT], [(S, 0.9), (S, 0.8)])
    assert label == L2Label.LOWER_COURT_VIEW


def test_c1_rule5_party_support():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(S, 0.9), (U, 0.9)])
    assert label == L2Label.PARTY_ARGUMENT and ev.segment_index == 0


def test_c1_rule6_unknown_or_main_text_is_uncertain():
    label, ev = run([R.UNKNOWN, R.MAIN_TEXT], [(S, 0.7), (C, 0.9)])
    assert label == L2Label.UNCERTAIN and ev.confidence == 0.9


def test_c1_rule7_everything_else_is_not_in_judgment():
    label, ev = run([R.SUPREME_COURT, R.PARTY_CLAIM], [(U, 0.9), (S, 0.59)])
    assert label == L2Label.NOT_IN_JUDGMENT and ev is None


# ---------------------------------------------------------------- C2: apply_quotes
def sup(text, idx=3):
    return Passage(idx, R.SUPREME_COURT, text)


def test_apply_quotes_all_sentences_quoted():
    out = apply_quotes([sup("甲。乙。")], {(0, 0): "LOWER_COURT", (0, 1): "LOWER_COURT"})
    assert [(p.role, p.text, p.segment_index) for p in out] == [(R.LOWER_COURT, "甲。乙。", 3)]


def test_apply_quotes_partial_splits_in_order_and_maps_roles():
    out = apply_quotes([sup("甲。乙。丙。丁。")], {(0, 1): "PARTY", (0, 2): "PARTY", (0, 3): "LOWER_COURT"})
    assert [(p.role, p.text) for p in out] == [
        (R.SUPREME_COURT, "甲。"), (R.PARTY_CLAIM, "乙。丙。"), (R.LOWER_COURT, "丁。")]
    assert all(p.segment_index == 3 for p in out)


def test_apply_quotes_without_quotes_keeps_the_passage_whole():
    assert apply_quotes([sup("甲。乙。")], {}) == [sup("甲。乙。")]


def test_apply_quotes_leaves_other_roles_alone_and_uses_position_as_seq():
    ps = [Passage(0, R.PARTY_CLAIM, "當事人。"), sup("甲。乙。", 1), Passage(2, R.LOWER_COURT, "原審。")]
    out = apply_quotes(ps, {(0, 0): "LOWER_COURT", (1, 1): "PARTY"})       # (0,0) points at a non-本院 passage
    assert [(p.role, p.text) for p in out] == [
        (R.PARTY_CLAIM, "當事人。"), (R.SUPREME_COURT, "甲。"), (R.PARTY_CLAIM, "乙。"), (R.LOWER_COURT, "原審。")]


# ---------------------------------------------------------------- C2 / C3 through build_report
JUDGMENT = ("最高法院民事判決\n115年度台上字第7號\n上  訴  人  甲\n    主  文\n上訴駁回。\n    理  由\n"
            "一、本件被上訴人主張：兩造間有契約。\n"
            "二、本院之判斷：按該約款之性質應依契約全文判斷。原審雖認該約款為解除條件，惟其認定並無違誤。\n"
            "三、據上論結，本件上訴為無理由。\n"
            "中　　華　　民　　國　　115 　年　　3 　　月　　31　　日\n")
RULING = ("最高法院民事裁定\n115年度台抗字第3號\n抗  告  人  甲\n    主  文\n抗告駁回。\n    理  由\n"
          "一、按該約款為解除條件者，其效力應依契約全文判斷。\n"
          "據上論結，本件抗告為不合法。裁定如主文。\n"
          "中　　華　　民　　國　　115 　年　　3 　　月　　31　　日\n")
LEX = Lexicon({}, {}, {"台上", "台抗"})
KEYWORD = "解除條件"


class Smart(MockClient):
    """Answers the preparation call (marks sentences containing KEYWORD that begin with 原審) and the
    comparison call (SUPPORTS for passages containing KEYWORD), reading the prompt it is given."""

    def __init__(self, mark_quotes=True, fail_prep=False):
        super().__init__("")
        self.mark_quotes, self.fail_prep = mark_quotes, fail_prep

    def complete(self, system, user, max_tokens=600):
        self.calls.append((system, user))
        self.max_tokens.append(max_tokens)
        lines = re.findall(r"^\[(\d+)\] (.*)$", user, re.M)
        if system.startswith("你是法律文件標註助手"):
            if self.fail_prep:
                return "garbage"
            ids = [int(i) for i, t in lines if self.mark_quotes and t.startswith("原審")]
            return json.dumps({"quoted": [{"id": i, "speaker": "LOWER_COURT"} for i in ids]})
        claim = user.split("\n\n判決原文段落")[0]
        return json.dumps({"results": [
            {"id": int(i), "relation": S if KEYWORD in t and KEYWORD in claim else U, "confidence": 0.95}
            for i, t in lines]})


def setup_db(tmp_path, name, full, jid, kind, case_word, no):
    c = connect(tmp_path / name)
    c.execute("INSERT INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, month) "
              "VALUES(?,?,?,?,?,?,?,?,?)", (jid, 115, case_word, no, kind, "20260101", "fake", full, "202601"))
    set_meta(c, "months_imported", json.dumps(["202601", "202602"]))
    c.commit()
    build_index(c)
    return c


BRIEF = "按原審認該約款為解除條件，最高法院115年度台上字第7號判決意旨參照。"


def run_report(c, client, brief=BRIEF):
    return build_report(brief, "x", c, LEX, client)


def test_quoted_sentence_in_a_court_passage_is_no_longer_taken_as_the_courts_view(tmp_path):
    jid = "TPSV,115,台上,7,20260101,1"
    plain = setup_db(tmp_path, "a.db", JUDGMENT, jid, "判決", "台上", 7)
    r = run_report(plain, Smart(mark_quotes=False))
    assert r.judgments[0].l2_label == L2Label.COURT_HOLDING                    # nothing flagged: held to be the Court's
    marked = setup_db(tmp_path, "b.db", JUDGMENT, jid, "判決", "台上", 7)
    r = run_report(marked, Smart(mark_quotes=True))
    j = r.judgments[0]
    assert j.l2_label == L2Label.LOWER_COURT_VIEW and j.l2_evidence.segment_role == R.LOWER_COURT
    assert "原審雖認" in j.l2_evidence.passage


def test_comparison_prompt_never_carries_roles(tmp_path):
    c = setup_db(tmp_path, "c.db", JUDGMENT, "TPSV,115,台上,7,20260101,1", "判決", "台上", 7)
    m = Smart(mark_quotes=True)
    run_report(c, m)
    compare = [u for s, u in m.calls if not s.startswith("你是法律文件標註助手")]
    assert compare and all(role not in compare[0] for role in ("SUPREME_COURT", "LOWER_COURT", "PARTY_CLAIM"))


def test_failed_preparation_turns_court_holding_into_uncertain_but_keeps_evidence(tmp_path):
    c = setup_db(tmp_path, "d.db", JUDGMENT, "TPSV,115,台上,7,20260101,1", "判決", "台上", 7)
    r = run_report(c, Smart(fail_prep=True))
    j = r.judgments[0]
    assert j.l2_label == L2Label.UNCERTAIN and j.l2_evidence is not None
    assert c.execute("SELECT status FROM judgment_prep").fetchone()[0] == "failed"


def test_failed_preparation_does_not_touch_other_labels(tmp_path):
    c = setup_db(tmp_path, "e.db", JUDGMENT, "TPSV,115,台上,7,20260101,1", "判決", "台上", 7)
    r = run_report(c, Smart(fail_prep=True), "按與本案無關的法律見解，最高法院115年度台上字第7號判決意旨參照。")
    assert r.judgments[0].l2_label == L2Label.NOT_IN_JUDGMENT


def test_procedural_dismissal_ruling_never_gets_court_holding(tmp_path):
    c = setup_db(tmp_path, "f.db", RULING, "TPSV,115,台抗,3,20260101,1", "裁定", "台抗", 3)
    assert c.execute("SELECT procedural_dismissal FROM judgments").fetchone()[0] == 1
    brief = "按該約款為解除條件者，其效力應依契約全文判斷，最高法院115年度台抗字第3號裁定意旨參照。"
    r = run_report(c, Smart(mark_quotes=False), brief)
    j = r.judgments[0]
    assert j.procedural_dismissal and j.l2_label == L2Label.UNCERTAIN and j.l2_evidence is not None
