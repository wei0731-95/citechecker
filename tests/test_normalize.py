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
