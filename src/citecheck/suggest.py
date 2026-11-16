"""Phase G: judgments whose passages say something similar to the brief's sentence (SPEC_V2 section 10).

Strict on purpose: the list is only a reading aid and must never look like legal advice.
Database access stays on the calling thread; only LLM calls run in worker threads.
"""
import re
import sqlite3

from .index import fts_search, has_fts
from .judge import build_user_prompt, query_llm, run_limited
from .models import (JudgmentCitation, JudgmentStatus, L2Label, Relation, SegmentRole, SimilarJudgment)
from .retrieve import Passage, clean_query

SUGGEST_THRESHOLD = 0.8
MIN_CLAIM_CHARS = 15
CANDIDATES = 5
MAX_SHOWN = 3
PASSAGE_CHARS = 400

TRIGGER_STATUS = {JudgmentStatus.NOT_FOUND_IN_SCOPE, JudgmentStatus.NOT_FOUND_CIVIL_ONLY,
                  JudgmentStatus.DATA_NOT_COVERED}
TRIGGER_L2 = {L2Label.NOT_IN_JUDGMENT, L2Label.PARTY_ARGUMENT, L2Label.LOWER_COURT_VIEW, L2Label.CONTRADICTED}


def display_of(year, case_word, case_no, doc_kind) -> str:
    return f"最高法院{year}年度{case_word}字第{case_no}號民事{doc_kind or ''}"


def query_text(j: JudgmentCitation, raws: list[str]) -> str | None:
    """Claim sentence without citations and filler words, or None when it is too short (G2.1)."""
    if not j.claim_sentence:
        return None
    q = clean_query(j.claim_sentence, raws)
    return q if len(re.sub(r"\s", "", q)) >= MIN_CLAIM_CHARS else None


def triggered(j: JudgmentCitation) -> bool:
    return j.status in TRIGGER_STATUS or j.l2_label in TRIGGER_L2


def candidates(con: sqlite3.Connection, query: str, skip_jids: set[str], n: int = CANDIDATES) -> list[tuple[str, int, str]]:
    """G2.2: best passage per judgment, brief's own judgment skipped, up to n."""
    out, seen = [], set(skip_jids)
    for jid, seq, text in fts_search(con, query):
        if jid in seen:
            continue
        seen.add(jid)
        out.append((jid, seq, text))
        if len(out) >= n:
            break
    return out


def apply_suggestions(judgments: list[JudgmentCitation], con: sqlite3.Connection, client, stats=None) -> None:
    from .models import RunStats
    from .prep import _current_ok, call_prep, plan_prep, store_prep

    if client is None or not has_fts(con):
        return
    stats = stats if stats is not None else RunStats()
    raws = [j.raw for j in judgments]
    groups: dict[str, dict] = {}                       # query text -> {"cites": [...], "skip": set}
    for j in judgments:
        if not triggered(j):
            continue
        q = query_text(j, raws)
        if q is None:
            continue
        g = groups.setdefault(q, {"cites": [], "skip": set()})
        g["cites"].append(j)
        if j.jid:
            g["skip"].add(j.jid)
    if not groups:
        return

    # G2.2 + G2.3: candidates and one comparison call per distinct claim
    for q, g in groups.items():
        g["cand"] = candidates(con, q, g["skip"])
        g["top"] = []
        for jid, seq, text in g["cand"]:
            row = con.execute("SELECT segment_index FROM passages WHERE jid=? AND seq=?", (jid, seq)).fetchone()
            g["top"].append(Passage(row[0] if row else 0, SegmentRole.SUPREME_COURT, text))
    runnable = [q for q, g in groups.items() if g["top"]]
    res, skipped = run_limited(
        runnable, lambda q: query_llm(client, build_user_prompt(q, groups[q]["top"]), len(groups[q]["top"])),
        client)
    kept: dict[str, list[tuple[str, int, str, float]]] = {}      # query -> [(jid, seq, text, confidence)]
    for i, q in enumerate(runnable):
        if i in skipped:
            stats.skipped_by_cap += 1
            continue
        if res[i] is None:
            continue
        g = groups[q]
        kept[q] = [(g["cand"][r.id - 1][0], g["cand"][r.id - 1][1], g["cand"][r.id - 1][2], r.confidence)
                   for r in res[i] if r.relation == Relation.SUPPORTS and r.confidence >= SUGGEST_THRESHOLD]

    # G2.4: preparation of every kept judgment, then drop failed / paraphrased ones
    jids = list(dict.fromkeys(jid for lst in kept.values() for jid, *_ in lst))
    plans = [pl for pl in (plan_prep(con, jid) for jid in jids) if pl is not None]
    pres, pskipped = run_limited(plans, lambda pl: call_prep(client, pl), client)
    for i, pl in enumerate(plans):
        if i in pskipped:
            continue
        if store_prep(con, pl, pres[i], client):
            stats.prep_new += 1
    ok = {jid: _current_ok(con, jid) for jid in jids}

    for q, lst in kept.items():
        final = []
        for jid, seq, text, conf in lst:
            if not ok.get(jid):
                continue
            if con.execute("SELECT 1 FROM quoted_sentences WHERE jid=? AND passage_seq=? LIMIT 1",
                           (jid, seq)).fetchone():
                continue
            final.append((jid, text, conf))
        final.sort(key=lambda t: -t[2])
        sims = []
        for jid, text, conf in final[:MAX_SHOWN]:                # G2.5
            r = con.execute("SELECT year, case_word, case_no, doc_kind, pdf_url FROM judgments WHERE jid=?",
                            (jid,)).fetchone()
            if r is None:
                continue
            sims.append(SimilarJudgment(jid=jid, display=display_of(r[0], r[1], r[2], r[3]),
                                        passage=text[:PASSAGE_CHARS], confidence=conf, pdf_url=r[4]))
        for j in groups[q]["cites"]:                              # G2.6: same claim shares the result
            j.similar = [s.model_copy() for s in sims]
