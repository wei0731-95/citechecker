"""Citation extraction (SPEC 6, SPEC_V2 A1-A6, A12, A13). Input text must already be normalize()d."""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .models import (JudgmentCitation, JudgmentStatus, OtherAuthority, Span, StatuteCitation,
                     StatuteStatus, UnrecognizedSentence)
from .normalize import normalize

SUPREME = "最高法院"

# A1: characters / words that may legitimately stand directly before a law name.
LAW_PREFIX_SINGLE = set("依按有觀以及於無與為逾又屬前之非查而或且另惟但故至係在就因何犯指見暨如即並對和跟則均亦仍應得可")
LAW_PREFIX_WORDS = [
    "適用", "準用", "援用", "引用", "類推", "違反", "違背", "有違", "無違", "未違", "牴觸",
    "參照", "依照", "按照", "依據", "根據", "引據", "基於", "依循", "參酌", "參考", "參見", "詳見",
    "觀諸", "揆諸", "參諸", "審諸", "符合", "不符", "合於", "不合", "構成", "成立", "該當",
    "觸犯", "涉犯", "另涉", "無涉", "行使", "踐行", "主張", "所指", "援引", "追加",
    "修正", "公布", "增訂", "施行", "現行", "當時", "我國", "上述", "上開", "前開",
    "應負", "應受", "不受", "具備", "不備", "不生", "未達", "已達", "超過", "有關", "攸關", "認為",
]

# A2: what stands before "第N條" when no law name matched.
NON_STATUTE_SUFFIXES = ["契約", "合約", "契約書", "合約書", "協議", "協議書", "規約", "章程", "公約",
                        "條款", "約款", "保單", "要點", "須知", "規章", "工作規則", "約定", "合意",
                        "租約", "和解書", "切結書", "同意書"]
BACKREF_SUFFIXES = ["本法", "該法", "同法", "前法", "新法", "舊法", "母法", "上開法", "前開法", "前揭法", "各該法",
                    "本條例", "該條例", "同條例", "系爭條例",
                    "本辦法", "該辦法", "系爭辦法", "本規則", "該規則", "系爭規則", "本細則"]
ORDER_SUFFIXES = ["施行細則", "細則", "規則", "辦法", "準則", "標準", "規程", "綱要", "自治條例"]

_CJK = ("一", "鿿")


def _is_cjk(ch: str) -> bool:
    return _CJK[0] <= ch <= _CJK[1]


def boundary_ok(pre: str) -> bool:
    """A1: may a law name directly follow `pre`?"""
    pre = pre.rstrip()
    if not pre:
        return True
    ch = pre[-1]
    if not _is_cjk(ch):
        return True
    if ch in LAW_PREFIX_SINGLE:
        return True
    return any(pre.endswith(w) for w in LAW_PREFIX_WORDS)


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
        """Longest law name / alias that is a suffix of window -> (official, length, start index)."""
        window = window.rstrip()
        for n in range(min(self._maxlen, len(window)), 0, -1):
            hit = self._lookup.get(window[-n:])
            if hit:
                return hit, n, len(window) - n
        return None

    def first_name_start(self, s: str) -> int | None:
        """Leftmost index in s where a known law name / alias begins."""
        for i in range(len(s)):
            for n in range(min(self._maxlen, len(s) - i), 1, -1):
                if s[i:i + n] in self._lookup:
                    return i
        return None


def build_lexicon(con: sqlite3.Connection, aliases_path: Path, case_words_path: Path) -> Lexicon:
    # A11: repealed laws are in the lexicon too (verify_statute reports LAW_REPEALED).
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
_KIND = re.compile(r"\s*(民事|刑事|行政)?(大法庭)?(判決|裁定|判例|決議)")
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


def _domain_stated(domain: str | None):
    """A4: 民事 / 刑事 / 行政 as written in the brief, else None."""
    for d in ("民事", "刑事", "行政"):
        if domain and domain.startswith(d):
            return d
    return None


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
        court_inferred = False
        if cm:
            court = cm.group(1)
            domain = cm.group(2)
            raw_start = items[0][0] - (len(prefix) - cm.start())
            if court == "同院":
                court = last_court
                if court is None:
                    warnings.append(f"「同院」前文無可承接之法院，未列入查核：{text[raw_start:pos]}")
                    continue
        else:
            # A6: no court named. A Supreme Court civil case word -> infer the court; else out of scope.
            court, domain, raw_start = None, None, items[0][0]
            if items[0][3] in lex.case_words:
                court, court_inferred = SUPREME, True
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
        if cm:
            last_court = court
        for i, (s0, e0, year, word, no) in enumerate(items):
            start = raw_start if i == 0 else s0
            end = end_all if i == len(items) - 1 else e0
            scope_out = out_of_scope(court, domain, word, lex.case_words) or doc_kind in ("判例", "決議")
            out.append(JudgmentCitation(
                raw=text[start:end], span=Span(start=start, end=end), court=court,
                year=year, case_word=word, case_no=no, doc_kind=doc_kind,
                domain_stated=_domain_stated(domain), court_inferred=court_inferred,
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
# SPEC_V3 H5: "、第5項" / "及第11款" right after a citation that has a paragraph or subparagraph.
_ITEM_CONT = re.compile(
    r"(?:、|及|或|與|暨|至)\s*(?:第(\d+)項(?:第(\d+)款)?|第(\d+)款)(?:前段|後段|但書)?")


def _art_key(m: re.Match) -> str:
    return m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")


def _int(x):
    return int(x) if x else None


def _trailing_cjk(text: str, end: int, limit: int = 30) -> str:
    i = end
    while i > 0 and end - i < limit and _is_cjk(text[i - 1]):
        i -= 1
    return text[i:end]


def _unresolved_name_start(run: str, run_start: int, lex: Lexicon) -> int:
    """A2: where `raw` starts for LAW_OUT_OF_SCOPE / LAW_NAME_UNCERTAIN (absolute text index)."""
    i = lex.first_name_start(run)
    if i is not None:
        return run_start + i
    return run_start + max(0, len(run) - 12)


def _extract_statutes(text: str, lex: Lexicon) -> list[StatuteCitation]:
    # collect candidates in text order (articles and "同條" back-references)
    cands = sorted([(m.start(), "art", m) for m in _ART.finditer(text)] +
                   [(m.start(), "same", m) for m in _SAME_ART.finditer(text)], key=lambda t: t[0])
    out: list[StatuteCitation] = []
    prev: StatuteCitation | None = None
    prev_end = -1
    for start, kind, m in cands:
        if kind == "art" and start < prev_end:                   # H5: inside an item run already handled
            continue
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
            status = None
            gap = text[prev_end:start] if prev is not None and prev_end >= 0 else None
            win_start = max(0, start - 80)
            window = text[win_start:start]
            if gap is not None and _STAT_SEP.fullmatch(gap):
                law = prev.law_name
                status = prev.status if prev.status in (
                    StatuteStatus.LAW_NAME_UNCERTAIN, StatuteStatus.LAW_OUT_OF_SCOPE) else None
            elif window.rstrip().endswith("同法"):
                law = prev.law_name if prev else None
                raw_start = start - (len(window) - len(window.rstrip())) - 2
            else:
                hit = lex.law_suffix(window)
                if hit:
                    name_start = win_start + hit[2]
                    raw_start = name_start
                    if boundary_ok(text[:name_start]):          # A1
                        law = hit[0]
                    else:
                        status = StatuteStatus.LAW_NAME_UNCERTAIN
                else:                                            # A2
                    run = _trailing_cjk(text, start)
                    run_start = start - len(run)
                    if any(run.endswith(s) for s in NON_STATUTE_SUFFIXES):
                        continue                                  # a contract / charter clause
                    if any(run.endswith(s) for s in BACKREF_SUFFIXES):
                        status = StatuteStatus.UNRESOLVED_REF
                    elif any(run.endswith(s) for s in ORDER_SUFFIXES):
                        status = StatuteStatus.LAW_OUT_OF_SCOPE
                        raw_start = _unresolved_name_start(run, run_start, lex)
                    elif run.endswith(("法", "條例", "通則")) and len(run) >= 3:
                        status = StatuteStatus.LAW_NAME_UNCERTAIN
                        raw_start = _unresolved_name_start(run, run_start, lex)
                    else:
                        status = StatuteStatus.UNRESOLVED_REF
            if status is None:
                status = StatuteStatus.UNRESOLVED_REF if law is None else StatuteStatus.LAW_NOT_FOUND
            c = StatuteCitation(
                raw=text[raw_start:m.end()], span=Span(start=raw_start, end=m.end()),
                law_name=law, article=_art_key(m), paragraph=_int(m.group(3)),
                subparagraph=_int(m.group(4)), status=status)
        out.append(c)
        prev, prev_end = c, m.end()
        if c.paragraph is not None or c.subparagraph is not None:   # H5: 第N項、第M項 / 第N款、第M款
            last, pos = c, m.end()
            while (mm := _ITEM_CONT.match(text, pos)):
                if mm.group(1):
                    para, sub = int(mm.group(1)), _int(mm.group(2))
                else:
                    para, sub = last.paragraph, int(mm.group(3))
                s0 = mm.start() + mm.group(0).index("第")
                last = StatuteCitation(
                    raw=text[s0:mm.end()], span=Span(start=s0, end=mm.end()), law_name=c.law_name,
                    article=c.article, paragraph=para, subparagraph=sub, status=c.status, is_continuation=True)
                out.append(last)
                pos = mm.end()
            prev, prev_end = last, pos
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


def attach_statute_claims(text: str, statutes: list[StatuteCitation]) -> None:
    """B2: the sentence holding the citation; if over 300 chars, 200 before to 100 after its end."""
    sents = _sentences(text)
    for c in statutes:
        si = next(i for i, (s, e) in enumerate(sents) if s <= c.span.start <= max(s, e))
        s, e = sents[si]
        if e - s > 300:
            s, e = max(s, c.span.end - 200), min(e, c.span.end + 100)
        c.claim_sentence = text[s:e].strip()


# ------------------------------------------------- A12: other authorities
_N = "[0-9〇○零一二三四五六七八九十百千]+"
_AUTHORITY_PATTERNS = [
    ("釋字", re.compile(rf"(?:司法院|大法官)?釋字第{_N}號(?:解釋)?")),
    ("憲判字", re.compile(rf"(?:憲法法庭)?{_N}年憲判字第{_N}號(?:判決)?")),
    ("決議", re.compile(
        rf"(?:最高法院)?{_N}年度?第{_N}次(?:民事庭|民刑事庭|刑事庭)會議(?:決議(?:[(（]{_N}[)）])?)?")),
    ("座談會", re.compile(rf"{_N}年[^。；\n]*?法律座談會")),
]


def _extract_other_authorities(text: str) -> list[OtherAuthority]:
    out = []
    for kind, pat in _AUTHORITY_PATTERNS:
        for m in pat.finditer(text):
            if kind == "座談會" and len(m.group(0)) > 40:
                continue
            out.append(OtherAuthority(raw=m.group(0), span=Span(start=m.start(), end=m.end()), kind=kind))
    return sorted(out, key=lambda a: (a.span.start, a.span.end))


# ------------------------------------------------- A13: unrecognized citations
_CITE_WORDS = ("判決", "裁定", "判例", "決議", "解釋", "參照")


def _find_unrecognized(text: str, judgments, others) -> list[UnrecognizedSentence]:
    spans = [(c.span.start, c.span.end) for c in judgments] + [(a.span.start, a.span.end) for a in others]
    out = []
    for s, e in _sentences(text):
        sent = text[s:e]
        if "號" not in sent or not any(w in sent for w in _CITE_WORDS):
            continue
        if any(a < e and s < b for a, b in spans):
            continue
        out.append(UnrecognizedSentence(text=sent[:200], span=Span(start=s, end=e)))
    return out


# ------------------------------------------------------------------ entry
@dataclass
class ExtractResult:
    statutes: list[StatuteCitation]
    judgments: list[JudgmentCitation]
    warnings: list[str]
    other_authorities: list[OtherAuthority]
    unrecognized: list[UnrecognizedSentence]


def extract_full(text: str, lex: Lexicon) -> ExtractResult:
    """text must be normalize()d."""
    warnings: list[str] = []
    judgments = _extract_judgments(text, lex, warnings)
    statutes = _extract_statutes(text, lex)
    others = _extract_other_authorities(text)
    # H3: a 憲法法庭 judgment is reported once, as an other authority
    judgments = [j for j in judgments
                 if not any(a.span.start < j.span.end and j.span.start < a.span.end for a in others)]
    attach_claim_sentences(text, judgments)
    attach_statute_claims(text, statutes)
    return ExtractResult(statutes, judgments, warnings, others, _find_unrecognized(text, judgments, others))


def extract_all(text: str, lex: Lexicon):
    """Kept for existing callers. Returns (statutes, judgments, warnings)."""
    r = extract_full(text, lex)
    return r.statutes, r.judgments, r.warnings
