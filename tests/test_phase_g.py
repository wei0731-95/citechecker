"""SPEC_V2 Phase G: full-text index (G1), similar judgments (G2). No real API, no real database."""
import json
import re

import pytest

from citecheck.db import connect
from citecheck.index import build_fts, fts_query, fts_search, has_fts
from citecheck.judge import SYSTEM_PROMPT
from citecheck.models import JudgmentCitation, JudgmentStatus, L2Label, RunStats, Span
from citecheck.suggest import MAX_SHOWN, SUGGEST_THRESHOLD, apply_suggestions, display_of

CLAIM = "買賣契約之出賣人負有交付標的物並移轉所有權之義務"      # 25 characters


# ---------------------------------------------------------------- G1: fts_query
def test_fts_query_takes_every_3_character_window_in_order():
    assert fts_query("甲乙丙丁") == '"甲乙丙" OR "乙丙丁"'


def test_fts_query_ignores_short_runs_and_splits_on_punctuation():
    assert fts_query("甲乙，丙丁戊。己") == '"丙丁戊"'
    assert fts_query("甲乙 ab") == ""


def test_fts_query_deduplicates_keeping_first_seen_order():
    assert fts_query("甲乙丙。甲乙丙。乙丙丁") == '"甲乙丙" OR "乙丙丁"'


def test_fts_query_keeps_only_the_first_60_terms():
    q = fts_query("".join(chr(0x4e00 + i) for i in range(100)))
    assert q.count(" OR ") == 59 and q.startswith('"一') and q.endswith('"')


def test_fts_query_empty_input_gives_empty_string():
    assert fts_query("") == "" and fts_query("，。") == ""


# ---------------------------------------------------------------- helpers
def add(con, jid, text, *, role="SUPREME_COURT", doc_kind="判決", dismissal=0, seq=0, year=110, no=1):
    con.execute("INSERT OR IGNORE INTO judgments(jid, year, case_word, case_no, doc_kind, date, title, full_text, "
                "pdf_url, month, procedural_dismissal) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (jid, year, "台上", no, doc_kind, "1100101", "", "", f"http://pdf/{jid}", "202101", dismissal))
    con.execute("INSERT INTO passages(jid, seq, segment_index, role, text) VALUES(?,?,?,?,?)",
                (jid, seq, seq, role, text))


@pytest.fixture()
def con(tmp_path):
    c = connect(tmp_path / "g.db")
    yield c
    c.close()


class Router:
    """Answers the comparison prompt from `judge` and the preparation prompt from `prep`."""

    def __init__(self, judge, prep=lambda user: '{"quoted":[]}'):
        self.judge, self.prep, self.calls = judge, prep, []

    @property
    def api_calls(self):
        return len(self.calls)

    def complete(self, system, user, max_tokens=600):
        self.calls.append((system, user))
        return self.judge(user) if system == SYSTEM_PROMPT else self.prep(user)


def judge_by(table):
    """table: {text-prefix: (relation, confidence)}; unknown passages are UNRELATED 0.9."""
    def fn(user):
        out = []
        for i, text in re.findall(r"^\[(\d)\] (.*)$", user, re.M):
            rel, conf = next((v for k, v in table.items() if text.startswith(k)), ("UNRELATED", 0.9))
            out.append({"id": int(i), "relation": rel, "confidence": conf})
        return json.dumps({"results": out})
    return fn


def cite(claim=CLAIM, status=JudgmentStatus.NOT_FOUND_IN_SCOPE, label=L2Label.NOT_RUN, jid=None, raw="某號判決"):
    return JudgmentCitation(raw=raw, span=Span(start=0, end=1), court="最高法院", year=1, case_word="台上",
                            case_no=1, doc_kind="判決", status=status, jid=jid, claim_sentence=claim, l2_label=label)


def doc(con, jid, suffix, **kw):
    add(con, jid, CLAIM + "。" + suffix + "。", **kw)


# ---------------------------------------------------------------- G1: the table
def test_fts_holds_only_supreme_court_passages_of_ordinary_judgments(con):
    add(con, "J1", "本院見解甲乙丙丁。")
    add(con, "J2", "當事人主張甲乙丙丁。", role="PARTY_CLAIM")
    add(con, "J3", "本院見解甲乙丙丁。", doc_kind="其他")
    add(con, "J4", "本院見解甲乙丙丁。", doc_kind="裁定", dismissal=1)
    add(con, "J5", "本院見解甲乙丙丁。", doc_kind="裁定")
    con.commit()
    assert build_fts(con) == 2
    assert {r[0] for r in fts_search(con, "本院見解甲乙丙丁")} == {"J1", "J5"}


def test_build_fts_is_a_full_rebuild_and_has_fts_reports_it(con):
    assert not has_fts(con)
    add(con, "J1", "本院見解甲乙丙丁。")
    con.commit()
    build_fts(con)
    build_fts(con)
    assert has_fts(con) and con.execute("SELECT COUNT(*) FROM passages_fts").fetchone()[0] == 1


# ---------------------------------------------------------------- G2
def run(con, cites, client):
    con.commit()
    build_fts(con)
    stats = RunStats()
    apply_suggestions(cites, con, client, stats)
    return stats


def test_threshold_is_0_8(con):
    doc(con, "A", "甲甲甲")
    doc(con, "B", "乙乙乙")
    c = cite()
    run(con, [c], Router(judge_by({CLAIM + "。甲": ("SUPPORTS", 0.8), CLAIM + "。乙": ("SUPPORTS", 0.79)})))
    assert SUGGEST_THRESHOLD == 0.8 and [s.jid for s in c.similar] == ["A"]


def test_only_supports_counts(con):
    doc(con, "A", "甲甲甲")
    c = cite()
    run(con, [c], Router(judge_by({"": ("CONTRADICTS", 0.99)})))
    assert c.similar == []


def test_one_passage_per_judgment_and_the_cited_judgment_is_skipped(con):
    add(con, "A", CLAIM + "。甲。", seq=0)
    add(con, "A", CLAIM + "。乙。", seq=1)
    doc(con, "SELF", "丙丙丙")
    doc(con, "B", "丁丁丁")
    c = cite(status=JudgmentStatus.FOUND, label=L2Label.NOT_IN_JUDGMENT, jid="SELF")
    client = Router(judge_by({"": ("SUPPORTS", 0.9)}))
    run(con, [c], client)
    assert sorted(s.jid for s in c.similar) == ["A", "B"]
    first_prompt = next(u for s, u in client.calls if s == SYSTEM_PROMPT)
    assert first_prompt.count("\n[") == 2 and "丙丙丙" not in first_prompt          # A once, B, and not SELF


def test_paraphrased_passage_is_dropped(con):
    doc(con, "A", "甲甲甲")
    doc(con, "B", "乙乙乙")
    c = cite()

    def prep(user):                      # marks the sentence "[1]" of whichever judgment contains 甲甲甲 as quoted
        return '{"quoted":[{"id":2,"speaker":"LOWER_COURT"}]}' if "甲甲甲" in user else '{"quoted":[]}'

    run(con, [c], Router(judge_by({"": ("SUPPORTS", 0.9)}), prep))
    assert [s.jid for s in c.similar] == ["B"]


def test_failed_preparation_drops_the_judgment(con):
    doc(con, "A", "甲甲甲")
    doc(con, "B", "乙乙乙")
    c = cite()
    run(con, [c], Router(judge_by({"": ("SUPPORTS", 0.9)}), lambda u: "not json" if "甲甲甲" in u else '{"quoted":[]}'))
    assert [s.jid for s in c.similar] == ["B"]


def test_at_most_three_highest_confidence(con):
    for i, name in enumerate("ABCDE"):
        doc(con, name, name * 3)
    c = cite()
    table = {CLAIM + "。" + n * 3: ("SUPPORTS", conf) for n, conf in zip("ABCDE", (0.85, 0.95, 0.9, 0.81, 0.99))}
    run(con, [c], Router(judge_by(table)))
    assert MAX_SHOWN == 3 and [s.jid for s in c.similar] == ["E", "B", "C"]


def test_claim_too_short_after_cleaning_does_not_trigger(con):
    doc(con, "A", "甲甲甲")
    short = cite(claim="買賣契約之出賣人負有交付義務")                 # 14 characters
    client = Router(judge_by({"": ("SUPPORTS", 0.9)}))
    run(con, [short], client)
    assert short.similar == [] and client.calls == []
    padded = cite(claim=CLAIM + "可資參照", raw="某號判決")              # filler words do not count
    assert len(padded.claim_sentence) > 15
    run(con, [padded], client)
    assert padded.similar != []


def test_citation_without_a_problem_does_not_trigger(con):
    doc(con, "A", "甲甲甲")
    ok = cite(status=JudgmentStatus.FOUND, label=L2Label.COURT_HOLDING, jid="X")
    unc = cite(status=JudgmentStatus.FOUND, label=L2Label.UNCERTAIN, jid="X")
    oos = cite(status=JudgmentStatus.OUT_OF_SCOPE)
    client = Router(judge_by({"": ("SUPPORTS", 0.9)}))
    run(con, [ok, unc, oos], client)
    assert client.calls == [] and ok.similar == unc.similar == oos.similar == []


@pytest.mark.parametrize("status", [JudgmentStatus.NOT_FOUND_IN_SCOPE, JudgmentStatus.NOT_FOUND_CIVIL_ONLY,
                                    JudgmentStatus.DATA_NOT_COVERED])
def test_not_found_statuses_trigger(con, status):
    doc(con, "A", "甲甲甲")
    c = cite(status=status)
    run(con, [c], Router(judge_by({"": ("SUPPORTS", 0.9)})))
    assert [s.jid for s in c.similar] == ["A"]


@pytest.mark.parametrize("label", [L2Label.NOT_IN_JUDGMENT, L2Label.PARTY_ARGUMENT, L2Label.LOWER_COURT_VIEW,
                                   L2Label.CONTRADICTED])
def test_problem_labels_trigger(con, label):
    doc(con, "A", "甲甲甲")
    c = cite(status=JudgmentStatus.FOUND, label=label, jid="X")
    run(con, [c], Router(judge_by({"": ("SUPPORTS", 0.9)})))
    assert [s.jid for s in c.similar] == ["A"]


def test_same_claim_shares_one_comparison_call(con):
    doc(con, "A", "甲甲甲")
    a, b = cite(raw="甲號判決"), cite(raw="乙號判決")
    client = Router(judge_by({"": ("SUPPORTS", 0.9)}))
    run(con, [a, b], client)
    assert sum(1 for s, _ in client.calls if s == SYSTEM_PROMPT) == 1
    assert [s.jid for s in a.similar] == [s.jid for s in b.similar] == ["A"]


def test_without_a_client_or_without_the_table_nothing_happens(con):
    doc(con, "A", "甲甲甲")
    con.commit()
    c = cite()
    apply_suggestions([c], con, None)
    apply_suggestions([c], con, Router(judge_by({"": ("SUPPORTS", 0.9)})))     # no passages_fts yet
    assert c.similar == []


def test_display_and_passage_length(con):
    add(con, "A", CLAIM + "。" + "甲" * 600 + "。", year=111, no=77, doc_kind="裁定")
    c = cite()
    run(con, [c], Router(judge_by({"": ("SUPPORTS", 0.9)})))
    s = c.similar[0]
    assert s.display == display_of(111, "台上", 77, "裁定") == "最高法院111年度台上字第77號民事裁定"
    assert len(s.passage) == 400 and s.pdf_url == "http://pdf/A" and s.confidence == 0.9


def test_api_cap_stops_suggestions(con):
    doc(con, "A", "甲甲甲")
    c = cite()
    client = Router(judge_by({"": ("SUPPORTS", 0.9)}))
    client.calls = [("x", "y")] * 120                                # already at MAX_API_CALLS_PER_CHECK
    stats = run(con, [c], client)
    assert c.similar == [] and stats.skipped_by_cap == 1
