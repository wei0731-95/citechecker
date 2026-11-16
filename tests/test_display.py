"""SPEC_V2 section 11: levels, wording and page structure."""
from datetime import date

import pytest

from citecheck.models import (DataCoverage, JudgmentCitation, JudgmentStatus, L2Evidence, L2Label,
                              OtherAuthority, Report, RunStats, SegmentRole, SimilarJudgment, Span,
                              StatuteCitation, StatuteL2Label, StatuteStatus, UnrecognizedSentence)
from citecheck.models import Relation
from citecheck.report import (DISCLAIMER, HEADER_STATEMENT, ZH, describe, laws_age_days, level_of,
                              month_range_note, render_html, stats_line)

BANNED = ("不存在", "偽造", "虛構", "錯誤引用", "建議引用", "應改引")


def st(status, l2=StatuteL2Label.NOT_RUN, not_in_force=False):
    return StatuteCitation(raw="民法第1條", span=Span(start=0, end=5), law_name="民法", article="1",
                           status=status, l2_label=l2, not_in_force_note=not_in_force)


def jd(status, l2=L2Label.NOT_RUN, **kw):
    return JudgmentCitation(raw="最高法院115年度台上字第7號判決", span=Span(start=0, end=10), court="最高法院",
                            year=115, case_word="台上", case_no=7, doc_kind="判決", status=status, l2_label=l2, **kw)


S = StatuteStatus
M = StatuteL2Label

STATUTE_ROWS = [
    (st(S.LAW_NOT_FOUND), "check", "在法律資料中查不到這部法規的名稱。"),
    (st(S.ARTICLE_NOT_FOUND), "check", "這部法規存在，但查不到這一條。"),
    (st(S.PARAGRAPH_NOT_FOUND), "check", "這一條存在，但沒有書狀所寫的這一項。"),
    (st(S.SUBPARAGRAPH_NOT_FOUND), "check", "這一項存在，但沒有書狀所寫的這一款。"),
    (st(S.ARTICLE_DELETED), "notice", "這一條已經刪除。"),
    (st(S.LAW_REPEALED), "notice", "這部法規已經廢止。"),
    (st(S.LAW_NAME_UNCERTAIN), "unknown", "無法確認書狀指的是哪一部法規，請核對法規全名。"),
    (st(S.LAW_OUT_OF_SCOPE), "unknown", "本工具未收錄行政命令與地方法規（施行細則、辦法、自治條例等），無法查核。"),
    (st(S.UNRESOLVED_REF), "unknown", "這裡只寫了條號，看不出是哪一部法規。"),
    (st(S.FOUND, not_in_force=True), "notice",
     "查得到這一條，但全國法規資料庫註明這一條有尚未施行或施行日期另定的情形。本工具顯示的條文可能不是目前有效的版本，請點連結確認。"),
    (st(S.FOUND, M.MISMATCH), "notice",
     "書狀對這一條的說明，與現行條文對不上。本工具只有現行條文；如果書狀引用的是修正前的舊條文，請自行確認。"),
    (st(S.FOUND, M.MATCHES), "ok", "查得到這一條，書狀對它的說明與現行條文大致相符。"),
    (st(S.FOUND, M.NOT_STATED), "ok", "查得到這一條。書狀沒有說明條文內容，所以沒有比對。"),
    (st(S.FOUND, M.UNCERTAIN), "ok", "查得到這一條。本工具無法判斷書狀的說明與條文是否相符，請自行核對。"),
    (st(S.FOUND, M.NOT_RUN), "ok", "查得到這一條。本工具只確認了條號，沒有比對書狀對條文內容的說明。"),
]

J = JudgmentStatus
L = L2Label
JUDGMENT_ROWS = [
    (jd(J.NOT_FOUND_IN_SCOPE), "check", "在最高法院民事裁判的公開資料中查不到這個字號。請到司法院裁判書系統確認字號是否正確。"),
    (jd(J.NOT_FOUND_CIVIL_ONLY), "notice", "在最高法院民事裁判中查不到這個字號。書狀沒有寫明是民事或刑事；如果是刑事裁判，本工具無法查核。"),
    (jd(J.DATA_NOT_COVERED), "unknown", "本工具的資料沒有涵蓋這個年度，無法查核。"),
    (jd(J.OUT_OF_SCOPE), "unknown", "本工具只查最高法院民事判決與裁定，這一筆無法查核。"),
    (jd(J.AMBIGUOUS), "notice", "這個字號對應到不只一件裁判，無法確定書狀指的是哪一件。"),
    (jd(J.FOUND, L.COURT_HOLDING), "ok", "查得到這件裁判，且裁判中最高法院自己的論述有與書狀說法相符的段落。"),
    (jd(J.FOUND, L.CONTRADICTED), "check", "這件裁判中最高法院的論述，與書狀的說法相反。"),
    (jd(J.FOUND, L.PARTY_ARGUMENT), "notice", "與書狀說法相符的內容，在這件裁判裡是當事人的主張，不是法院的見解。"),
    (jd(J.FOUND, L.LOWER_COURT_VIEW), "notice",
     "與書狀說法相符的內容，在這件裁判裡是下級法院的見解。最高法院有沒有接受這個見解，請看下方的主文與裁判原文。"),
    (jd(J.FOUND, L.NOT_IN_JUDGMENT), "notice", "查得到這件裁判，但裁判中找不到與書狀說法對應的內容。"),
    (jd(J.FOUND, L.UNCERTAIN), "unknown", "查得到這個字號，但本工具無法判斷內容是否相符，請自行閱讀裁判原文。"),
    (jd(J.FOUND, L.NOT_RUN), "unknown", "查得到這個字號。本工具沒有比對裁判內容是否與書狀說法相符。"),
]


@pytest.mark.parametrize("c,level,text", STATUTE_ROWS + JUDGMENT_ROWS,
                         ids=[f"{r[0].type}-{r[0].status.value}-{getattr(r[0], 'l2_label').value}" for r in STATUTE_ROWS + JUDGMENT_ROWS])
def test_every_row_of_the_level_tables(c, level, text):
    assert level_of(c) == level
    lv, explanation, notes = describe(c)
    assert (lv, explanation, notes) == (level, text, [])


def test_other_authority_is_unknown_with_its_own_wording():
    a = OtherAuthority(raw="司法院釋字第748號解釋", span=Span(start=0, end=5), kind="釋字")
    assert describe(a) == ("unknown", "本工具無法查核這一類依據，請自行確認。", [])


def test_extra_notes_follow_the_explanation():
    c = jd(J.FOUND, L.UNCERTAIN, court_inferred=True, procedural_dismissal=True)
    _, _, notes = describe(c)
    assert notes == ["書狀沒有寫法院名稱，本工具依字別推定為最高法院。",
                     "這是一件以程序理由駁回的裁定，通常不包含最高法院對法律問題的見解。"]
    assert describe(jd(J.NOT_FOUND_IN_SCOPE, latest_year_note=True))[2] == ["這個年度的案件可能尚未裁判或尚未公開。"]


def test_no_banned_wording_anywhere_in_zh():
    flat = repr(ZH)
    assert not any(b in flat for b in BANNED)


def make_report(**kw):
    text = "依民法第1條，又最高法院115年度台上字第7號判決，另見釋字第748號解釋。最高院（109）台上字第1號判決參照。"
    base = dict(
        input_file="x.txt", generated_at="2026-11-17T00:00:00+08:00",
        data_coverage=DataCoverage(laws_update_date="2026/11/6 上午 12:00:00", months_imported=["202305", "202602", "202603"],
                                   covered_years=[]),
        normalized_text=text, original_text=text,
        statutes=[st(S.FOUND, M.MATCHES)],
        judgments=[jd(J.FOUND, L.PARTY_ARGUMENT, main_text="上訴駁回。", claim_sentence="按某說法",
                      pdf_url="https://example.invalid/x.pdf", jid="TPSV,115,台上,7,20260101,1",
                      l2_evidence=L2Evidence(segment_index=1, segment_role=SegmentRole.PARTY_CLAIM,
                                             passage="當事人主張某說法。", relation=Relation.SUPPORTS, confidence=0.9))],
        other_authorities=[OtherAuthority(raw="釋字第748號解釋", span=Span(start=30, end=38), kind="釋字")],
        unrecognized=[UnrecognizedSentence(text="最高院（109）台上字第1號判決參照", span=Span(start=40, end=60))])
    base["statutes"][0].span = Span(start=1, end=6)
    base["statutes"][0].law_url = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001"
    base["statutes"][0].article_url = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=B0000001&flno=1"
    base["judgments"][0].span = Span(start=9, end=25)
    base.update(kw)
    return Report(**base)


def test_page_structure_and_fixed_wording():
    html = render_html(make_report(), today=date(2026, 11, 17))
    assert html.index(HEADER_STATEMENT) < html.index("摘要") < html.index("書狀原文") < html.index("逐筆明細")
    assert html.index("逐筆明細") < html.index("這幾句話看起來有引用，但本工具沒有辨識出來，請自行核對") < html.index("資料涵蓋")
    assert "法規資料來源：全國法規資料庫；裁判資料來源：司法院開放資料。" in html and DISCLAIMER in html
    assert html.rstrip().endswith("</html>") and html.index(DISCLAIMER) > html.index("法規資料來源")
    assert not any(b in html for b in BANNED) and "<script" not in html and "https://fonts" not in html


def test_summary_counts_the_four_levels_including_other_authorities():
    html = render_html(make_report())
    # 1 ok (statute), 1 notice (judgment), 1 unknown (other authority), 0 check
    import re
    rows = dict(re.findall(r'<span class="badge \w+">(\S+)</span></td><td>(\d+)</td>', html))
    assert rows == {"查得到": "1", "請確認": "0", "請留意": "1", "無法查核": "1"}


def test_every_cited_span_carries_a_text_label_not_only_a_colour():
    html = render_html(make_report())
    assert 'class="tag">查得到</span>' in html and 'class="tag">請留意</span>' in html
    assert "民法第1條" in html and "回到全文" in html


def test_links_open_in_a_new_tab_safely_and_use_the_fixed_text():
    html = render_html(make_report())
    for text in ("在全國法規資料庫看這一條", "在全國法規資料庫看這部法規", "看裁判原文（司法院 PDF）"):
        assert f'target="_blank" rel="noopener">{text}</a>' in html
    assert "這件裁判的主文" in html and "上訴駁回。" in html and "判決中最相關的段落" in html


def test_footer_shows_coverage_generation_conditions_and_stale_laws_note():
    r = make_report(llm_model="某模型", versions={"segment_rules": 1, "schema": 2}, stats=RunStats(api_calls=3, seconds=1.5))
    html = render_html(r, today=date(2026, 11, 17))
    assert "法規資料日期 2026/11/6" in html and "202305–202603（缺漏 32 個月）" in html
    assert "模型 某模型" in html and "segment_rules 1" in html and stats_line(r.stats) in html
    assert "法規資料已超過 90 天沒有更新，近期修正的條文可能沒有反映。" not in html
    late = render_html(r, today=date(2027, 2, 10))
    assert "法規資料已超過 90 天沒有更新，近期修正的條文可能沒有反映。" in late
    assert "模型 未使用" in render_html(make_report())


def test_law_age_and_month_range_helpers():
    assert laws_age_days("2026/11/6 上午 12:00:00", date(2026, 11, 16)) == 10
    assert laws_age_days(None) is None and laws_age_days("無日期") is None
    assert month_range_note([]) == "尚未匯入裁判資料"
    assert month_range_note(["202602", "202603"]) == "202602–202603（缺漏 0 個月）"


def test_skipped_by_cap_note_is_shown_only_when_something_was_skipped():
    assert "這份書狀的引用太多，有 4 筆沒有比對內容。" in render_html(make_report(stats=RunStats(skipped_by_cap=4)))
    assert "引用太多" not in render_html(make_report())


def test_similar_judgments_block_only_when_present():
    sim = SimilarJudgment(jid="a", display="最高法院115年度台上字第9號民事判決", passage="相近段落。",
                          confidence=0.9, pdf_url="https://example.invalid/s.pdf")
    r = make_report()
    assert "論述相近的最高法院民事裁判（僅供自行查閱）" not in render_html(r)
    r.judgments[0].similar = [sim]
    html = render_html(r)
    assert "論述相近的最高法院民事裁判（僅供自行查閱）" in html and "最高法院115年度台上字第9號民事判決" in html
    assert "本工具沒有判斷它們是否適用於您的案件，也不保證是最新或唯一的見解。" in html


def test_report_shows_the_original_text_not_the_normalized_one():
    text = "依 **民法** 第一百八十四條規定"
    r = make_report(original_text=text, normalized_text="依 民法 第184條規定", statutes=[], judgments=[],
                    other_authorities=[], unrecognized=[])
    assert "**民法**" in render_html(r)


# ------------------------------------------------------------------ SPEC_V3 Phase H: page usability
def test_overview_lists_every_citation_most_urgent_first():
    import re
    r = make_report()
    r.statutes.append(StatuteCitation(raw="民法第9條", span=Span(start=26, end=29), law_name="民法", article="9",
                                      status=S.ARTICLE_NOT_FOUND))
    html = render_html(r)
    ov = html[html.index("各筆引用一覽"):html.index("書狀原文")]
    labels = re.findall(r'<td class="lv"><span class="badge \w+">(\S+)</span>', ov)
    assert labels == ["請確認", "請留意", "無法查核", "查得到"]
    assert all(f'href="#c{n}"' in ov for n in range(1, 5))


def test_summary_tiles_explain_each_level():
    from citecheck.report import LEVEL_HELP
    html = render_html(make_report())
    assert all(h in html for h in LEVEL_HELP.values())
    assert html.count('class="card"') == 3 and 'data-level="notice"' in html


def test_back_link_only_on_the_served_page():
    from citecheck.report import BACK_LINK_TEXT
    assert BACK_LINK_TEXT not in render_html(make_report())
    html = render_html(make_report(), back_link=True)
    assert f'href="/">{BACK_LINK_TEXT}</a>' in html and "<script" not in html


def test_continuation_shows_which_article_it_belongs_to():
    r = make_report()
    r.statutes.append(StatuteCitation(raw="第5項", span=Span(start=26, end=29), law_name="民法", article="195-1",
                                      paragraph=5, status=S.PARAGRAPH_NOT_FOUND, is_continuation=True))
    html = render_html(r)
    assert html.count("（民法第195條之1）") == 2           # overview row + detail card
    assert "（民法第1條）" not in html                      # ordinary citations are unchanged
