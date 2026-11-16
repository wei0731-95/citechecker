"""SPEC_V2 Phase F tests: build-index (F1), ensure_prepared (F2), dedupe / parallel / cap (F3)."""
import json
import threading
import time

import pytest

from citecheck import judge, prep
from citecheck.db import connect, set_meta
from citecheck.evaluate import gold_chunks, passage_hits_gold
from citecheck.extract import Lexicon
from citecheck.index import (IndexNotReady, build_index, check_index_ready, is_procedural_dismissal,
                             load_passages, main_text_of)
from citecheck.llm import CachingClient, MockClient
from citecheck.models import L2Label
from citecheck.prep import (PREP_SYSTEM_PROMPT, build_prep_prompt, ensure_prepared, load_quotes,
                            parse_prep_response, split_sentences)
from citecheck.report import build_report
from citecheck.segment import SEGMENT_RULES_VERSION

FULL = ("最高法院民事判決\n115年度台上字第7號\n上  訴  人  甲\n    主  文\n上訴駁回。\n    理  由\n"
        "一、本件被上訴人主張：伊與上訴人訂立買賣契約，上訴人未依約履行，致伊受有損害等情。\n"
        "二、原審以：契約有效，上訴人應負賠償責任，因而判決上訴人敗訴。\n"
        "三、本院之判斷：按出賣人就買賣標的物有瑕疵時應負擔保責任。原審雖認契約有效，惟其認定並無違誤。\n"
        "四、查買受人主張瑕疵者，應就瑕疵負舉證責任。本件被上訴人已舉證。\n"
        "五、據上論結，本件上訴為無理由。\n"
        "中　　華　　民　　國　　115 　年　　3 　　月　　31　　日\n")


def add_judgment(con, jid, full=FULL, kind="判決", year=115, no=7):
    con.execute("INSERT INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, month) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (jid, year, "台上", no, kind, "20260101", "fake", full, "202601"))
    con.commit()


@pytest.fixture()
def con(tmp_path):
    c = connect(tmp_path / "t.db")
    add_judgment(c, "TPSV,115,台上,7,20260101,1")
    set_meta(c, "months_imported", json.dumps(["202601", "202602"]))
    c.commit()
    return c


JID = "TPSV,115,台上,7,20260101,1"


# ---------------------------------------------------------------- F1
def test_build_index_is_idempotent(con):
    r1 = build_index(con)
    n_seg = con.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
    n_pas = con.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
    assert r1["indexed_now"] == 1 and n_seg > 0 and n_pas > 0
    assert con.execute("SELECT MIN(seq) FROM passages WHERE jid=?", (JID,)).fetchone()[0] == 0
    r2 = build_index(con)
    assert r2["indexed_now"] == 0
    assert con.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == n_seg
    assert con.execute("SELECT COUNT(*) FROM passages").fetchone()[0] == n_pas


def test_build_index_resumes_and_skips_done(con):
    add_judgment(con, "TPSV,115,台上,8,20260101,1", no=8)
    build_index(con)
    add_judgment(con, "TPSV,115,台上,9,20260101,1", no=9)
    r = build_index(con)
    assert r["indexed_now"] == 1 and r["already_indexed"] == 2


def test_build_index_rebuilds_when_version_differs_and_clears_prep(con):
    build_index(con)
    con.execute("INSERT INTO judgment_prep VALUES(?, 'ok', 'm', 1, 1, 'now')", (JID,))
    con.execute("INSERT INTO quoted_sentences VALUES(?, 0, 0, 'PARTY')", (JID,))
    set_meta(con, "segment_rules_version", str(SEGMENT_RULES_VERSION + 1))
    con.commit()
    r = build_index(con)
    assert r["rebuilt"] and r["indexed_now"] == 1
    assert con.execute("SELECT COUNT(*) FROM judgment_prep").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM quoted_sentences").fetchone()[0] == 0
    assert dict(con.execute("SELECT key, value FROM meta").fetchall())["segment_rules_version"] == str(SEGMENT_RULES_VERSION)


def test_build_index_rebuild_flag_forces_redo(con):
    build_index(con)
    assert build_index(con, rebuild=True)["indexed_now"] == 1


def test_passages_are_loaded_in_order_with_roles(con):
    build_index(con)
    ps = load_passages(con, JID)
    assert [p.role.value for p in ps][:1] and all(len(p.text) <= 400 for p in ps)
    assert any(p.role.value == "SUPREME_COURT" for p in ps)


def test_index_not_ready_before_build(con):
    with pytest.raises(IndexNotReady, match="請先執行 citecheck build-index"):
        check_index_ready(con, [JID])
    build_index(con)
    check_index_ready(con, [JID])
    with pytest.raises(IndexNotReady):
        check_index_ready(con, ["TPSV,115,台上,99,20260101,1"])


def test_main_text_comes_from_segments(con):
    assert main_text_of(con, JID) is None
    build_index(con)
    assert main_text_of(con, JID) == "上訴駁回。"


@pytest.mark.parametrize("tail,expected", [
    ("據上論結，本件上訴為不合法。依民事訴訟法第481條、第444條第1項、第78條，裁定如主文。", True),
    ("據上論結，本件再抗告為不合法。依民事訴訟法第495條之1，裁定如主文。", True),
    ("據上論結，本件聲請為不合法。依民事訴訟法第507條，裁定如主文。", True),
    ("據上論結，本件上訴為無理由。依民事訴訟法第481條，判決如主文。", False),
    ("據上論結，本件上訴為有理由。依民事訴訟法第477條第1項，判決如主文。", False),
])
def test_procedural_dismissal_rule(tail, expected):
    kind = "裁定" if "裁定如主文" in tail else "判決"
    assert is_procedural_dismissal(kind, "理由\n一、說明。\n" + tail) is expected
    assert is_procedural_dismissal("判決", "據上論結，本件上訴為不合法。") is False      # only rulings


def test_procedural_dismissal_is_stored(con):
    add_judgment(con, "TPSV,115,台抗,3,20260101,1",
                 "最高法院民事裁定\n115年度台抗字第3號\n    主  文\n抗告駁回。\n    理  由\n一、說明。\n"
                 "據上論結，本件抗告為不合法。裁定如主文。\n", kind="裁定", no=3)
    build_index(con)
    assert con.execute("SELECT procedural_dismissal FROM judgments WHERE jid='TPSV,115,台抗,3,20260101,1'").fetchone()[0] == 1
    assert con.execute("SELECT procedural_dismissal FROM judgments WHERE jid=?", (JID,)).fetchone()[0] == 0


# ---------------------------------------------------------------- F2
def quoted(*pairs):
    return json.dumps({"quoted": [{"id": i, "speaker": s} for i, s in pairs]})


def test_split_sentences():
    assert split_sentences("甲。乙。丙") == ["甲。", "乙。", "丙"]
    assert split_sentences("甲。 \n乙。") == ["甲。", " \n乙。"] or split_sentences("甲。 \n乙。") == ["甲。", "乙。"]
    assert split_sentences("  ") == [] and split_sentences("") == []


def test_prep_prompt_is_verbatim():
    assert prep.PREP_PROMPT_VERSION == 1
    assert PREP_SYSTEM_PROMPT.startswith("你是法律文件標註助手。你會收到最高法院一件民事裁判中的數個編號句子。")
    assert PREP_SYSTEM_PROMPT.endswith('沒有任何符合的句子時輸出 {"quoted":[]}。')
    assert build_prep_prompt([(1, "甲。"), (2, "乙。")]) == (
        "裁判句子：\n[1] 甲。\n[2] 乙。\n\n請輸出：\n"
        '{"quoted":[{"id":1,"speaker":"LOWER_COURT"},{"id":2,"speaker":"PARTY"}]}')


@pytest.mark.parametrize("raw", [
    "", "not json", '{"quoted":[{"id":1,"speaker":"JUDGE"}]}', '{"quoted":[{"id":9,"speaker":"PARTY"}]}',
    '{"quoted":[{"id":0,"speaker":"PARTY"}]}',
    '{"quoted":[{"id":1,"speaker":"PARTY"},{"id":1,"speaker":"LOWER_COURT"}]}', '{"other":[]}'])
def test_prep_response_validation_rejects(raw):
    with pytest.raises(ValueError):
        parse_prep_response(raw, 1, 3)


def test_prep_response_accepts_empty_list_and_fences():
    assert parse_prep_response('{"quoted":[]}', 1, 3) == {}
    assert parse_prep_response("```json\n" + quoted((2, "PARTY")) + "\n```", 1, 3) == {2: "PARTY"}


def test_ensure_prepared_success_stores_quotes_and_row(con):
    build_index(con)
    m = MockClient(quoted((1, "LOWER_COURT")))
    assert ensure_prepared(JID, con, m) is True
    assert len(m.calls) == 1 and m.max_tokens == [2000]
    row = con.execute("SELECT status, prompt_version, segment_rules_version FROM judgment_prep WHERE jid=?", (JID,)).fetchone()
    assert row == ("ok", prep.PREP_PROMPT_VERSION, SEGMENT_RULES_VERSION)
    q = load_quotes(con, JID)
    assert list(q.values()) == ["LOWER_COURT"] and all(k[1] >= 0 for k in q)


def test_ensure_prepared_already_done_makes_no_call(con):
    build_index(con)
    ensure_prepared(JID, con, MockClient(quoted((1, "PARTY"))))
    m = MockClient("never used")
    assert ensure_prepared(JID, con, m) is True and m.calls == []


def test_ensure_prepared_redone_when_prompt_or_rules_version_changed(con, monkeypatch):
    build_index(con)
    ensure_prepared(JID, con, MockClient(quoted()))
    monkeypatch.setattr(prep, "PREP_PROMPT_VERSION", 2)
    m = MockClient(quoted())
    assert ensure_prepared(JID, con, m) and len(m.calls) == 1


def test_ensure_prepared_parse_failure_retries_once(con, tmp_path, monkeypatch):
    monkeypatch.setenv("CITECHECK_FAILURE_LOG", str(tmp_path / "f.log"))
    build_index(con)
    m = MockClient(["garbage", quoted((1, "PARTY"))])
    assert ensure_prepared(JID, con, m) is True and len(m.calls) == 2
    assert (tmp_path / "f.log").exists()


def test_ensure_prepared_two_failures_write_failed_and_no_quotes(con):
    build_index(con)
    con.execute("INSERT INTO quoted_sentences VALUES(?, 0, 0, 'PARTY')", (JID,))
    con.commit()
    m = MockClient(["garbage", "still garbage"])
    assert ensure_prepared(JID, con, m) is False and len(m.calls) == 2
    assert con.execute("SELECT status FROM judgment_prep WHERE jid=?", (JID,)).fetchone()[0] == "failed"
    assert con.execute("SELECT COUNT(*) FROM quoted_sentences WHERE jid=?", (JID,)).fetchone()[0] == 1   # untouched
    m2 = MockClient(quoted())                                                                           # retried later
    assert ensure_prepared(JID, con, m2) is True and len(m2.calls) == 1


def test_ensure_prepared_without_supreme_passage_makes_no_call(con):
    add_judgment(con, "TPSV,115,台上,8,20260101,1",
                 "最高法院民事判決\n115年度台上字第8號\n    主  文\n上訴駁回。\n    理  由\n"
                 "一、本件被上訴人主張：甲。\n", no=8)
    build_index(con)
    m = MockClient("never")
    assert ensure_prepared("TPSV,115,台上,8,20260101,1", con, m) is True and m.calls == []
    assert con.execute("SELECT status FROM judgment_prep WHERE jid='TPSV,115,台上,8,20260101,1'").fetchone()[0] == "ok"


def test_ensure_prepared_batches_whole_passages(con, monkeypatch):
    monkeypatch.setattr(prep, "BATCH_CHARS", 25)         # tiny: every passage becomes its own batch
    build_index(con)
    supreme = [p for p in load_passages(con, JID) if p.role.value == "SUPREME_COURT"]
    assert len(supreme) >= 2
    m = MockClient(quoted())
    assert ensure_prepared(JID, con, m) is True
    assert len(m.calls) == len(supreme)
    firsts = [c[1].split("\n")[1].split("]")[0] for c in m.calls]       # "[n] ..." of each batch
    assert len(set(firsts)) == len(firsts)                              # numbering continues across batches
    assert firsts[0] == "[1"


def test_ensure_prepared_rejects_id_outside_the_batch(con, monkeypatch):
    monkeypatch.setattr(prep, "BATCH_CHARS", 25)
    build_index(con)
    m = MockClient([quoted((99, "PARTY"))] * 4)
    assert ensure_prepared(JID, con, m) is False


# ---------------------------------------------------------------- F3
class CountingClient(MockClient):
    pass


def test_run_limited_caps_and_reports_skipped():
    m = MockClient("x")

    def fn(i):
        m.complete("s", "u")
        return i * 2

    res, skipped = judge.run_limited([1, 2, 3, 4, 5], fn, m, max_workers=1, cap=3)
    assert res[:3] == [2, 4, 6] and skipped == [3, 4] and len(m.calls) == 3


def test_run_limited_never_exceeds_parallelism():
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fn(i):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.03)
        with lock:
            state["now"] -= 1
        return i

    res, skipped = judge.run_limited(list(range(10)), fn, MockClient("x"), max_workers=4, cap=1000)
    assert res == list(range(10)) and skipped == [] and 1 < state["max"] <= 4


def test_caching_client_counts_calls_and_hits(tmp_path):
    c = CachingClient(MockClient("a"), "m", tmp_path)
    c.complete("s", "u")
    c.complete("s", "u")
    c.complete("s", "u2")
    assert (c.api_calls, c.cache_hits) == (2, 1)


LEX = Lexicon({}, {}, {"台上"})


def compare_reply():
    return json.dumps({"results": [{"id": i, "relation": "SUPPORTS", "confidence": 0.9} for i in range(1, 6)]})


def indexed(con):
    build_index(con)
    return con


def test_same_claim_and_jid_is_compared_once(con):
    indexed(con)
    brief = "按出賣人就買賣標的物有瑕疵時應負擔保責任，最高法院115年度台上字第7號判決及最高法院115年度台上字第7號判決意旨參照。"
    m = MockClient([quoted(), compare_reply()])            # call 1 = preparation, call 2 = the comparison
    r = build_report(brief, "x", con, LEX, m)
    assert len(r.judgments) == 2 and len(m.calls) == 2
    assert r.judgments[0].l2_label == r.judgments[1].l2_label != L2Label.NOT_RUN
    assert r.stats.api_calls == 2 and r.stats.prep_new == 1


def test_api_call_cap_skips_the_rest_and_counts_them(con, monkeypatch):
    add_judgment(con, "TPSV,115,台上,8,20260101,1", no=8)
    add_judgment(con, "TPSV,115,台上,9,20260101,1", no=9)
    indexed(con)
    monkeypatch.setattr(judge, "MAX_API_CALLS_PER_CHECK", 1)
    monkeypatch.setattr(judge, "MAX_PARALLEL_CALLS", 1)
    brief = ("按甲，最高法院115年度台上字第7號判決參照。又按乙，最高法院115年度台上字第8號判決參照。"
             "再按丙，最高法院115年度台上字第9號判決參照。")
    m = MockClient([quoted(), compare_reply()])
    r = build_report(brief, "x", con, LEX, m)
    assert len(m.calls) == 1                                   # the cap stopped everything after one call
    assert r.stats.skipped_by_cap >= 2
    assert [j.l2_label for j in r.judgments].count(L2Label.NOT_RUN) >= 2


def test_apply_l2_raises_before_any_call_when_index_missing(con):
    m = MockClient("never")
    brief = "按甲，最高法院115年度台上字第7號判決參照。"
    with pytest.raises(IndexNotReady):
        build_report(brief, "x", con, LEX, m)
    assert m.calls == []


def test_report_records_versions_model_and_timing(con):
    indexed(con)
    r = build_report("按甲，最高法院115年度台上字第7號判決參照。", "x", con, LEX, None)
    assert r.versions == {"segment_rules": SEGMENT_RULES_VERSION, "prep_prompt": 1, "statute_prompt": 1, "schema": 2}
    assert r.llm_model is None and r.stats.api_calls == 0 and r.stats.seconds >= 0


# ---------------------------------------------------------------- eval-retrieval helpers
def test_gold_chunks_and_hit_rule():
    gold = "甲" * 15 + "乙" * 15 + "丙" * 15
    chunks = gold_chunks(gold + "…")
    assert chunks == ["甲" * 15, "乙" * 15, "丙" * 15]
    assert passage_hits_gold("前文" + "甲" * 15 + "後文", chunks)              # 1 of 3 chunks is enough
    assert not passage_hits_gold("毫不相干的段落", chunks)
    assert passage_hits_gold("短", ["短"]) and not passage_hits_gold("長", [])


# ------------------------------------------------------------------ SPEC_V3 H2
class _NamedMock(MockClient):
    def __init__(self, responses, model):
        super().__init__(responses)
        self.model = model


def test_h2_prep_by_another_model_is_redone_then_reused(con, monkeypatch):
    monkeypatch.delenv("CITECHECK_MODEL", raising=False)
    build_index(con)
    assert ensure_prepared(JID, con, _NamedMock(quoted(), "A")) is True
    assert con.execute("SELECT model FROM judgment_prep WHERE jid=?", (JID,)).fetchone()[0] == "A"
    b = _NamedMock(quoted((1, "PARTY")), "B")
    assert ensure_prepared(JID, con, b) is True and len(b.calls) == 1
    assert con.execute("SELECT model FROM judgment_prep WHERE jid=?", (JID,)).fetchone()[0] == "B"
    assert list(load_quotes(con, JID).values()) == ["PARTY"]
    b2 = _NamedMock("never used", "B")
    assert ensure_prepared(JID, con, b2) is True and b2.calls == []


def test_h2_current_model_prefers_env(monkeypatch):
    monkeypatch.setenv("CITECHECK_MODEL", "env-model")
    assert prep.current_model(_NamedMock("x", "client-model")) == "env-model"
    monkeypatch.delenv("CITECHECK_MODEL")
    assert prep.current_model(_NamedMock("x", "client-model")) == "client-model"
    assert prep.current_model(MockClient("x")) is None
