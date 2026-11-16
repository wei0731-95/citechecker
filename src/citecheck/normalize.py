"""Text normalization (SPEC 6.1). Spans elsewhere always index into normalize()'s output."""
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
_ARTICLE_NUM = re.compile(rf"第({_NUM})(?=[條項款號])")
_ARTICLE_SUB = re.compile(rf"(?<=\d條)之({_NUM})")
_CASE_YEAR = re.compile(
    rf"(?<!{_NUMCH})({_NUM})(?=年度?[^\s，。；、：\d]{{1,6}}?字第)")


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


def _fullwidth_to_ascii(text: str) -> str:
    out = []
    for ch in text:
        o = ord(ch)
        if 0xFF10 <= o <= 0xFF19 or 0xFF21 <= o <= 0xFF3A or 0xFF41 <= o <= 0xFF5A:
            out.append(chr(o - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def normalize(text: str) -> str:
    text = _fullwidth_to_ascii(text)
    text = text.replace("臺", "台")
    text = _SPACE.sub("", text)
    # Chinese numerals -> Arabic, only inside citation fragments.
    text = _ARTICLE_NUM.sub(lambda m: f"第{cn_to_int(m.group(1))}", text)
    text = _ARTICLE_SUB.sub(lambda m: f"之{cn_to_int(m.group(1))}", text)
    text = _CASE_YEAR.sub(lambda m: str(cn_to_int(m.group(1))), text)
    return text
