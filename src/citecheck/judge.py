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
from .retrieve import Passage, apply_quotes, clean_query, retrieve

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
    """SPEC_V2 C1, pure rules; only results with confidence >= CONFIDENCE_THRESHOLD count."""
    hits = [(passages[r.id - 1], r) for r in results if r.confidence >= CONFIDENCE_THRESHOLD]

    def pick(relation: Relation, roles: set[SegmentRole]):
        c = [(p, r) for p, r in hits if r.relation == relation and p.role in roles]
        return max(c, key=lambda t: t[1].confidence) if c else None

    def ev(hit):
        p, r = hit
        return L2Evidence(segment_index=p.segment_index, segment_role=p.role,
                          passage=p.text[:400], relation=r.relation, confidence=r.confidence)

    S, C = Relation.SUPPORTS, Relation.CONTRADICTS
    court = {SegmentRole.SUPREME_COURT}
    sup, con = pick(S, court), pick(C, court)
    if sup and not con:                                              # 1
        return L2Label.COURT_HOLDING, ev(sup)
    if sup and con:                                                  # 2
        return L2Label.UNCERTAIN, ev(max((sup, con), key=lambda t: t[1].confidence))
    if con:                                                          # 3
        return L2Label.CONTRADICTED, ev(con)
    low = pick(S, {SegmentRole.LOWER_COURT})                         # 4
    if low:
        return L2Label.LOWER_COURT_VIEW, ev(low)
    party = pick(S, {SegmentRole.PARTY_CLAIM})                       # 5
    if party:
        return L2Label.PARTY_ARGUMENT, ev(party)
    unclear = {SegmentRole.UNKNOWN, SegmentRole.MAIN_TEXT}           # 6
    cands = [h for h in (pick(S, unclear), pick(C, unclear)) if h]
    if cands:
        return L2Label.UNCERTAIN, ev(max(cands, key=lambda t: t[1].confidence))
    return L2Label.NOT_IN_JUDGMENT, None                             # 7


def judge_passages(claim: str, passages: list[Passage], client: LLMClient):
    results = query_llm(client, build_user_prompt(claim, passages), len(passages))
    if results is None:
        return L2Label.UNCERTAIN, None
    return decide(passages, results)


MAX_PARALLEL_CALLS = 4
MAX_API_CALLS_PER_CHECK = 120


def api_calls_of(client) -> int:
    if hasattr(client, "api_calls"):
        return client.api_calls
    return len(getattr(client, "calls", []))


def run_limited(items: list, fn, client, max_workers: int = MAX_PARALLEL_CALLS,
                cap: int | None = None) -> tuple[list, list[int]]:
    """Run fn(item) in worker threads, at most max_workers at a time, in order.

    Before each submission the client's real API-call count is checked against `cap`; once it is
    reached nothing further is submitted. Returns (results aligned with items, indexes never run).
    Worker threads must only do LLM calls (SPEC_V2 F3): all database work stays with the caller.
    """
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    cap = MAX_API_CALLS_PER_CHECK if cap is None else cap
    results: list = [None] * len(items)
    pos = 0
    inflight: dict = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        while pos < len(items) or inflight:
            while pos < len(items) and len(inflight) < max_workers and api_calls_of(client) + len(inflight) < cap:
                inflight[ex.submit(fn, items[pos])] = pos
                pos += 1
            if not inflight:
                break
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for f in done:
                results[inflight.pop(f)] = f.result()
    return results, list(range(pos, len(items)))


def apply_l2(judgments: list[JudgmentCitation], con: sqlite3.Connection, client: LLMClient,
             stats=None) -> None:
    """Steps 2-3 of SPEC_V2 F3. Segments come from the `passages` table (build-index)."""
    from .index import check_index_ready, load_passages
    from .models import RunStats
    from .prep import call_prep, plan_prep, store_prep

    stats = stats if stats is not None else RunStats()
    raws = [j.raw for j in judgments]
    targets = []
    for j in judgments:
        if j.status != JudgmentStatus.FOUND or not j.jid:
            j.l2_label, j.l2_evidence = L2Label.NOT_RUN, None
        else:
            targets.append(j)
    if not targets:
        return
    jids = list(dict.fromkeys(j.jid for j in targets))          # first-appearance order, de-duplicated
    check_index_ready(con, jids)                                 # before any API call

    # step 2: first-use preparation of each judgment (quoted sentences)
    plans = [pl for pl in (plan_prep(con, jid) for jid in jids) if pl is not None]
    results, skipped = run_limited(plans, lambda pl: call_prep(client, pl), client)
    for pl, res, i in zip(plans, results, range(len(plans))):
        if i in skipped:
            continue
        if store_prep(con, pl, res, client):
            stats.prep_new += 1
    capped_jids = {plans[i].jid for i in skipped}
    from .prep import _current_ok, load_quotes
    prepared = {jid: _current_ok(con, jid) for jid in jids}

    # step 3: compare; one call per distinct (claim sentence, jid)
    jobs: dict[tuple[str, str], dict] = {}
    for j in targets:
        if j.jid in capped_jids:
            j.l2_label, j.l2_evidence = L2Label.NOT_RUN, None
            stats.skipped_by_cap += 1
            continue
        claim = j.claim_sentence or ""
        key = (claim, j.jid)
        if key not in jobs:
            passages = load_passages(con, j.jid)
            if prepared[j.jid]:                                  # C2: split off the quoted sentences
                passages = apply_quotes(passages, load_quotes(con, j.jid))
            top = retrieve(clean_query(claim, raws), passages)
            jobs[key] = {"top": top, "claim": claim,
                         "user": build_user_prompt(claim, top) if top else None, "cites": []}
        jobs[key]["cites"].append(j)
    runnable = [k for k, v in jobs.items() if v["user"] is not None]
    for k, v in jobs.items():
        if v["user"] is None:                                    # nothing retrievable -> cannot judge
            for j in v["cites"]:
                j.l2_label, j.l2_evidence = L2Label.UNCERTAIN, None
    res, skipped = run_limited(runnable, lambda k: query_llm(client, jobs[k]["user"], len(jobs[k]["top"])), client)
    for i, k in enumerate(runnable):
        v = jobs[k]
        for j in v["cites"]:
            if i in skipped:
                j.l2_label, j.l2_evidence = L2Label.NOT_RUN, None
                stats.skipped_by_cap += 1
            elif res[i] is None:
                j.l2_label, j.l2_evidence = L2Label.UNCERTAIN, None
            else:
                j.l2_label, j.l2_evidence = decide(v["top"], res[i])
                if j.l2_label == L2Label.COURT_HOLDING and (not prepared[j.jid] or j.procedural_dismissal):
                    j.l2_label = L2Label.UNCERTAIN               # C2.2 (prep failed) / C3 (procedural ruling)
