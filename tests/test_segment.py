from citecheck.models import SegmentRole as R
from citecheck.segment import role_of, segment

# fake_ judgment text, indented style (continuation lines indented 4 spaces)
FAKE_A = """最高法院民事判決
115年度台上字第1號
上  訴  人  甲公司
被 上訴 人  乙
上列當事人間請求損害賠償事件，上訴人對於原審判決，提起上訴，本院判決如下：
    主  文
上訴駁回。
第三審訴訟費用由上訴人負擔。
    理  由
一、本件被上訴人主張：伊與上訴人訂立契約，上訴人未依約
    履行，致伊受損害等情。
二、上訴人則以：伊已履行完畢等語，資為抗辯。
三、原審判決上訴人敗訴，係以：契約有效，上訴人
    應負賠償責任。
  ㈠上訴人未舉證其已履行。
四、本院之判斷：
  ㈠按當事人主張有利於己之事實者，應負舉證責任。
  ㈡上訴論旨，指摘原判決不當，非有理由。
五、據上論結，本件上訴為無理由。
中　　華　　民　　國　　115 　年　　3 　　月　　31　　日
                      最高法院民事第一庭
"""

# flush-left style: no indentation, short last line ends the paragraph
FAKE_B = """最高法院民事判決
115年度台上字第2號
上  訴  人  甲
    主  文
上訴駁回。
    理  由
一、按當事人主張有利於己之事實者，應負舉證責任，此為民事
訴訟法所明定。
惟查原審已依證據認定事實，並無違誤。
二、上訴論旨指摘原判決不當，求予廢棄，非有理由。
據上論結，本件上訴為無理由。
中　　華　　民　　國　　115 　年　　3 　　月　　31　　日
"""


def roles(text):
    return [(s.part, s.role) for s in segment(text)]


def test_parts_and_main_text():
    segs = segment(FAKE_A)
    assert segs[0].part == "header" and segs[0].role == R.UNKNOWN
    assert [s.role for s in segs if s.part == "main"] == [R.MAIN_TEXT, R.MAIN_TEXT]
    assert segs[-1].part == "footer"


def test_roles_with_state_carry_over_indented_style():
    reason = [(s.text[:6], s.role) for s in segment(FAKE_A) if s.part == "reason"]
    assert [r for _, r in reason] == [
        R.PARTY_CLAIM,      # 一、本件被上訴人主張
        R.PARTY_CLAIM,      # 二、上訴人則以
        R.LOWER_COURT,      # 三、原審…
        R.LOWER_COURT,      # ㈠ carried over from 三
        R.SUPREME_COURT,    # 四、本院之判斷
        R.SUPREME_COURT,    # ㈠按
        R.PARTY_CLAIM,      # ㈡上訴論旨 at paragraph start switches
        R.SUPREME_COURT,    # 五、據上論結
    ]


def test_wrapped_lines_are_joined():
    segs = [s for s in segment(FAKE_A) if s.part == "reason"]
    assert "未依約履行" in segs[0].text


def test_flush_left_style_splits_on_short_last_line():
    reason = [s for s in segment(FAKE_B) if s.part == "reason"]
    assert [s.role for s in reason] == [R.SUPREME_COURT, R.SUPREME_COURT, R.PARTY_CLAIM, R.SUPREME_COURT]


def test_mid_sentence_shangsu_lunzhi_does_not_switch():
    # 上訴論旨 inside a paragraph that opens with 本院 must keep SUPREME_COURT
    role, _ = role_of("本院按上訴論旨，指摘原判決不當，非有理由。")
    assert role == R.SUPREME_COURT


def test_unmarked_paragraph_before_any_marker_is_unknown():
    text = FAKE_A.replace("一、本件被上訴人主張：", "一、茲就本件事實說明：")
    first = [s for s in segment(text) if s.part == "reason"][0]
    assert first.role == R.UNKNOWN


def test_digit_wrap_is_not_enumerator():
    text = FAKE_A.replace("致伊受損害等情。", "地號\n    0、000等情。")
    segs = [s for s in segment(text) if s.part == "reason"]
    assert len(segs) == 8


FAKE_RULING = """最高法院民事裁定
115年度台上字第3號
上  訴  人  甲
    主  文
上訴駁回。
    理  由
一、上訴第三審法院，非以原判決違背法令為理由，不得為之。
二、上訴人對於原判決提起上訴，雖以該判決違背法令為由，惟核其上訴理由狀所載內容，
    係就原審取捨證據之職權行使所論斷，難認已合法表明上訴理由。
\uf6ae據上論結，本件上訴為不合法。
中　　華　　民　　國　　115 　年　　3 　　月　　31　　日
"""


def test_ruling_unmarked_reasoning_defaults_to_supreme_court():
    reason = [s for s in segment(FAKE_RULING) if s.part == "reason"]
    assert [s.role for s in reason] == [R.SUPREME_COURT] * 3


def test_judgment_unmarked_first_paragraph_stays_unknown():
    text = FAKE_RULING.replace("民事裁定", "民事判決")
    assert [s.role for s in segment(text) if s.part == "reason"][0] == R.UNKNOWN


def test_private_use_glyph_is_an_enumerator():
    reason = [s for s in segment(FAKE_RULING) if s.part == "reason"]
    assert reason[-1].marker == "據上論結" and reason[-1].text.startswith("\uf6ae")


# ------------------------------------------------------------------ SPEC_V3 H1
def _fake(reason_lines):
    return ("最高法院民事判決\n115年度台上字第9號\n上  訴  人  甲\n    主  文\n上訴駁回。\n    理  由\n"
            + "\n".join(reason_lines)
            + "\n中　　華　　民　　國　　115 　年　　3 　　月　　31　　日\n")


def reason_roles(text):
    return [(s.text, s.role) for s in segment(text) if s.part == "reason"]


H1_BASE = ["一、上訴人主張：甲。", "二、原審以：", "㈠查乙事實。", "㈡按丙見解。",
           "㈢從而上訴人之請求為無理由等詞，為其判斷之基礎。"]


def test_h1_lower_court_round_is_backfilled():
    got = reason_roles(_fake(H1_BASE + ["三、按丁見解。"]))
    assert len(got) == 6
    assert [r for _, r in got[1:5]] == [R.LOWER_COURT] * 4
    assert got[5][1] == R.SUPREME_COURT


def test_h1_unmarked_paragraph_after_end_sentence_is_supreme_court():
    got = reason_roles(_fake(H1_BASE + ["四、關於廢棄發回部分：", "戊見解。"]))
    assert [t for t, _ in got[-2:]] == ["四、關於廢棄發回部分：", "戊見解。"]
    assert [r for _, r in got[-2:]] == [R.SUPREME_COURT, R.SUPREME_COURT]


def test_h1_split_at_mid_paragraph_end_sentence():
    got = reason_roles(_fake(["二、原審以：乙見解等詞，為其判斷之基礎。惟按丙見解。"]))
    assert got == [("二、原審以：乙見解等詞，為其判斷之基礎。", R.LOWER_COURT), ("惟按丙見解。", R.SUPREME_COURT)]


def test_h1_two_rounds_do_not_backfill_across():
    got = reason_roles(_fake(["二、原審以：甲等詞，為其判斷之基礎。", "三、按乙見解。",
                              "四、原審就另一部分以：丙等詞，為其判斷之基礎。", "五、按丁見解。"]))
    assert [r for _, r in got] == [R.LOWER_COURT, R.SUPREME_COURT, R.LOWER_COURT, R.SUPREME_COURT]


def test_h1_end_sentence_without_lower_marker_changes_nothing_backwards():
    got = reason_roles(_fake(["一、上訴人主張：甲。", "㈠乙事實。", "㈡從而丙等詞，為其判斷之基礎。", "三、丁見解。"]))
    assert [r for _, r in got] == [R.PARTY_CLAIM, R.PARTY_CLAIM, R.PARTY_CLAIM, R.SUPREME_COURT]
