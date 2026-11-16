"""L2: is the brief's statement the Supreme Court's own holding? (SPEC 7.3, 7.5)"""
import json
import os
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from .llm import LLMClient
from .models import (JudgmentCitation, JudgmentStatus, L2Evidence, L2Label, Relation,
                     SegmentRole)
from .retrieve import Passage, clean_query, retrieve, split_passages
from .segment import segment

CONFIDENCE_THRESHOLD = 0.6

SYSTEM_PROMPT = """你是法律文件比對助手。你會收到一句「書狀主張」和數段編號的「判決原文段落」。
你的工作只有一件事：判斷每一段原文與書狀主張的關係。
規則：
1. 只能依據提供的段落判斷，不得使用任何你自己的法律知識或記憶。
2. relation 只能是 SUPPORTS、CONTRADICTS、UNRELATED 三者之一。
   - SUPPORTS：該段表達的法律見解或認定，與書狀主張實質相同。
   - CONTRADICTS：該段表達的法律見解或認定，與書狀主張相反。
   - UNRELATED：該段未談到書狀主張的內容，或無法判斷。
3. 不要判斷該段是誰的意見，也不要判斷書狀主張是否正確。
4. confidence 為 0 到 1 的小數。沒有把握時給低分，並選 UNRELATED。
5. 只輸出 JSON，不要任何其他文字。"""

USER_TEMPLATE = """書狀主張：
{claim_sentence}

判決原文段落：
[1] {passage_1}
[2] {passage_2}
[3] {passage_3}
[4] {passage_4}
[5] {passage_5}

請輸出：
{"results":[{"id":1,"relation":"...","confidence":0.0},{"id":2,...},{"id":3,...},{"id":4,...},{"id":5,...}]}"""


class LLMResult(BaseModel):
    id: int
    relation: Relation
    confidence: float = Field(ge=0, le=1)


class LLMResponse(BaseModel):
    results: list[LLMResult]


def build_user_prompt(claim: str, passages: list[Passage]) -> str:
    # Roles are deliberately NOT sent to the LLM (SPEC 7.3). With fewer than 5 passages the
    # missing "[n]" lines are dropped; the rest of the template is unchanged.
    out = USER_TEMPLATE.replace("{claim_sentence}", claim)
    for i in range(1, 6):
        line = f"[{i}] {{passage_{i}}}\n"
        if i <= len(passages):
            out = out.replace(f"{{passage_{i}}}", passages[i - 1].text)
        else:
            out = out.replace(line, "")
    return out


def parse_response(raw: str, n: int) -> list[LLMResult]:
    """Raises ValueError on anything that is not a valid response for n passages."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b < a:
        raise ValueError("no JSON object")
    try:
        resp = LLMResponse.model_validate(json.loads(s[a:b + 1]))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(str(e)) from e
    ids = [r.id for r in resp.results]
    if len(set(ids)) != len(ids) or any(i < 1 or i > n for i in ids):
        raise ValueError("bad ids")
    return resp.results


def _log_failure(error: str, raw: str) -> None:
    """Keep unparseable model output for debugging (path: env CITECHECK_FAILURE_LOG)."""
    path = os.environ.get("CITECHECK_FAILURE_LOG", "data/llm_failures.log")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"error": error, "raw": raw[:3000]}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def query_llm(client: LLMClient, user: str, n: int) -> list[LLMResult] | None:
    """One call, one retry on unparseable output; None if both fail."""
    for _ in range(2):
        raw = client.complete(SYSTEM_PROMPT, user)
        try:
            return parse_response(raw, n)
        except ValueError as e:
            _log_failure(str(e), raw)
            forget = getattr(client, "forget", None)
            if forget:
                forget(SYSTEM_PROMPT, user)
    return None


def decide(passages: list[Passage], results: list[LLMResult]) -> tuple[L2Label, L2Evidence | None]:
    """SPEC 7.5, pure rules."""
    hits = [(passages[r.id - 1], r) for r in results if r.confidence >= CONFIDENCE_THRESHOLD]

    def pick(relation: Relation, roles: set[SegmentRole]):
        c = [(p, r) for p, r in hits if r.relation == relation and p.role in roles]
        return max(c, key=lambda t: t[1].confidence) if c else None

    def ev(hit):
        p, r = hit
        return L2Evidence(segment_index=p.segment_index, segment_role=p.role,
                          passage=p.text[:400], relation=r.relation, confidence=r.confidence)

    S, C = Relation.SUPPORTS, Relation.CONTRADICTS
    for relation, roles, label in (
        (S, {SegmentRole.SUPREME_COURT}, L2Label.COURT_HOLDING),
        (C, {SegmentRole.SUPREME_COURT}, L2Label.CONTRADICTED),
        (S, {SegmentRole.LOWER_COURT}, L2Label.LOWER_COURT_VIEW),
        (S, {SegmentRole.PARTY_CLAIM}, L2Label.PARTY_ARGUMENT),
    ):
        h = pick(relation, roles)
        if h:
            return label, ev(h)
    unclear = {SegmentRole.UNKNOWN, SegmentRole.MAIN_TEXT}
    cands = [h for h in (pick(S, unclear), pick(C, unclear)) if h]
    if cands:
        return L2Label.UNCERTAIN, ev(max(cands, key=lambda t: t[1].confidence))
    return L2Label.NOT_IN_JUDGMENT, None


def judge_passages(claim: str, passages: list[Passage], client: LLMClient):
    results = query_llm(client, build_user_prompt(claim, passages), len(passages))
    if results is None:
        return L2Label.UNCERTAIN, None
    return decide(passages, results)


def judge_citation(c: JudgmentCitation, full_text: str, client: LLMClient,
                   all_raws: list[str]) -> None:
    query = clean_query(c.claim_sentence or "", all_raws)
    top = retrieve(query, split_passages(segment(full_text)))
    if not top:
        c.l2_label, c.l2_evidence = L2Label.UNCERTAIN, None
        return
    c.l2_label, c.l2_evidence = judge_passages(c.claim_sentence or "", top, client)


def apply_l2(judgments: list[JudgmentCitation], con: sqlite3.Connection, client: LLMClient) -> None:
    raws = [j.raw for j in judgments]
    for j in judgments:
        if j.status != JudgmentStatus.FOUND or not j.jid:
            j.l2_label, j.l2_evidence = L2Label.NOT_RUN, None
            continue
        row = con.execute("SELECT full_text FROM judgments WHERE jid=?", (j.jid,)).fetchone()
        judge_citation(j, row[0], client, raws)
