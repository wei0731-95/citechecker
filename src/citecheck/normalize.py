"""Text normalization (SPEC 6.1 + SPEC_V2 A7/A8).

Spans elsewhere index into normalize()'s output. normalize_with_map() additionally returns,
for every output character, the (start, end) range of original characters it came from, so a
citation found in the normalized text can be shown at its place in the text the user pasted.
"""
import re

_CN_DIGITS = {"〇": 0, "○": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_CLASS = "〇○零一二三四五六七八九十百千"
_NUM = f"[{_CN_CLASS}]+"
_NUMCH = f"[0-9{_CN_CLASS}]"
_SUF = "年度字第號條項款"

_SPACE = re.compile(
    rf"(?<={_NUMCH})\s+(?=[{_SUF}])|(?<=[{_SUF}])\s+(?={_NUMCH})|(?<=字)\s+(?=第)")
# Only the numeral itself is replaced, so neighbouring characters keep their own original ranges.
_ARTICLE_NUM = re.compile(rf"(?<=第)({_NUM})(?=[條項款號])")
_ARTICLE_SUB = re.compile(rf"(?<=\d條之)({_NUM})")
_CASE_YEAR = re.compile(
    rf"(?<!{_NUMCH})({_NUM})(?=年度?[^\s，。；、：\d]{{1,6}}?字第)")

# A7: Markdown symbols AI-written text often carries.
_MD_INLINE = re.compile(r"\*\*|__|`")
_MD_HEADING = re.compile(r"^([ \t]*)(#+[ \t]*)", re.M)
_MD_BULLET = re.compile(r"^([ \t]*)([-*>] )", re.M)

Ranges = list[tuple[int, int]]


def cn_to_int(s: str) -> int:
    """'一○九'->109, '一百零九'->109, '一百八十四'->184, '十五'->15, '二十'->20."""
    if not any(c in _CN_UNITS for c in s):
        return int("".join(str(_CN_DIGITS[c]) for c in s))
    total = cur = 0
    for c in s:
        if c in _CN_DIGITS:
            cur = _CN_DIGITS[c]
        else:
            total += (cur or 1) * _CN_UNITS[c]
            cur = 0
    return total + cur


def _sub(pattern, repl, text: str, ranges: Ranges, group: int = 0) -> tuple[str, Ranges]:
    """re.sub that also keeps the output-char -> original-range list in step.

    Only `group` of each match is replaced. Characters of the replacement all map to the whole
    replaced original range; deleted characters map to nothing.
    """
    pat = re.compile(pattern) if isinstance(pattern, str) else pattern
    out: list[str] = []
    out_r: Ranges = []
    pos = 0
    for m in pat.finditer(text):
        s, e = m.span(group)
        if s < 0 or s == e:
            continue
        out.append(text[pos:s])
        out_r.extend(ranges[pos:s])
        new = repl(m) if callable(repl) else repl
        if new:
            rng = (ranges[s][0], ranges[e - 1][1])
            out.append(new)
            out_r.extend([rng] * len(new))
        pos = e
    out.append(text[pos:])
    out_r.extend(ranges[pos:])
    return "".join(out), out_r


def _strip_markdown_map(text: str, ranges: Ranges) -> tuple[str, Ranges]:
    text, ranges = _sub(_MD_INLINE, "", text, ranges)
    text, ranges = _sub(_MD_HEADING, "", text, ranges, group=2)
    text, ranges = _sub(_MD_BULLET, "", text, ranges, group=2)
    return text, ranges


def strip_markdown(text: str) -> str:
    """Remove only: ** __ ` anywhere; leading #'s (+ spaces); leading '- ', '* ', '> '."""
    return _strip_markdown_map(text, [(i, i + 1) for i in range(len(text))])[0]


def _fullwidth_to_ascii(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19 or 0xFF21 <= o <= 0xFF3A or 0xFF41 <= o <= 0xFF5A:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize_with_map(text: str) -> tuple[str, Ranges]:
    ranges: Ranges = [(i, i + 1) for i in range(len(text))]
    text, ranges = _strip_markdown_map(text, ranges)
    text = _fullwidth_to_ascii(text)      # 1 char -> 1 char: ranges unchanged
    text = text.replace("臺", "台")        # likewise
    text, ranges = _sub(_SPACE, "", text, ranges)
    # Chinese numerals -> Arabic, only inside citation fragments.
    text, ranges = _sub(_ARTICLE_NUM, lambda m: str(cn_to_int(m.group(1))), text, ranges, group=1)
    text, ranges = _sub(_ARTICLE_SUB, lambda m: str(cn_to_int(m.group(1))), text, ranges, group=1)
    text, ranges = _sub(_CASE_YEAR, lambda m: str(cn_to_int(m.group(1))), text, ranges, group=1)
    assert len(text) == len(ranges)
    return text, ranges


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]
