import json
from pathlib import Path

from citecheck.db import connect
from citecheck.ingest_judgments import doc_kind, ingest_judgments
from citecheck.ingest_laws import ingest_laws

FX = Path(__file__).parent / "fixtures"


def _make_judgments(root: Path):
    rows = [
        ("最高法院民事", "TPSV,110,台上,1,20260105,1", "最高法院民事判決  110年度台上字第1號"),
        ("最高法院民事", "TPSV,115,台上,2,20260110,1", "最高法院民事判決  115年度台上字第2號"),
        ("最高法院民事", "TPSV,115,台上,2,20260110,2", "最高法院民事裁定  115年度台上字第2號"),
        ("最高法院民事", "TPSV,115,台上大,3,20260111,1", "最高法院民事大法庭裁定  115年度台上大字第3號"),
        ("最高法院刑事", "TPSM,115,台上,9,20260112,1", "最高法院刑事判決  fake"),
    ]
    for d, jid, full in rows:
        p = jid.split(",")
        (root / "202601" / d).mkdir(parents=True, exist_ok=True)
        j = {"JID": jid, "JYEAR": p[1], "JCASE": p[2], "JNO": p[3], "JDATE": p[4],
             "JTITLE": "fake_測試", "JFULL": full, "JPDF": ""}
        (root / "202601" / d / f"{jid}.json").write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")


def test_doc_kind():
    assert doc_kind("最高法院民事判決  1") == "判決"
    assert doc_kind("最高法院民事大法庭裁定  1") == "裁定"
    assert doc_kind("最高法院民事提案裁定 1") == "裁定"
    assert doc_kind("") is None


def test_ingest_laws(tmp_path):
    db = tmp_path / "t.db"
    r = ingest_laws(FX / "fake_laws", db)
    assert r == {"laws": 2, "repealed": 1, "articles": 5}   # A11: repealed laws are kept
    con = connect(db)
    assert con.execute("SELECT text FROM articles WHERE law_name='假法' AND article='15-1'").fetchone()[0] == "乙條文。"
    assert con.execute("SELECT repealed FROM laws WHERE law_name='假廢止法'").fetchone()[0] == 1
    assert con.execute("SELECT text FROM articles WHERE law_name='假增修條文'").fetchone()[0] == "y"


def test_ingest_laws_idempotent(tmp_path):
    db = tmp_path / "t.db"
    ingest_laws(FX / "fake_laws", db)
    ingest_laws(FX / "fake_laws", db)
    assert connect(db).execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 5


def test_ingest_judgments(tmp_path):
    src = tmp_path / "src"
    _make_judgments(src)
    db = tmp_path / "t.db"
    assert ingest_judgments(src, db)["judgments"] == 4
    ingest_judgments(src, db)  # rerun: no duplicates
    con = connect(db)
    assert con.execute("SELECT COUNT(*) FROM judgments").fetchone()[0] == 4
    assert con.execute("SELECT COUNT(*) FROM judgments WHERE jid LIKE 'TPSM%'").fetchone()[0] == 0
    assert dict(con.execute("SELECT key,value FROM meta").fetchall())["year_min"] == "110"
    assert con.execute("SELECT doc_kind FROM judgments WHERE case_word='台上大'").fetchone()[0] == "裁定"
