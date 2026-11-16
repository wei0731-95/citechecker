import json

import pytest

from citecheck.judge import (CONFIDENCE_THRESHOLD, SYSTEM_PROMPT, build_user_prompt,
                             judge_passages, parse_response)
from citecheck.llm import CachingClient, MockClient
from citecheck.models import L2Label
from citecheck.models import SegmentRole as R
from citecheck.retrieve import Passage, bigrams, clean_query, retrieve, split_passages
from citecheck.segment import Segment


def P(i, role, text="fake 段落"):
    return Passage(i, role, f"{text}{i}")


def resp(*pairs):
    return json.dumps({"results": [{"id": i + 1, "relation": r, "confidence": c}
                                   for i, (r, c) in enumerate(pairs)]})


def run(roles, pairs):
    passages = [P(i, r) for i, r in enumerate(roles)]
    return judge_passages("fake 主張句", passages, MockClient(resp(*pairs)))


S, C, U = "SUPPORTS", "CONTRADICTS", "UNRELATED"


# ---- SPEC 7.5 rules 1-6
def test_rule1_court_holding():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(S, 0.9), (S, 0.7)])
    assert label == L2Label.COURT_HOLDING and ev.segment_role == R.SUPREME_COURT


def test_rule2_contradicted():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(S, 0.9), (C, 0.8)])
    assert label == L2Label.CONTRADICTED and ev.relation.value == C


def test_rule3_lower_court_view():
    label, _ = run([R.PARTY_CLAIM, R.LOWER_COURT], [(S, 0.9), (S, 0.8)])
    assert label == L2Label.LOWER_COURT_VIEW


def test_rule4_party_argument():
    label, ev = run([R.PARTY_CLAIM, R.SUPREME_COURT], [(S, 0.9), (U, 0.9)])
    assert label == L2Label.PARTY_ARGUMENT and ev.segment_index == 0


def test_rule5_uncertain_for_unknown_or_main():
    label, ev = run([R.UNKNOWN, R.MAIN_TEXT], [(S, 0.7), (C, 0.9)])
    assert label == L2Label.UNCERTAIN and ev.confidence == 0.9


def test_rule6_not_in_judgment():
    label, ev = run([R.SUPREME_COURT, R.PARTY_CLAIM], [(U, 0.9), (U, 0.9)])
    assert label == L2Label.NOT_IN_JUDGMENT and ev is None


def test_low_confidence_is_ignored():
    assert CONFIDENCE_THRESHOLD == 0.6
    label, _ = run([R.SUPREME_COURT], [(S, 0.59)])
    assert label == L2Label.NOT_IN_JUDGMENT
    label, _ = run([R.SUPREME_COURT], [(S, 0.6)])
    assert label == L2Label.COURT_HOLDING


def test_highest_confidence_evidence_chosen():
    _, ev = run([R.SUPREME_COURT, R.SUPREME_COURT], [(S, 0.7), (S, 0.95)])
    assert ev.segment_index == 1


# ---- JSON parse failure path
def test_parse_failure_retries_once_then_succeeds():
    m = MockClient(["not json", resp((S, 0.9))])
    label, _ = judge_passages("x", [P(0, R.SUPREME_COURT)], m)
    assert label == L2Label.COURT_HOLDING and len(m.calls) == 2


def test_parse_failure_twice_is_uncertain():
    m = MockClient(["not json", '{"results":[{"id":1,"relation":"MAYBE","confidence":0.9}]}'])
    label, ev = judge_passages("x", [P(0, R.SUPREME_COURT)], m)
    assert label == L2Label.UNCERTAIN and ev is None and len(m.calls) == 2


def test_parse_accepts_code_fence_and_rejects_bad_ids():
    assert parse_response("```json\n" + resp((S, 0.9)) + "\n```", 1)[0].id == 1
    with pytest.raises(ValueError):
        parse_response(resp((S, 0.9), (S, 0.9)), 1)  # id 2 with only 1 passage
    with pytest.raises(ValueError):
        parse_response('{"results":[{"id":1,"relation":"SUPPORTS","confidence":1.5}]}', 1)


# ---- cache
def test_cache_hit_does_not_call_inner(tmp_path):
    inner = MockClient("answer")
    c = CachingClient(inner, "model-x", tmp_path)
    assert c.complete("sys", "usr") == "answer"
    assert c.complete("sys", "usr") == "answer"
    assert len(inner.calls) == 1
    c.complete("sys", "other")
    assert len(inner.calls) == 2
    CachingClient(inner, "model-y", tmp_path).complete("sys", "usr")  # model is part of the key
    assert len(inner.calls) == 3


def test_bad_response_is_not_served_from_cache_on_retry(tmp_path):
    inner = MockClient(["garbage", resp((S, 0.9))])
    c = CachingClient(inner, "m", tmp_path)
    label, _ = judge_passages("x", [P(0, R.SUPREME_COURT)], c)
    assert label == L2Label.COURT_HOLDING and len(inner.calls) == 2


# ---- prompt / retrieval
def test_prompt_is_verbatim_and_has_no_roles():
    assert SYSTEM_PROMPT.startswith("你是法律文件比對助手。") and SYSTEM_PROMPT.endswith("不要任何其他文字。")
    u = build_user_prompt("主張X", [P(i, R.SUPREME_COURT) for i in range(5)])
    assert u.startswith("書狀主張：\n主張X\n\n判決原文段落：\n[1] fake 段落0\n")
    assert "[5] fake 段落4\n\n請輸出：" in u
    assert "SUPREME" not in u


def test_prompt_with_fewer_passages_drops_missing_lines():
    u = build_user_prompt("主張X", [P(0, R.UNKNOWN)])
    assert "[1] fake 段落0" in u and "[2]" not in u and "{passage" not in u


def test_split_passages_respects_limit_and_skips_header_footer():
    long = "甲乙丙丁。" * 200
    segs = [Segment(0, R.UNKNOWN, "header text", "header", None),
            Segment(1, R.SUPREME_COURT, long, "reason", None),
            Segment(2, R.UNKNOWN, "footer text", "footer", None)]
    ps = split_passages(segs)
    assert all(p.segment_index == 1 and len(p.text) <= 400 for p in ps) and len(ps) == 3


def test_retrieve_ranks_relevant_passage_first():
    ps = [Passage(0, R.PARTY_CLAIM, "假資料：關於房屋租賃契約之終止事由"),
          Passage(1, R.SUPREME_COURT, "假資料：出賣人就買賣標的物之瑕疵應負擔保責任"),
          Passage(2, R.LOWER_COURT, "假資料：訴訟費用由上訴人負擔")]
    top = retrieve("買賣標的物有瑕疵出賣人負擔保責任", ps)
    assert top[0].segment_index == 1 and len(top) == 3


def test_query_cleaning_and_bigrams():
    q = clean_query("應負擔保責任，最高法院109年度台上字第1號判決意旨參照", ["最高法院109年度台上字第1號判決"])
    assert "最高法院" not in q and "參照" not in q
    assert bigrams("買賣，契約") == ["買賣", "契約"]


# ---- OpenAI client (HTTP mocked; no real API)
class _Resp:
    def __init__(self, payload):
        self._p = payload

    def read(self):
        import json as _j
        return _j.dumps(self._p).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openai_client_request_and_parse(monkeypatch):
    import json as _j

    from citecheck.llm import OpenAIClient
    seen = {}

    def fake_urlopen(req, timeout):
        seen["body"] = _j.loads(req.data.decode("utf-8"))
        seen["auth"] = req.headers["Authorization"]
        seen["timeout"] = timeout
        return _Resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = OpenAIClient("model-x", "KEY")
    assert c.complete("sys", "usr") == "ok"
    b = seen["body"]
    assert b["model"] == "model-x" and b["temperature"] == 0 and b["max_completion_tokens"] == 600
    assert b["reasoning_effort"] == "low"
    assert [m["role"] for m in b["messages"]] == ["system", "user"]
    assert seen["auth"] == "Bearer KEY" and seen["timeout"] == 60


def test_openai_client_drops_temperature_when_rejected(monkeypatch):
    import io
    import json as _j
    import urllib.error

    from citecheck.llm import OpenAIClient
    bodies = []

    def fake_urlopen(req, timeout):
        body = _j.loads(req.data.decode("utf-8"))
        bodies.append(body)
        if "temperature" in body:
            raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"temperature unsupported"}'))
        return _Resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert OpenAIClient("m", "K").complete("s", "u") == "ok"
    assert "temperature" in bodies[0] and "temperature" not in bodies[1]
    assert bodies[1]["reasoning_effort"] == "low"  # only the rejected parameter is dropped


def test_client_from_env_provider_switch(monkeypatch):
    from citecheck.llm import LLMConfigError, OpenAIClient, client_from_env
    monkeypatch.setenv("CITECHECK_PROVIDER", "openai")
    monkeypatch.setenv("CITECHECK_MODEL", "m")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMConfigError):
        client_from_env()
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert isinstance(client_from_env(), OpenAIClient)
    monkeypatch.setenv("CITECHECK_PROVIDER", "other")
    with pytest.raises(LLMConfigError):
        client_from_env()


def test_openai_reasoning_effort_env_and_rejection(monkeypatch):
    import io
    import json as _j
    import urllib.error

    from citecheck.llm import OpenAIClient
    monkeypatch.setenv("CITECHECK_REASONING_EFFORT", "off")
    assert "reasoning_effort" not in OpenAIClient("m", "K")._optional
    monkeypatch.setenv("CITECHECK_REASONING_EFFORT", "minimal")
    assert OpenAIClient("m", "K")._optional["reasoning_effort"] == "minimal"
    monkeypatch.delenv("CITECHECK_REASONING_EFFORT")
    bodies = []

    def fake_urlopen(req, timeout):
        body = _j.loads(req.data.decode("utf-8"))
        bodies.append(body)
        if "reasoning_effort" in body:
            raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b'{"error":"reasoning_effort unsupported"}'))
        return _Resp({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert OpenAIClient("m", "K").complete("s", "u") == "ok"
    assert "reasoning_effort" not in bodies[-1] and bodies[-1]["temperature"] == 0
