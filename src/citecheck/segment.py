"""Rule-based segmentation of a Supreme Court civil judgment (SPEC 7.1). No LLM.

Pipeline: split into header / 主文 / 理由 / footer -> reflow hard-wrapped lines into
paragraphs -> assign a role per paragraph from its opening marker, with state
carry-over (an unmarked paragraph keeps the previous paragraph's role).
"""
import random
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import SegmentRole

# ------------------------------------------------------------------ markers
# Starting candidates from SPEC 7.1; revised against real data in docs/SEGMENT_RULES.md.
PARTY_MARKERS = [
    "上訴人主張", "被上訴人主張", "本件上訴人主張", "被上訴人則以", "上訴人則以",
    "原告主張", "被告則以", "再抗告人主張", "抗告意旨", "上訴意旨", "上訴論旨",
]
LOWER_MARKERS = ["原審", "原法院", "原判決", "第一審"]
SUPREME_MARKERS = ["本院", "按", "惟按", "惟查", "查", "經核", "據上論結"]

_ENUM = re.compile(
    # -: private-use glyphs the open data uses for some ordinal markers (e.g. U+F6AE)
    r"^[ \t　]*(?:[一二三四五六七八九十]+、|[㈠-㈩]|[⒈-⒛]|[①-⑳]|[-]|"
    r"（[一二三四五六七八九十0-9]+）|\([一二三四五六七八九十0-9]+\)|[0-9]{1,2}[、.．](?![0-9]))\s*")
_HEADINGS = {"主文": "main", "理由": "reason", "事實及理由": "reason",
             "事實與理由": "reason", "事實": "reason", "事實理由": "reason"}
_DATE_LINE = re.compile(r"中華民國[0-9０-９]+年[0-9０-９]+月[0-9０-９]+日")
_TERMINAL = "。；："
_CLOSERS = "）)」』 "
# In flush-left layouts, a line right after a sentence end that opens with one of these
# is treated as a new paragraph even if the previous line was nearly full width.
_STRONG_STARTS = ("據上論結", "惟按", "惟查", "本院", "經核")
# Generic party-statement clause, e.g. "本件被上訴人主張：" / "上訴人林某則以：" (added after
# validating on real data; checked after the explicit markers).
_PARTY_CLAUSE = re.compile(r"^[^，。：；]{0,40}?(?:主張|則以|辯稱|抗辯|陳稱|略以)")
_LEAD_JUNK = re.compile(r"^[\s　​﻿]+")


@dataclass
class Segment:
    index: int
    role: SegmentRole
    text: str
    part: str          # "header" | "main" | "reason" | "footer"
    marker: str | None  # opening marker that decided the role (None = carried over)


def _squash(s: str) -> str:
    return re.sub(r"[\s　]", "", s)


def _indent(line: str) -> int:
    lead = line[: len(line) - len(line.lstrip(" 　"))]
    return lead.count(" ") + 2 * lead.count("　")


def role_of(paragraph: str) -> tuple[SegmentRole | None, str | None]:
    """Role decided by the paragraph's opening marker; (None, None) if no marker."""
    body = _LEAD_JUNK.sub("", _ENUM.sub("", paragraph, count=1))
    for markers, role in ((PARTY_MARKERS, SegmentRole.PARTY_CLAIM),
                          (LOWER_MARKERS, SegmentRole.LOWER_COURT),
                          (SUPREME_MARKERS, SegmentRole.SUPREME_COURT)):
        for mk in sorted(markers, key=len, reverse=True):
            if body.startswith(mk):
                return role, mk
    if _PARTY_CLAUSE.match(body):
        return SegmentRole.PARTY_CLAIM, "(當事人陳述句)"
    return None, None


def _split_parts(lines: list[str]):
    """-> (header, main, reason, footer) line lists."""
    header, main, reason, footer = [], [], [], []
    cur = header
    seen_main = False
    for ln in lines:
        s = _squash(ln)
        kind = _HEADINGS.get(s)
        if kind == "main" and not seen_main:
            seen_main = True
            cur = main
            continue
        if kind == "reason" and seen_main and cur is main:
            cur = reason
            continue
        if cur is not header and cur is not footer and _DATE_LINE.fullmatch(s):
            cur = footer
        cur.append(ln)
    return header, main, reason, footer


def _reflow(lines: list[str]) -> list[str]:
    """Join hard-wrapped lines into logical paragraphs."""
    lines = [ln.rstrip() for ln in lines if ln.strip()]
    if not lines:
        return []
    lens = sorted(len(ln.strip()) for ln in lines)
    width = lens[int(len(lens) * 0.9)] if lens else 0
    indented_style = sum(1 for ln in lines if _indent(ln) >= 4 and not _ENUM.match(ln)) >= 3
    paras: list[str] = []
    prev = ""
    for ln in lines:
        text = ln.strip()
        ends = prev.rstrip(_CLOSERS)[-1:] in tuple(_TERMINAL) if prev else True
        new = not paras or (bool(_ENUM.match(ln)) and ends)
        if not new and prev and prev[-1] in _TERMINAL:
            if indented_style:
                new = _indent(ln) < 4
            else:
                new = len(prev) < width * 0.8 or text.startswith(_STRONG_STARTS)
        if new:
            paras.append(text)
        else:
            paras[-1] += text
        prev = text
    return paras


def segment(full_text: str) -> list[Segment]:
    lines = full_text.replace("\r", "").split("\n")
    header, main, reason, footer = _split_parts(lines)
    segs: list[Segment] = []

    def add(role, text, part, marker=None):
        segs.append(Segment(len(segs), role, text, part, marker))

    if any(ln.strip() for ln in header):
        add(SegmentRole.UNKNOWN, " ".join(ln.strip() for ln in header if ln.strip()), "header")
    for p in _reflow(main):
        add(SegmentRole.MAIN_TEXT, p, "main")
    # A 裁定's reasoning is the court's own; unlike a 判決 it does not recount the parties'
    # and lower court's positions under unmarked paragraphs (validated: see SEGMENT_RULES.md).
    first_line = next((ln for ln in lines if ln.strip()), "")
    state = SegmentRole.SUPREME_COURT if "裁定" in first_line else SegmentRole.UNKNOWN
    for p in _reflow(reason):
        role, marker = role_of(p)
        if role is not None:
            state = role
        add(state, p, "reason", marker)
    if any(ln.strip() for ln in footer):
        add(SegmentRole.UNKNOWN, " ".join(ln.strip() for ln in footer if ln.strip()), "footer")
    return segs


# ------------------------------------------------------------------ samples
def sample_judgments(con: sqlite3.Connection, n: int = 20, seed: int = 42, kind: str = "判決"):
    rows = con.execute("SELECT jid, full_text FROM judgments WHERE doc_kind=? ORDER BY jid", (kind,)).fetchall()
    return random.Random(seed).sample(rows, min(n, len(rows)))


def write_samples(con: sqlite3.Connection, out_dir: Path, n: int = 20, seed: int = 42, kind: str = "判決") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = ["# 切段樣本摘要（seed=%d）" % seed, "",
               "| JID | 段數 | 理由段 | 理由段 UNKNOWN |", "|---|---|---|---|"]
    for jid, text in sample_judgments(con, n, seed, kind):
        segs = segment(text)
        body = [s for s in segs if s.part == "reason"]
        unk = [s for s in body if s.role == SegmentRole.UNKNOWN]
        summary.append(f"| {jid} | {len(segs)} | {len(body)} | {len(unk)} |")
        lines = [f"# {jid}", ""]
        for s in segs:
            t = s.text if len(s.text) <= 160 else s.text[:120] + f"……（共{len(s.text)}字）……" + s.text[-20:]
            lines.append(f"[{s.index:02d}] {s.role.value:<13} ({s.part}) "
                         f"{'«' + s.marker + '»' if s.marker else '-'}\n     {t}")
        (out_dir / (jid.replace(",", "_") + ".txt")).write_text("\n".join(lines), encoding="utf-8")
    (out_dir / "_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")


if __name__ == "__main__":
    from .db import DEFAULT_DB
    import sys
    ruling = "--ruling" in sys.argv
    write_samples(sqlite3.connect(DEFAULT_DB), Path("docs/segment_samples_ruling" if ruling else "docs/segment_samples"),
                  kind="裁定" if ruling else "判決")
