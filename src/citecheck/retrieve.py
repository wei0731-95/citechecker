"""Passage splitting and BM25 retrieval over one judgment (SPEC 7.2)."""
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .models import SegmentRole
from .segment import Segment

MAX_PASSAGE_LEN = 400
TOP_K = 5
# SPEC says UNKNOWN passages are retrieved too. The judgment header (parties) and footer
# (judges' names) are tagged UNKNOWN by segment.py but carry no reasoning; skipped by default.
INCLUDE_HEADER_FOOTER = False
_FILLERS = ["可資參照", "亦同此旨", "意旨", "參照", "可參"]


@dataclass
class Passage:
    segment_index: int
    role: SegmentRole
    text: str


def _pack(text: str, max_len: int) -> list[str]:
    out, cur = [], ""
    for sent in re.split(r"(?<=。)", text):
        if not sent:
            continue
        while len(sent) > max_len:  # a single over-long sentence is cut hard
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:max_len])
            sent = sent[max_len:]
        if len(cur) + len(sent) > max_len:
            out.append(cur)
            cur = sent
        else:
            cur += sent
    if cur:
        out.append(cur)
    return [p for p in out if p.strip()]


def split_passages(segments: list[Segment], max_len: int = MAX_PASSAGE_LEN) -> list[Passage]:
    out = []
    for seg in segments:
        if not INCLUDE_HEADER_FOOTER and seg.part in ("header", "footer"):
            continue
        out += [Passage(seg.index, seg.role, p) for p in _pack(seg.text, max_len)]
    return out


def bigrams(text: str) -> list[str]:
    toks: list[str] = []
    for run in re.findall(r"\w+", text):
        toks += [run[i:i + 2] for i in range(len(run) - 1)] if len(run) > 1 else [run]
    return toks


def clean_query(claim: str, citation_raws: list[str]) -> str:
    q = claim
    for raw in sorted(citation_raws, key=len, reverse=True):
        q = q.replace(raw, "")
    for f in _FILLERS:
        q = q.replace(f, "")
    return q


def retrieve(query: str, passages: list[Passage], k: int = TOP_K) -> list[Passage]:
    q = bigrams(query)
    if not q or not passages:
        return []
    bm25 = BM25Okapi([bigrams(p.text) or [""] for p in passages])
    scores = bm25.get_scores(q)
    order = sorted(range(len(passages)), key=lambda i: (-scores[i], i))
    return [passages[i] for i in order[:k]]
