"""Build eval/candidates.md: a worksheet that helps annotators PICK source judgments and
statute cases for the 30-question test set (docs/TESTSET_GUIDE.md).

It only suggests candidates. Roles come from the rule-based segmenter, so annotators must
re-read the full judgment before fixing any gold label. Run:
    python eval/make_candidates.py
"""
import random
import re
import sqlite3
from pathlib import Path

from citecheck.models import SegmentRole as R
from citecheck.segment import segment

SEED = 42
DB = Path("data/citecheck.db")
OUT = Path("eval/candidates.md")


def clip(t: str, n: int = 150) -> str:
    return re.sub(r"\s+", " ", t)[:n] + ("…" if len(t) > n else "")


def first(segs, role, min_len=60):
    for s in segs:
        if s.part == "reason" and s.role == role and len(s.text) >= min_len:
            return s
    return None


def main() -> None:
    con = sqlite3.connect(DB)
    rows = con.execute("SELECT jid, title, full_text FROM judgments WHERE doc_kind='判決' "
                       "AND year>=114 ORDER BY jid").fetchall()
    random.Random(SEED).shuffle(rows)
    reverse, dismiss = [], []
    for jid, title, text in rows:
        segs = segment(text)
        main_txt = " ".join(s.text for s in segs if s.part == "main")
        sup, party, lower = (first(segs, R.SUPREME_COURT), first(segs, R.PARTY_CLAIM),
                             first(segs, R.LOWER_COURT))
        if not (sup and party and lower):
            continue
        item = (jid, title, main_txt, sup, party, lower)
        if "廢棄" in main_txt and len(reverse) < 12:
            reverse.append(item)
        elif "上訴駁回" in main_txt and len(dismiss) < 12:
            dismiss.append(item)
        if len(reverse) >= 12 and len(dismiss) >= 12:
            break

    out = ["# 測試集候選清單（輔助挑題用）", "",
           "> 角色（本院／當事人／原審）由規則式切段自動標出，**僅供挑題**。定案金標前，兩位標註者"
           "必須回到判決全文獨立確認（TESTSET_GUIDE §2）。不可用系統輸出回頭修改金標。", "",
           "## 一、適合做 C 類（原審見解被廢棄）與 A、D、E 類的判決（主文含「廢棄」）", ""]

    def dump(items):
        for jid, title, main_txt, sup, party, lower in items:
            out.extend([f"### {jid}", f"- 案由：{title}", f"- 主文：{clip(main_txt, 100)}",
                        f"- 本院段（seg {sup.index}）：{clip(sup.text)}",
                        f"- 當事人段（seg {party.index}）：{clip(party.text)}",
                        f"- 原審段（seg {lower.index}）：{clip(lower.text)}", ""])

    dump(reverse)
    out += ["## 二、適合做 B 類（當事人主張未被採納）與 A、D、E 類的判決（主文為「上訴駁回」）", ""]
    dump(dismiss)

    out += ["## 三、F 類（改號後確定查無）候選字號", "",
            "取自上面某判決的字號，把號數改成資料庫中確實 0 筆者（以下已逐筆查詢確認）：", ""]
    rng = random.Random(SEED)
    for jid, *_ in (reverse + dismiss)[:6]:
        _, year, word, no = jid.split(",")[:4]
        for _try in range(50):
            fake = int(no) + rng.randint(2000, 9000)
            n = con.execute("SELECT COUNT(*) FROM judgments WHERE year=? AND case_word=? AND case_no=?",
                            (int(year), word, fake)).fetchone()[0]
            if n == 0:
                out.append(f"- 最高法院{year}年度{word}字第{fake}號（改自第{no}號；資料庫 0 筆）")
                break

    out += ["", "## 四、法條題素材（現行法規資料庫）", ""]
    deleted = con.execute("SELECT law_name, article FROM articles WHERE text IN ('（刪除）') "
                          "AND law_name IN ('民事訴訟法','民法','強制執行法') LIMIT 6").fetchall()
    out.append("- 已刪除條文（可做 ARTICLE_DELETED）：" + "、".join(f"{l}第{a}條" for l, a in deleted))
    for law in ("民法", "民事訴訟法"):
        mx = con.execute("SELECT MAX(CAST(article AS INTEGER)) FROM articles WHERE law_name=? "
                         "AND article GLOB '[0-9]*' AND article NOT LIKE '%-%'", (law,)).fetchone()[0]
        out.append(f"- {law}最大條號 {mx}：寫「第{mx + 50}條」可做 ARTICLE_NOT_FOUND（先確認查詢為 0 筆）")
    out.append("- 簡稱題：民訴＝民事訴訟法；「同法」承接題：前一句先出現完整法規名稱，下一句寫「同法第○條」。")
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(reverse)} reversal + {len(dismiss)} dismissal candidates")


if __name__ == "__main__":
    main()
