"""Citation extraction (SPEC 6). Input text must already be normalize()d."""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .models import (JudgmentCitation, JudgmentStatus, Span, StatuteCitation,
                     StatuteStatus)
from .normalize import normalize

SUPREME = "最高法院"


@dataclass
class Lexicon:
    # normalized (matching) name -> official name as stored in DB
    law_names: dict[str, str] = field(default_factory=dict)
    # alias (normalized) -> official name
    aliases: dict[str, str] = field(default_factory=dict)
    case_words: set[str] = field(default_factory=set)

    def __post_init__(self):
        self._lookup = {**self.law_names, **self.aliases}
        self._maxlen = max((len(k) for k in self._lookup), default=0)

    def law_suffix(self, window: str):
        """Longest law name / alias that is a suffix of window -> (official, length)."""
        window = window.rstrip()
        for n in range(min(self._maxlen, len(window)), 0, -1):
            hit = self._lookup.get(window[-n:])
            if hit:
                return hit, n, len(window) - n
        return None


def build_lexicon(con: sqlite3.Connection, aliases_path: Path, case_words_path: Path) -> Lexicon:
    names = {normalize(r[0]): r[0] for r in con.execute("SELECT law_name FROM laws")}
    raw_aliases = json.loads(Path(aliases_path).read_text(encoding="utf-8"))
    aliases = {normalize(k): names.get(normalize(v), v) for k, v in raw_aliases.items()}
    words = set(json.loads(Path(case_words_path).read_text(encoding="utf-8"))["case_words"])
    return Lexicon(names, aliases, words)


# ---------------------------------------------------------------- judgments
_COURT = (r"最高行政法院|最高法院|同院|智慧財產及商業法院|智慧財產法院|憲法法庭|"
          r"(?:台灣|福建)[一-鿿]{1,3}?地方法院|"
          r"(?:台灣|福建)?高等法院(?:(?:台中|台南|高雄|花蓮|金門)分院)?")
_COURT_TAIL = re.compile(rf"({_COURT})\s*((?:民事|刑事|行政)庭?)?\s*$")
_REF = re.compile(r"(\d+)年度?([一-鿿]{1,4}?)字第(\d+)號")
_ELL = re.compile(r"(?:、|及|暨|與)\s*第(\d+)號")
_SEP = re.compile(r"(?:、|及|暨|與)\s*")
_KIND = re.compile(r"\s*(民事|刑事|行政)?(大法庭)?(判決|裁定)")
_INCOMPLETE = re.compile(r"最高法院[^。；，\n]{0,20}?\d+年度?[^。；，\n]{0,12}")


def out_of_scope(court: str | None, domain: str | None, case_word: str | None,
                 case_words: set[str]) -> bool:
    if court != SUPREME:
        return True
    if domain and not domain.startswith("民事"):
        return True
    if not case_word or "大" in case_word or case_word not in case_words:
        return True
    return False


def _extract_judgments(text: str, lex: Lexicon, warnings: list[str]) -> list[JudgmentCitation]:
    refs = list(_REF.finditer(text))
    out: list[JudgmentCitation] = []
    last_court: str | None = None
    idx = 0
    while idx < len(refs):
        m = refs[idx]
        idx += 1
        items = [(m.start(), m.end(), int(m.group(1)), m.group(2), int(m.group(3)))]
        pos = m.end()
        while True:
            e = _ELL.match(text, pos)
            if e:
                year, word = items[-1][2], items[-1][3]
                items.append((e.start() + e.group(0).index("第"), e.end(), year, word,
                              int(e.group(1))))
                pos = e.end()
                continue
            s = _SEP.match(text, pos)
            if s and idx < len(refs) and refs[idx].start() == s.end():
                n = refs[idx]
                items.append((n.start(), n.end(), int(n.group(1)), n.group(2), int(n.group(3))))
                pos = n.end()
                idx += 1
                continue
            break
        # court / domain before the first reference
        prefix = text[max(0, items[0][0] - 40):items[0][0]]
        cm = _COURT_TAIL.search(prefix)
        if not cm:
            warnings.append(f"未標示法院之案號，未列入查核：{text[items[0][0]:pos]}")
            continue
        court = cm.group(1)
        domain = cm.group(2)
        raw_start = items[0][0] - (len(prefix) - cm.start())
        if court == "同院":
            court = last_court
            if court is None:
                warnings.append(f"「同院」前文無可承接之法院，未列入查核：{text[raw_start:pos]}")
                continue
        km = _KIND.match(text, pos)
        doc_kind = None
        end_all = pos
        if km:
            doc_kind = km.group(3)
            end_all = km.end()
            if km.group(1):
                domain = km.group(1)
            if km.group(2):
                domain = (domain or "") + "大法庭"
        last_court = court
        for i, (s0, e0, year, word, no) in enumerate(items):
            start = raw_start if i == 0 else s0
            end = end_all if i == len(items) - 1 else e0
            scope_out = out_of_scope(court, domain, word, lex.case_words)
            out.append(JudgmentCitation(
                raw=text[start:end], span=Span(start=start, end=end), court=court,
                year=year, case_word=word, case_no=no, doc_kind=doc_kind,
                status=JudgmentStatus.OUT_OF_SCOPE if scope_out else JudgmentStatus.NOT_FOUND_IN_SCOPE))
    covered = [(c.span.start, c.span.end) for c in out]
    for fm in _INCOMPLETE.finditer(text):
        if not any(s <= fm.start() < e for s, e in covered) and not any(
                fm.start() <= s < fm.end() for s, _ in covered):
            warnings.append(f"引用片段不完整（缺字別或號數），未列入查核：{fm.group(0)}")
    return out


# ------------------------------------------------------------------ statutes
_ART = re.compile(r"第(\d+)條(?:之(\d+))?(?:第(\d+)項)?(?:第(\d+)款)?(?:前段|後段|但書)?")
_SAME_ART = re.compile(r"同條(?:第(\d+)項)?(?:第(\d+)款)?(?:前段|後段|但書)?")
_STAT_SEP = re.compile(r"(?:、|及|暨|與|或|至)\s*")


def _art_key(m: re.Match) -> str:
    return m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")


def _int(x):
    return int(x) if x else None


def _extract_statutes(text: str, lex: Lexicon) -> list[StatuteCitation]:
    # collect candidates in text order (articles and "同條" back-references)
    cands = sorted([(m.start(), "art", m) for m in _ART.finditer(text)] +
                   [(m.start(), "same", m) for m in _SAME_ART.finditer(text)], key=lambda t: t[0])
    out: list[StatuteCitation] = []
    prev: StatuteCitation | None = None
    prev_end = -1
    for start, kind, m in cands:
        if kind == "same":
            law = prev.law_name if prev else None
            art = prev.article if prev else None
            c = StatuteCitation(
                raw=m.group(0), span=Span(start=start, end=m.end()), law_name=law, article=art,
                paragraph=_int(m.group(1)), subparagraph=_int(m.group(2)),
                status=StatuteStatus.UNRESOLVED_REF if not (law and art) else StatuteStatus.LAW_NOT_FOUND)
        else:
            raw_start = start
            law = None
            gap = text[prev_end:start] if prev is not None and prev_end >= 0 else None
            window = text[max(0, start - 80):start]
            if gap is not None and _STAT_SEP.fullmatch(gap):
                law = prev.law_name
            elif window.rstrip().endswith("同法"):
                law = prev.law_name if prev else None
                raw_start = start - (len(window) - len(window.rstrip())) - 2
            else:
                hit = lex.law_suffix(window)
                if hit:
                    law = hit[0]
                    raw_start = max(0, start - 80) + hit[2]
            c = StatuteCitation(
                raw=text[raw_start:m.end()], span=Span(start=raw_start, end=m.end()),
                law_name=law, article=_art_key(m), paragraph=_int(m.group(3)),
                subparagraph=_int(m.group(4)),
                status=StatuteStatus.UNRESOLVED_REF if law is None else StatuteStatus.LAW_NOT_FOUND)
        out.append(c)
        prev, prev_end = c, m.end()
    return out


# ------------------------------------------------------------ claim sentence
_FILLERS = ["可資參照", "亦同此旨", "意旨", "參照", "可參"]
_MIN_REST = 15
_MAX_CLAIM = 300


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"[。；\n]", text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return spans


def attach_claim_sentences(text: str, judgments: list[JudgmentCitation]) -> None:
    sents = _sentences(text)
    for j in judgments:
        si = next(i for i, (s, e) in enumerate(sents) if s <= j.span.start <= max(s, e))
        s, e = sents[si]
        sentence = text[s:e]
        rest = sentence
        for other in judgments:
            if s <= other.span.start and other.span.end <= e:
                rest = rest.replace(other.raw, "")
        for f in _FILLERS:
            rest = rest.replace(f, "")
        if len(re.findall(r"\w", rest)) < _MIN_REST:
            for pj in range(si - 1, -1, -1):
                ps, pe = sents[pj]
                if text[ps:pe].strip():
                    sentence = text[ps:pe] + "，" + sentence
                    break
        sentence = sentence.strip()
        j.claim_sentence = sentence[-_MAX_CLAIM:]


def extract_all(text: str, lex: Lexicon):
    """text must be normalize()d. Returns (statutes, judgments, warnings)."""
    warnings: list[str] = []
    judgments = _extract_judgments(text, lex, warnings)
    statutes = _extract_statutes(text, lex)
    attach_claim_sentences(text, judgments)
    return statutes, judgments, warnings
