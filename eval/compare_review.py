"""Compare a reviewer's independent labels with the gold labels in eval/testset.jsonl.

    python eval/compare_review.py eval/review/review_answers.json [--apply]

Prints the first-pass agreement rate and every disagreement. It never changes a gold label.
With --apply it only records annotators / agreed_first_pass on each reviewed item; items
where the two disagree must then be discussed and fixed by hand.
"""
import json
import sys
from pathlib import Path

TESTSET = Path("eval/testset.jsonl")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: python eval/compare_review.py <review_answers.json> [--apply]")
    review = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    answers, notes, who = review["answers"], review.get("notes", {}), review.get("reviewer", "reviewer")
    items = [json.loads(l) for l in TESTSET.read_text(encoding="utf-8").splitlines() if l.strip()]
    agree = total = 0
    rows = []
    for it in items:
        j = next(c for c in it["citations"] if c["type"] == "JUDGMENT")
        if j["gold_l2"] == "NOT_RUN":
            continue
        got = answers.get(it["id"])
        if got is None:
            print(f"（未作答）{it['id']}")
            continue
        total += 1
        ok = got == j["gold_l2"]
        agree += ok
        it["_ok"] = ok
        if not ok:
            rows.append((it["id"], j["gold_l2"], got, notes.get(it["id"], ""), it["brief_text"]))
    rate = agree / total if total else 0
    print(f"初次一致率：{agree}/{total} = {rate:.1%}（指南：85% 以上繼續；低於 75% 先修題目定義）")
    for id_, gold, got, note, brief in rows:
        print(f"\n[{id_}] 原標：{gold}　複核者：{got}\n  書狀：{brief}\n  備註：{note or '—'}")
    if "--apply" in sys.argv:
        for it in items:
            if "_ok" in it:
                it["annotators"] = ["Claude(AI)", who]
                it["agreed_first_pass"] = it.pop("_ok")
        TESTSET.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
        print("\n已寫入 annotators / agreed_first_pass（金標未更動）。")


if __name__ == "__main__":
    main()
