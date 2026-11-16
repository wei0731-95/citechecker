import pytest

from citecheck.normalize import cn_to_int, normalize


@pytest.mark.parametrize("s,n", [("一○九", 109), ("一〇九", 109), ("一百零九", 109),
                                  ("一百八十四", 184), ("十五", 15), ("二十", 20), ("一二三", 123)])
def test_cn_to_int(s, n):
    assert cn_to_int(s) == n


def test_fullwidth_digits_and_letters():
    assert normalize("第１８４條ＡＢｃ") == "第184條ABc"


def test_tai_replaced():
    assert normalize("臺灣高等法院") == "台灣高等法院"


def test_spaces_removed_around_digits():
    assert normalize("109 年度台上字第 123 號") == "109年度台上字第123號"


def test_case_ref_chinese_numbers():
    assert normalize("一○九年度台上字第一二三號") == "109年度台上字第123號"


def test_article_chinese_numbers():
    assert normalize("第一百八十四條") == "第184條"
    assert normalize("第十五條") == "第15條"
    assert normalize("第二十條") == "第20條"
    assert normalize("第一項第二款") == "第1項第2款"


def test_article_with_sub_number():
    assert normalize("第一百八十四條之一") == "第184條之1"


def test_year_twenty_in_case_ref():
    assert normalize("二十年度台上字第5號") == "20年度台上字第5號"


def test_numbers_outside_citations_untouched():
    s = "經過一年，第一審判決，共三人"
    assert normalize(s) == s


# ---- SPEC_V2 A7: Markdown symbols
from citecheck.normalize import normalize_with_map, strip_markdown  # noqa: E402


def test_md_bold_and_underscore_and_backtick():
    assert strip_markdown("最高法院 **109年度台上字第123號** 判決") == "最高法院 109年度台上字第123號 判決"
    assert strip_markdown("__民法__第184條") == "民法第184條"
    assert strip_markdown("見`民法第1條`") == "見民法第1條"


def test_md_heading_marks_removed_with_following_spaces():
    assert strip_markdown("## 壹、事實\n  ### 二、理由") == "壹、事實\n  二、理由"


def test_md_list_and_quote_marks_removed():
    assert strip_markdown("- 民法第1條\n  * 民法第2條\n> 民法第3條") == "民法第1條\n  民法第2條\n民法第3條"


def test_md_symbols_elsewhere_untouched():
    s = "原告請求 5-3 元，a - b，x > y，a*b"
    assert strip_markdown(s) == s


# ---- SPEC_V2 A8: offset map
def test_map_lengths_and_replaced_fragment_maps_to_whole_original():
    text = "依 **民法** 第一百八十四條"
    out, rng = normalize_with_map(text)
    assert out == "依 民法 第184條" and len(rng) == len(out)
    i = out.index("184")
    assert [text[rng[i + k][0]:rng[i + k][1]] for k in range(3)] == ["一百八十四"] * 3
    assert text[rng[out.index("民")][0]:rng[out.index("民")][1]] == "民"


def test_map_deleted_chars_have_no_output_char():
    text = "第 123 號"
    out, rng = normalize_with_map(text)
    assert out == "第123號"
    assert [text[a:b] for a, b in rng] == ["第", "1", "2", "3", "號"]
