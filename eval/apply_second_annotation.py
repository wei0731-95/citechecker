"""Merge the second (independent) annotation into eval/testset_v2.jsonl (SPEC_V2 T5).

    python eval/apply_second_annotation.py out/t5/answers.txt [--final C03=CONTRADICTED:理由 ...]

answers.txt: one "ID LABEL" per line (labels from the second annotator, who saw only the brief,
the cited case number, the full judgment and the five label definitions).
For each item: equal labels -> agreed_first_pass true. Different -> false, and the first-pass
label, the second label and the final decision (with reason) go into that citation's note. The
final label defaults to the first-pass label; --final changes it (decided after re-reading the
full judgment, before any system run).
"""
import json
import sys
from pathlib import Path

TESTSET = Path("eval/testset_v2.jsonl")


def main() -> None:
    args = sys.argv[1:]
    answers_file = Path(args[0])
    finals: dict[str, tuple[str, str]] = {}
    for a in args[1:]:
        if a.startswith("--final"):
            continue
        key, rest = a.split("=", 1)
        label, _, reason = rest.partition(":")
        finals[key] = (label, reason)
    second = dict(line.split()[:2] for line in answers_file.read_text(encoding="utf-8").splitlines() if line.strip())
    items = [json.loads(l) for l in TESTSET.read_text(encoding="utf-8").splitlines() if l.strip()]
    stats = {"total": 0, "agree": 0}
    by_cat: dict[str, list[int]] = {}
    for it in items:
        j = next((c for c in it["citations"] if c["type"] == "JUDGMENT" and c.get("gold_l2") not in (None, "NOT_RUN")), None)
        if j is None or it["id"] not in second:
            continue
        first, sec = j["gold_l2"], second[it["id"]]
        ok = first == sec
        stats["total"] += 1
        stats["agree"] += ok
        by_cat.setdefault(it["category"], [0, 0])
        by_cat[it["category"]][0] += 1
        by_cat[it["category"]][1] += ok
        it["agreed_first_pass"] = ok
        it["annotators"] = ["AI-1", "AI-2"]
        if not ok:
            final, reason = finals.get(it["id"], (first, "維持原標"))
            j["note"] = (f"{j['note']}｜第一次標註 {first}；第二次標註 {sec}；定案 {final}（{reason}）")
            j["gold_l2"] = final
    TESTSET.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    rate = stats["agree"] / stats["total"] if stats["total"] else 0
    print(f"初次一致 {stats['agree']}/{stats['total']} = {rate:.1%}")
    for cat, (n, k) in sorted(by_cat.items()):
        print(f"  {cat}: {k}/{n}")
    print("不一致題號：", [it["id"] for it in items if it.get("agreed_first_pass") is False])


if __name__ == "__main__":
    main()
