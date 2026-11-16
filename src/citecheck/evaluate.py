"""Evaluation against a hand-labelled test set (SPEC 9, docs/TESTSET_GUIDE.md)."""
import csv
import json
import sqlite3
from pathlib import Path

from .extract import Lexicon
from .models import L2Label
from .normalize import normalize
from .report import build_report

PROBLEM_LABELS = {"PARTY_ARGUMENT", "LOWER_COURT_VIEW", "CONTRADICTED", "NOT_IN_JUDGMENT"}
L2_GOLD_ORDER = ["COURT_HOLDING", "PARTY_ARGUMENT", "LOWER_COURT_VIEW", "CONTRADICTED",
                 "NOT_IN_JUDGMENT", "NOT_RUN"]


def load_testset(path: Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def ratio(num: int, den: int) -> dict:
    return {"num": num, "den": den, "rate": (num / den) if den else None}


def _locate(raw: str, norm_text: str, brief: str) -> tuple[int, int] | None:
    n = normalize(raw)
    i = norm_text.find(n)
    if i >= 0:
        return i, i + len(n)
    j = brief.find(raw)  # fallback: normalise the prefix to translate the offset
    if j >= 0:
        start = len(normalize(brief[:j]))
        return start, start + len(n)
    return None


def _match(gold_span, gold_type, system):
    best, best_ov = None, 0
    for c in system:
        if c.type != gold_type:
            continue
        ov = min(gold_span[1], c.span.end) - max(gold_span[0], c.span.start)
        if ov > best_ov:
            best, best_ov = c, ov
    return best


def l2_metrics(pairs: list[tuple[str, str | None]]) -> dict:
    """pairs: (gold_l2, system_l2 or None when the citation was not extracted)."""
    problem = [(g, s) for g, s in pairs if g in PROBLEM_LABELS]
    detected = sum(1 for _, s in problem if s is not None and s not in ("COURT_HOLDING", "UNCERTAIN"))
    holding = [(g, s) for g, s in pairs if g == "COURT_HOLDING"]
    false_alarm = sum(1 for _, s in holding if s in PROBLEM_LABELS)
    matched = [(g, s) for g, s in pairs if s is not None and g != "NOT_RUN"]
    uncertain = sum(1 for _, s in matched if s == "UNCERTAIN")
    cols = [l.value for l in L2Label] + ["MISSING"]
    confusion = {g: {c: 0 for c in cols} for g in L2_GOLD_ORDER}
    for g, s in pairs:
        if g in confusion:
            confusion[g][s if s is not None else "MISSING"] += 1
    return {"problem_detection_recall": ratio(detected, len(problem)),
            "false_alarm_rate": ratio(false_alarm, len(holding)),
            "uncertain_rate": ratio(uncertain, len(matched)),
            "confusion_matrix": confusion}


def evaluate(rows: list[dict], con: sqlite3.Connection, lex: Lexicon, client=None):
    n_gold = n_matched = l1_ok = 0
    l2_pairs: list[tuple[str, str | None]] = []
    errors: list[dict] = []
    for row in rows:
        brief = row["brief_text"]
        report = build_report(brief, row["id"], con, lex, client)
        for g in row["citations"]:
            n_gold += 1
            span = _locate(g["raw"], report.normalized_text, brief)
            system = report.statutes if g["type"] == "STATUTE" else report.judgments
            hit = _match(span, g["type"], system) if span else None
            is_j = g["type"] == "JUDGMENT"
            gold_l2 = g.get("gold_l2") if is_j else None
            if hit is None:
                errors.append({"item_id": row["id"], "category": row.get("category"), "type": g["type"],
                               "raw": g["raw"], "error_type": "EXTRACTION_MISS",
                               "gold": g["gold_status"], "system": "", "cause": ""})
                if gold_l2:
                    l2_pairs.append((gold_l2, None))
                continue
            n_matched += 1
            if hit.status.value == g["gold_status"]:
                l1_ok += 1
            else:
                errors.append({"item_id": row["id"], "category": row.get("category"), "type": g["type"],
                               "raw": g["raw"], "error_type": "L1_MISMATCH",
                               "gold": g["gold_status"], "system": hit.status.value, "cause": ""})
            if is_j and gold_l2:
                l2_pairs.append((gold_l2, hit.l2_label.value))
                if client is not None and hit.l2_label.value != gold_l2:
                    errors.append({"item_id": row["id"], "category": row.get("category"), "type": g["type"],
                                   "raw": g["raw"], "error_type": "L2_MISMATCH",
                                   "gold": gold_l2, "system": hit.l2_label.value, "cause": ""})
    metrics = {"n_items": len(rows), "n_gold_citations": n_gold, "n_matched": n_matched,
               "extraction_recall": ratio(n_matched, n_gold),
               "l1_accuracy": ratio(l1_ok, n_matched),
               "l2": l2_metrics(l2_pairs) if client is not None else None,
               "note": "L2 未執行（--no-l2）" if client is None else ""}
    return metrics, errors


def write_results(metrics: dict, errors: list[dict], out_dir: Path) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    cols = ["item_id", "category", "type", "raw", "error_type", "gold", "system", "cause"]
    with open(out_dir / "errors.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(errors)
