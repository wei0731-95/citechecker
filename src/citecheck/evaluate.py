"""Evaluation against a hand-labelled test set (SPEC 9, docs/TESTSET_GUIDE.md)."""
import csv
import json
import sqlite3
from pathlib import Path

from .extract import Lexicon
from .models import L2Label
from .normalize import normalize
from .report import build_report
from .verify import covered_years, imported_months

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
    green = [(g, s) for g, s in pairs if s == "COURT_HOLDING"]       # SPEC_V2 section 12
    cols = [l.value for l in L2Label] + ["MISSING"]
    confusion = {g: {c: 0 for c in cols} for g in L2_GOLD_ORDER}
    for g, s in pairs:
        if g in confusion:
            confusion[g][s if s is not None else "MISSING"] += 1
    return {"problem_detection_recall": ratio(detected, len(problem)),
            "false_alarm_rate": ratio(false_alarm, len(holding)),
            "uncertain_rate": ratio(uncertain, len(matched)),
            "green_precision": ratio(sum(1 for g, _ in green if g == "COURT_HOLDING"), len(green)),
            "valid_judgment_rate": ratio(sum(1 for _, s in matched if s != "UNCERTAIN"), len(matched)),
            "label_accuracy": ratio(sum(1 for g, s in matched if g == s), len(matched)),
            "confusion_matrix": confusion}


STATUTE_L2_LABELS = ["MATCHES", "MISMATCH", "NOT_STATED", "UNCERTAIN", "NOT_RUN"]


def statute_l2_metrics(pairs: list[tuple[str, str | None]]) -> dict:
    """pairs: (gold_statute_l2, system label or None when not extracted)."""
    mism = [(g, s) for g, s in pairs if g == "MISMATCH"]
    ok = [(g, s) for g, s in pairs if g in ("MATCHES", "NOT_STATED")]
    cm = {g: {c: 0 for c in STATUTE_L2_LABELS + ["MISSING"]} for g in STATUTE_L2_LABELS[:3]}
    for g, s in pairs:
        if g in cm:
            cm[g][s if s is not None else "MISSING"] += 1
    return {"mismatch_detection_rate": ratio(sum(1 for _, s in mism if s == "MISMATCH"), len(mism)),
            "false_alarm_rate": ratio(sum(1 for _, s in ok if s == "MISMATCH"), len(ok)),
            "confusion_matrix": cm}


def evaluate(rows: list[dict], con: sqlite3.Connection, lex: Lexicon, client=None):
    covered = covered_years(imported_months(con))
    n_gold = n_matched = l1_ok = l1_total = skipped = 0
    l2_pairs: list[tuple[str, str | None]] = []
    st_pairs: list[tuple[str, str | None]] = []
    nif = [0, 0]      # gold_not_in_force: matched / total
    oth = [0, 0]      # gold_other_authorities
    unrec = [0, 0]    # gold_unrecognized
    errors: list[dict] = []

    def err(row, g, kind, gold, system):
        errors.append({"item_id": row["id"], "category": row.get("category"), "type": g["type"],
                       "raw": g["raw"], "error_type": kind, "gold": gold, "system": system, "cause": ""})

    for row in rows:
        brief = row["brief_text"]
        report = build_report(brief, row["id"], con, lex, client)
        for g in row["citations"]:
            n_gold += 1
            span = _locate(g["raw"], report.normalized_text, brief)
            is_j = g["type"] == "JUDGMENT"
            system = report.judgments if is_j else report.statutes
            hit = _match(span, g["type"], system) if span else None
            gold_l2 = g.get("gold_l2") if is_j else None
            gold_st = g.get("gold_statute_l2") if not is_j else None
            if not is_j and "gold_not_in_force" in g:
                nif[1] += 1
                nif[0] += int(hit is not None and hit.not_in_force_note == bool(g["gold_not_in_force"]))
            if hit is None:
                err(row, g, "EXTRACTION_MISS", g["gold_status"], "")
                if gold_l2:
                    l2_pairs.append((gold_l2, None))
                if gold_st:
                    st_pairs.append((gold_st, None))
                continue
            n_matched += 1
            if g.get("requires_covered_year") and g["requires_covered_year"] not in covered:
                skipped += 1                       # T6: year not covered -> outside the L1 score
            else:
                l1_total += 1
                if hit.status.value == g["gold_status"]:
                    l1_ok += 1
                else:
                    err(row, g, "L1_MISMATCH", g["gold_status"], hit.status.value)
            if is_j and gold_l2:
                l2_pairs.append((gold_l2, hit.l2_label.value))
                if client is not None and hit.l2_label.value != gold_l2:
                    err(row, g, "L2_MISMATCH", gold_l2, hit.l2_label.value)
            if gold_st:
                st_pairs.append((gold_st, hit.l2_label.value))
                if client is not None and hit.l2_label.value != gold_st:
                    err(row, g, "STATUTE_L2_MISMATCH", gold_st, hit.l2_label.value)
        for raw in row.get("gold_other_authorities", []):
            oth[1] += 1
            sp = _locate(raw, report.normalized_text, brief)
            oth[0] += int(bool(sp) and any(a.span.start < sp[1] and sp[0] < a.span.end
                                           for a in report.other_authorities))
        if "gold_unrecognized" in row:
            unrec[1] += 1
            unrec[0] += int(bool(report.unrecognized) == bool(row["gold_unrecognized"]))
    metrics = {"n_items": len(rows), "n_gold_citations": n_gold, "n_matched": n_matched,
               "extraction_recall": ratio(n_matched, n_gold),
               "l1_accuracy": ratio(l1_ok, l1_total),
               "skipped_not_covered": skipped,
               "not_in_force_match": ratio(*nif),
               "other_authorities_match": ratio(*oth),
               "unrecognized_match": ratio(*unrec),
               "l2": l2_metrics(l2_pairs) if client is not None else None,
               "statute_l2": statute_l2_metrics(st_pairs) if client is not None else None,
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
