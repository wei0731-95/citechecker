"""SPEC_V2 T6 / section 12 evaluation additions. Fake data in tmp_path only."""
import json

import pytest

from citecheck.db import connect, set_meta
from citecheck.evaluate import evaluate, l2_metrics, statute_l2_metrics
from citecheck.extract import Lexicon


@pytest.fixture()
def con(tmp_path):
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO laws(law_name, modified_date, effective_note) VALUES('假法','2025','第 3 條施行日期另定')")
    c.executemany("INSERT INTO articles VALUES('假法',?,?)", [("1", "fake 條文一。"), ("3", "fake 條文三。")])
    set_meta(c, "months_imported", json.dumps(["202601", "202602"]))      # covers ROC year 115 only
    c.commit()
    return c


LEX = Lexicon({"假法": "假法"}, {}, {"台上"})


def row(id_, brief, citations, **extra):
    return {"id": id_, "category": "X", "brief_text": brief, "citations": citations, **extra}


def j(raw, status, **kw):
    return {"type": "JUDGMENT", "raw": raw, "gold_status": status, "gold_jid": None, "gold_l2": "NOT_RUN", **kw}


def s(raw, status, **kw):
    return {"type": "STATUTE", "raw": raw, "gold_status": status, "gold_law_name": "假法", "gold_article": "1", **kw}


def test_requires_covered_year_is_outside_the_l1_score(con):
    rows = [
        row("a", "最高法院115年度台上字第9號民事判決參照。",
            [j("最高法院115年度台上字第9號民事判決", "NOT_FOUND_IN_SCOPE", requires_covered_year=115)]),
        row("b", "最高法院114年度台上字第9號民事判決參照。",      # 114 is NOT covered -> skipped
            [j("最高法院114年度台上字第9號民事判決", "NOT_FOUND_IN_SCOPE", requires_covered_year=114)]),
    ]
    m, errors = evaluate(rows, con, LEX)
    assert m["skipped_not_covered"] == 1
    assert m["l1_accuracy"] == {"num": 1, "den": 1, "rate": 1.0}
    assert errors == []


def test_not_in_force_other_authority_and_unrecognized_counts(con):
    rows = [
        row("n1", "依假法第3條規定。", [s("假法第3條", "FOUND", gold_article="3", gold_not_in_force=True)]),
        row("n2", "依假法第1條規定。", [s("假法第1條", "FOUND", gold_not_in_force=False)]),
        row("o1", "見司法院釋字第748號解釋。", [], gold_other_authorities=["司法院釋字第748號解釋"]),
        row("o2", "見司法院釋字第999號解釋。", [], gold_other_authorities=["最高法院1年度第1次民事庭會議決議"]),
        row("u1", "最高院（109）台上字第123號判決參照。", [], gold_unrecognized=True),
        row("u2", "最高法院109年度台上字第123號判決參照。", [], gold_unrecognized=True),
    ]
    m, _ = evaluate(rows, con, LEX)
    assert m["not_in_force_match"] == {"num": 2, "den": 2, "rate": 1.0}
    assert m["other_authorities_match"] == {"num": 1, "den": 2, "rate": 0.5}
    assert m["unrecognized_match"] == {"num": 1, "den": 2, "rate": 0.5}


def test_l2_metrics_green_precision_valid_rate_and_label_accuracy():
    CH, PA, UN = "COURT_HOLDING", "PARTY_ARGUMENT", "UNCERTAIN"
    pairs = [(CH, CH), (PA, CH), (CH, UN), (PA, PA), ("NOT_RUN", "NOT_RUN"), (CH, None)]
    m = l2_metrics(pairs)
    assert m["green_precision"] == {"num": 1, "den": 2, "rate": 0.5}       # system said CH twice, one right
    assert m["valid_judgment_rate"] == {"num": 3, "den": 4, "rate": 0.75}  # matched, gold != NOT_RUN: 4; one UNCERTAIN
    assert m["label_accuracy"] == {"num": 2, "den": 4, "rate": 0.5}


def test_statute_l2_metrics():
    pairs = [("MISMATCH", "MISMATCH"), ("MISMATCH", "MATCHES"), ("MATCHES", "MISMATCH"), ("NOT_STATED", "NOT_STATED"),
             ("MATCHES", "MATCHES"), ("MISMATCH", None)]
    m = statute_l2_metrics(pairs)
    assert m["mismatch_detection_rate"] == {"num": 1, "den": 3, "rate": pytest.approx(1 / 3)}
    assert m["false_alarm_rate"] == {"num": 1, "den": 3, "rate": pytest.approx(1 / 3)}
    assert m["confusion_matrix"]["MISMATCH"]["MISSING"] == 1
