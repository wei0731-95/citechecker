"""Mark the sentences in a judgment's 本院 passages that merely quote someone else (SPEC_V2 F2).

Done the first time a judgment is used, then stored in `quoted_sentences` for good.
Database access happens on the calling (main) thread; only call_prep() touches the LLM.
"""
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ValidationError

from .judge import _log_failure
from .retrieve import split_sentences  # noqa: F401  (re-exported; defined in retrieve.py)
from .segment import SEGMENT_RULES_VERSION

PREP_PROMPT_VERSION = 1
PREP_MAX_TOKENS = 2000
BATCH_CHARS = 20000
SPEAKERS = ("LOWER_COURT", "PARTY")

PREP_SYSTEM_PROMPT = """你是法律文件標註助手。你會收到最高法院一件民事裁判中的數個編號句子。這些句子都出自法院說明理由的段落。
你的工作只有一件事：找出其中「不是最高法院自己的見解，而是在轉述別人說法」的句子。
規則：
1. LOWER_COURT：該句在轉述原審、原判決或第一審法院的認定或理由，例如以「原審以」「原審認」「原判決謂」開頭的敘述。
2. PARTY：該句在轉述當事人的主張、抗辯、上訴理由或抗告理由。
3. 一句話前半轉述、後半是最高法院的評價（例如「原審雖認…，惟…」）時，仍要列出，並依前半轉述的對象標示。
4. 最高法院自己的見解、對原審的指摘、結論，都不要列出。
5. 只依句子本身與其前後文判斷，不得使用你自己的法律知識。沒有把握的句子不要列出。
6. 只輸出 JSON，不要任何其他文字。沒有任何符合的句子時輸出 {"quoted":[]}。"""

PREP_USER_SUFFIX = """

請輸出：
{"quoted":[{"id":1,"speaker":"LOWER_COURT"},{"id":2,"speaker":"PARTY"}]}"""


def build_prep_prompt(items: list[tuple[int, str]]) -> str:
    return "裁判句子：\n" + "\n".join(f"[{i}] {t}" for i, t in items) + PREP_USER_SUFFIX


class _Quoted(BaseModel):
    id: int
    speaker: str


class _Response(BaseModel):
    quoted: list[_Quoted]


def parse_prep_response(raw: str, lo: int, hi: int) -> dict[int, str]:
    """{id: speaker}. Raises ValueError on anything invalid (duplicate/out-of-range id, bad speaker, empty)."""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object")
    try:
        resp = _Response.model_validate(json.loads(s[a:b + 1]))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(str(e)) from e
    out: dict[int, str] = {}
    for q in resp.quoted:
        if q.speaker not in SPEAKERS or not (lo <= q.id <= hi) or q.id in out:
            raise ValueError(f"bad item {q}")
        out[q.id] = q.speaker
    return out


@dataclass
class PrepPlan:
    jid: str
    # one entry per sentence: (global id, passage seq, sentence index inside the passage, text)
    sentences: list[tuple[int, int, int, str]]
    batches: list[list[tuple[int, int, int, str]]]


def _current_ok(con: sqlite3.Connection, jid: str) -> bool:
    row = con.execute("SELECT status, prompt_version, segment_rules_version FROM judgment_prep WHERE jid=?",
                      (jid,)).fetchone()
    return bool(row) and row[0] == "ok" and row[1] == PREP_PROMPT_VERSION and row[2] == SEGMENT_RULES_VERSION


def plan_prep(con: sqlite3.Connection, jid: str) -> PrepPlan | None:
    """None when nothing needs to be done (already prepared, or no 本院 passage -> recorded as ok)."""
    if _current_ok(con, jid):
        return None
    rows = con.execute("SELECT seq, text FROM passages WHERE jid=? AND role='SUPREME_COURT' ORDER BY seq",
                       (jid,)).fetchall()
    sents: list[tuple[int, int, int, str]] = []
    batches: list[list[tuple[int, int, int, str]]] = []
    cur: list[tuple[int, int, int, str]] = []
    cur_len = 0
    gid = 0
    for seq, text in rows:
        mine = []
        for k, sent in enumerate(split_sentences(text)):
            gid += 1
            mine.append((gid, seq, k, sent))
        size = sum(len(m[3]) for m in mine)
        if cur and cur_len + size > BATCH_CHARS:        # never split one passage across two batches
            batches.append(cur)
            cur, cur_len = [], 0
        cur += mine
        cur_len += size
        sents += mine
    if cur:
        batches.append(cur)
    if not sents:
        _write(con, jid, [], "ok")
        return None
    return PrepPlan(jid, sents, batches)


def call_prep(client, plan: PrepPlan) -> list[tuple[int, str]] | None:
    """LLM calls only (safe in a worker thread). [(global id, speaker)] or None if any batch failed."""
    out: list[tuple[int, str]] = []
    for batch in plan.batches:
        lo, hi = batch[0][0], batch[-1][0]
        user = build_prep_prompt([(b[0], b[3]) for b in batch])
        parsed = None
        for _ in range(2):
            raw = client.complete(PREP_SYSTEM_PROMPT, user, max_tokens=PREP_MAX_TOKENS)
            try:
                parsed = parse_prep_response(raw, lo, hi)
                break
            except ValueError as e:
                _log_failure(str(e), raw)
                forget = getattr(client, "forget", None)
                if forget:
                    forget(PREP_SYSTEM_PROMPT, user)
        if parsed is None:
            return None
        out += sorted(parsed.items())
    return out


def _write(con: sqlite3.Connection, jid: str, quoted: list[tuple[int, int, str]], status: str,
           client=None) -> None:
    model = os.environ.get("CITECHECK_MODEL") or getattr(client, "model", None)
    con.execute("DELETE FROM quoted_sentences WHERE jid=?", (jid,)) if status == "ok" else None
    con.executemany("INSERT INTO quoted_sentences(jid, passage_seq, sent_idx, speaker) VALUES(?,?,?,?)",
                    [(jid, seq, k, sp) for seq, k, sp in quoted])
    con.execute("INSERT OR REPLACE INTO judgment_prep(jid, status, model, prompt_version, "
                "segment_rules_version, created_at) VALUES(?,?,?,?,?,?)",
                (jid, status, model, PREP_PROMPT_VERSION, SEGMENT_RULES_VERSION,
                 datetime.now().astimezone().isoformat()))
    con.commit()


def store_prep(con: sqlite3.Connection, plan: PrepPlan, result: list[tuple[int, str]] | None, client=None) -> bool:
    if result is None:
        _write(con, plan.jid, [], "failed", client)
        return False
    pos = {s[0]: (s[1], s[2]) for s in plan.sentences}
    _write(con, plan.jid, [(*pos[i], sp) for i, sp in result], "ok", client)
    return True


def ensure_prepared(jid: str, con: sqlite3.Connection, client) -> bool:
    """True when the judgment's quoted sentences are available (already stored, or just created)."""
    plan = plan_prep(con, jid)
    if plan is None:
        return True
    return store_prep(con, plan, call_prep(client, plan), client)


def load_quotes(con: sqlite3.Connection, jid: str) -> dict[tuple[int, int], str]:
    return {(seq, k): sp for seq, k, sp in con.execute(
        "SELECT passage_seq, sent_idx, speaker FROM quoted_sentences WHERE jid=?", (jid,))}
