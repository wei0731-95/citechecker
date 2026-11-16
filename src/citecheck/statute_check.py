"""Paragraph / subparagraph structure of a statute article (SPEC_V2 B1).

Phase T needs parse_article() to check gold labels; Phase B wires it into verify_statute.
"""
import re

from .normalize import cn_to_int

STATUTE_DEEP_CHECK_LAWS = ["民法"]

_ITEM = re.compile(r"^([一二三四五六七八九十]+)、")          # 款
_SUB = re.compile(r"^（[一二三四五六七八九十]+）|^\([一二三四五六七八九十]+\)")   # 目 (ignored)
_END = "。：︰"


def parse_article(text: str) -> list[list[int]] | None:
    """One list per 項 holding that 項's 款 numbers (e.g. 民法第184條 -> [[], []]).

    Returns None when the structure cannot be read reliably.
    """
    lines = [ln.strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    paragraphs: list[list[int]] = []
    ends_ok = True
    last_para_line = ""
    for ln in lines:
        m = _ITEM.match(ln)
        if m:
            if not paragraphs:
                return None                      # first line is a 款
            paragraphs[-1].append(cn_to_int(m.group(1)))
            continue
        if _SUB.match(ln):
            if not paragraphs:
                return None                      # first line is a 目
            continue
        if paragraphs and last_para_line and last_para_line[-1] not in _END:
            ends_ok = False
        paragraphs.append([])
        last_para_line = ln
    if not paragraphs:
        return None
    if last_para_line[-1] not in _END:
        ends_ok = False
    if not ends_ok:
        return None
    for items in paragraphs:
        if items != list(range(1, len(items) + 1)):
            return None                          # 款 not 1,2,3,... in order
    return paragraphs
