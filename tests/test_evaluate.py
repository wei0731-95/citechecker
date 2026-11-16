import csv
import json
import sqlite3
from pathlib import Path

import pytest

from citecheck.db import SCHEMA, set_meta
from citecheck.evaluate import evaluate, l2_metrics, load_testset, write_results
from citecheck.extract import Lexicon

FIXTURE = Path(__file__).parent / "fixtures" / "fake_testset.jsonl"


@pytest.fixture()
def con():
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.execute("INSERT INTO laws(law_name, modified_date) VALUES('假法','20250101')")
    c.executemany("INSERT INTO articles VALUES('假法',?,?)", [("1", "fake 條文"), ("2", "（刪除）")])
    c.execute("INSERT INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, month) "
              "VALUES('TPSV,115,台上,7,20260101,1',115,'台上',7,'判決','20260101','fake','fake','202601')")
    set_meta(c, "year_min", "110")
    set_meta(c, "year_max", "115")
    set_meta(c, "months_imported", '["202601", "202602"]')
    return c


def test_eval_no_l2_matches_hand_computation(con, tmp_path):
    lex = Lexicon({"假法": "假法"}, {}, {"台上"})
    metrics, errors = evaluate(load_testset(FIXTURE), con, lex, client=None)
    # hand count: 7 gold citations; all 7 matched (SPEC_V2 A6: fake_E01 has no court name, but the
    # case word 台上 is a Supreme Court civil one, so the court is inferred and the citation extracted);
    # of the 7 matched, only fake_D01 has a wrong L1 status (民法 is not in this lexicon).
    assert metrics["n_gold_citations"] == 7 and metrics["n_matched"] == 7
    assert metrics["extraction_recall"] == {"num": 7, "den": 7, "rate": 1.0}
    assert metrics["l1_accuracy"] == {"num": 6, "den": 7, "rate": pytest.approx(6 / 7)}
    assert metrics["l2"] is None
    assert sorted((e["item_id"], e["error_type"]) for e in errors) == [("fake_D01", "L1_MISMATCH")]
    write_results(metrics, errors, tmp_path)
    assert json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))["n_matched"] == 7
    rows = list(csv.DictReader(open(tmp_path / "errors.csv", encoding="utf-8-sig")))
    assert len(rows) == 1 and {"item_id", "raw", "error_type", "gold", "system", "cause"} <= set(rows[0])


def test_l2_metrics_hand_computation():
    CH, PA, LO, CO, NI, UN = ("COURT_HOLDING", "PARTY_ARGUMENT", "LOWER_COURT_VIEW",
                              "CONTRADICTED", "NOT_IN_JUDGMENT", "UNCERTAIN")
    pairs = [(CH, CH), (CH, PA), (CH, UN),          # gold holding: 1 false alarm of 3
             (PA, PA), (PA, CH),                    # party: 1 detected of 2
             (LO, UN), (CO, CO), (NI, NI),          # lower->uncertain missed; contradicted, not-in ok
             (NI, None)]                            # not extracted counts as missed
    m = l2_metrics(pairs)
    assert m["problem_detection_recall"] == {"num": 3, "den": 6, "rate": 0.5}
    assert m["false_alarm_rate"] == {"num": 1, "den": 3, "rate": pytest.approx(1 / 3)}
    assert m["uncertain_rate"] == {"num": 2, "den": 8, "rate": 0.25}
    cm = m["confusion_matrix"]
    assert cm[CH][CH] == 1 and cm[CH][PA] == 1 and cm[PA][CH] == 1 and cm[NI]["MISSING"] == 1


def test_l2_metrics_empty_denominators():
    m = l2_metrics([])
    assert m["problem_detection_recall"]["rate"] is None and m["false_alarm_rate"]["rate"] is None
