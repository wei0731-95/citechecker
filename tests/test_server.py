"""SPEC_V2 Phase D: the local web page. Servers run on a random port; no real API, no real database."""
import http.client
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from citecheck.db import connect, set_meta
from citecheck.llm import LLMConfigError
from citecheck.report import HEADER_STATEMENT
from citecheck.server import (BUTTON_BUSY, L2_LABEL, MSG_EMPTY, MSG_TOO_LONG, make_server)

ROOT = Path(__file__).parent.parent


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    con = connect(db)
    con.execute("INSERT INTO laws(law_name, modified_date) VALUES('民法','2025')")
    con.execute("INSERT INTO articles VALUES('民法','184','因故意或過失，不法侵害他人之權利者，負損害賠償責任。\n違反保護他人之法律者，負賠償責任。')")
    set_meta(con, "months_imported", json.dumps(["202601", "202602"]))
    con.commit()
    con.close()
    monkeypatch.chdir(ROOT)                      # the handler reads data/law_aliases.json etc. relative to cwd
    server = make_server("127.0.0.1", 0, db)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    server.server_close()


def base(srv):
    return f"http://127.0.0.1:{srv.server_address[1]}"


def post(srv, fields, headers=None):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(base(srv) + "/check", data=data, headers=headers or {})
    with urllib.request.urlopen(req) as r:
        return r.status, r.read().decode("utf-8")


def raw_request(srv, method, path, headers, body=b""):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    for k, v in headers.items():
        c.putheader(k, v)
    c.endheaders(body)
    r = c.getresponse()
    out = (r.status, r.read().decode("utf-8", "replace"))
    c.close()
    return out


def test_get_form_has_the_fixed_pieces(srv):
    with urllib.request.urlopen(base(srv) + "/") as r:
        html = r.read().decode("utf-8")
        assert r.status == 200
    assert HEADER_STATEMENT in html
    assert 'name="text"' in html and 'name="l2"' in html and "<button" in html
    assert L2_LABEL in html and "checked" not in html          # off by default
    assert BUTTON_BUSY in html and html.count("<script") == 1  # the one allowed inline script
    assert "http://" not in html.replace("http://127.0.0.1", "") and "https://" not in html   # no external resources


def test_post_returns_the_report(srv):
    status, html = post(srv, {"text": "依民法第184條第1項規定請求損害賠償。"})
    assert status == 200 and HEADER_STATEMENT in html
    assert "民法第184條第1項" in html and "查得到" in html and "本工具僅提供引用查核輔助，不構成法律意見。" in html
    assert "<script" not in html


def test_text_over_50000_characters_is_refused_not_truncated(srv):
    status, html = post(srv, {"text": "a" * 50001})
    assert status == 200 and MSG_TOO_LONG in html and "引用查核報告" not in html


def test_exactly_50000_characters_is_accepted(srv):
    status, html = post(srv, {"text": "a" * 50000})
    assert status == 200 and MSG_TOO_LONG not in html and "引用查核報告" in html


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_blank_input(srv, text):
    status, html = post(srv, {"text": text})
    assert status == 200 and MSG_EMPTY in html


def test_l2_checked_without_environment_shows_the_form_with_variable_names(srv, monkeypatch):
    for v in ("CITECHECK_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CITECHECK_PROVIDER"):
        monkeypatch.delenv(v, raising=False)
    status, html = post(srv, {"text": "依民法第184條規定。", "l2": "1"})
    assert status == 200 and "CITECHECK_MODEL" in html and "ANTHROPIC_API_KEY" in html and "OPENAI_API_KEY" in html
    assert "引用查核報告" not in html and "checked" in html


def test_wrong_host_is_403(srv):
    for method in ("GET", "POST"):
        status, _ = raw_request(srv, method, "/" if method == "GET" else "/check",
                                {"Host": "evil.example:80", "Content-Length": "0"})
        assert status == 403
    port = srv.server_address[1]
    assert raw_request(srv, "GET", "/", {"Host": f"localhost:{port}"})[0] == 200


def test_foreign_origin_is_403_but_local_origin_is_fine(srv):
    port = srv.server_address[1]
    body = b"text=abc"
    common = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(body)),
              "Content-Type": "application/x-www-form-urlencoded"}
    assert raw_request(srv, "POST", "/check", {**common, "Origin": "http://evil.example"}, body)[0] == 403
    assert raw_request(srv, "POST", "/check", {**common, "Origin": f"http://localhost:{port}"}, body)[0] == 200
    assert raw_request(srv, "POST", "/check", {**common, "Origin": f"http://127.0.0.1:{port}"}, body)[0] == 200


def test_oversized_or_missing_content_length_is_413_without_reading_the_body(srv):
    port = srv.server_address[1]
    assert raw_request(srv, "POST", "/check", {"Host": f"127.0.0.1:{port}", "Content-Length": "400001"})[0] == 413
    assert raw_request(srv, "POST", "/check", {"Host": f"127.0.0.1:{port}", "Content-Length": "abc"})[0] == 413
    assert raw_request(srv, "POST", "/check", {"Host": f"127.0.0.1:{port}"})[0] == 413


def test_only_two_paths(srv):
    for path in ("/foo", "/check", "/static/x", "/index.html"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base(srv) + path)
        assert e.value.code == 404
    req = urllib.request.Request(base(srv) + "/other", data=b"text=a")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 404


def test_pasted_html_is_escaped_everywhere(srv):
    payload = "<script>alert(1)</script>依民法第184條規定。"
    _, html = post(srv, {"text": payload})
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html
    # an error page that echoes the text back (textarea) must escape it too
    status, html = post(srv, {"text": payload + "a" * 50000})
    assert "<script>alert" not in html and "&lt;script&gt;alert" in html


def _snapshot(folder):
    """{file: (mtime, size)}; data/raw (tens of thousands of files, slow on a bind mount) is skipped."""
    import os
    out = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not (d == "raw" and Path(root).name == "data")]
        for f in files:
            p = Path(root) / f
            out[p] = (p.stat().st_mtime_ns, p.stat().st_size)
    return out


def test_nothing_pasted_reaches_the_disk(srv, tmp_path, capfd):
    marker = "獨特標記字串XYZ12345"
    before = {f: _snapshot(f) for f in (ROOT / "data", tmp_path)}      # stat only: data/raw is huge
    post(srv, {"text": f"{marker}。依民法第184條規定。"})
    for folder, snap in before.items():
        after = _snapshot(folder)
        changed = [p for p, st in after.items() if snap.get(p) != st]
        for p in changed:                                              # read only new or modified files
            assert marker.encode("utf-8") not in p.read_bytes(), p
    assert marker not in capfd.readouterr().err                        # and not in the server log either


def test_each_request_uses_its_own_connection(srv):
    # two requests in a row must both work (a shared connection would be closed by the first)
    assert post(srv, {"text": "依民法第184條規定。"})[0] == 200
    assert post(srv, {"text": "依民法第184條規定。"})[0] == 200


def test_missing_index_is_reported_on_the_form_when_l2_is_requested(srv, monkeypatch):
    class Boom:
        model = "m"

        def complete(self, *a, **k):
            raise AssertionError("must not be called")

    monkeypatch.setattr("citecheck.server.client_from_env", lambda: Boom())
    status, html = post(srv, {"text": "依最高法院115年度台上字第7號判決意旨，請求給付。", "l2": "1"})
    assert status == 200       # the brief has no FOUND judgment here, so no index is needed and no API call is made


def test_very_long_cjk_text_hits_the_body_limit_first(srv):
    # Known consequence of the two fixed limits (SPEC_V2 section 9): a form encodes one Chinese
    # character as 9 bytes, so ~44,500 characters already exceed the 400,000-byte body limit.
    with pytest.raises(urllib.error.HTTPError) as e:
        post(srv, {"text": "甲" * 45000})
    assert e.value.code == 413
    assert post(srv, {"text": "甲" * 40000})[0] == 200
