"""Local web page (SPEC_V2 Phase D): paste a brief, get the same HTML report as `check`.

Standard library only (http.server). Nothing pasted is written to disk or logged.
"""
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jinja2 import Environment, PackageLoader, select_autoescape

from .db import DEFAULT_DB, connect
from .extract import build_lexicon
from .index import IndexNotReady
from .llm import CachingClient, LLMConfigError, client_from_env
from .report import (HEADER_STATEMENT, LEVEL_COLOR, LEVEL_HELP, LEVEL_LABEL, LEVEL_ORDER, build_report,
                     render_html)

MAX_CHARS = 50_000
MAX_BODY = 600_000
MSG_TOO_LONG = "文字超過 50,000 字，請分段查核。"
MSG_EMPTY = "請貼上書狀文字。"
L2_LABEL = "同時比對內容是否相符（會把含有引用的句子傳送到外部 AI 服務，請先自行刪除姓名、身分證字號、地址等個人資料）"
BUTTON_BUSY = "查核中，內容比對可能需要一到兩分鐘，請不要關閉頁面"
ENV_HELP = ("需要設定的環境變數：CITECHECK_MODEL，以及 ANTHROPIC_API_KEY（CITECHECK_PROVIDER 未設定或為 anthropic）"
            "或 OPENAI_API_KEY（CITECHECK_PROVIDER=openai）")

# Usage hints on the form page (UI aid, SPEC_V3 Phase H; the fixed SPEC_V2 wording above is unchanged).
TEXT_HINT = ("可直接貼上從 Word 或 PDF 複製的文字，最多 50,000 字。查核在這台電腦上進行，"
             "貼上的文字不會存檔；沒有勾選下方選項時，不會傳送到任何外部服務。")
L2_HINT = "不勾選：只確認法條與字號查不查得到，幾秒內完成。勾選：再比對書狀說法與條文、裁判內容是否相符，需要先設定 AI 服務的金鑰。"
LEGEND_TITLE = "報告怎麼看"

_ENV = Environment(loader=PackageLoader("citecheck", "templates"), autoescape=select_autoescape(["html", "j2"]))


def render_form(text: str = "", error: str | None = None, l2: bool = False) -> bytes:
    return _ENV.get_template("form.html.j2").render(
        text=text, error=error, l2=l2, header=HEADER_STATEMENT, l2_label=L2_LABEL, busy=BUTTON_BUSY,
        text_hint=TEXT_HINT, l2_hint=L2_HINT, legend_title=LEGEND_TITLE,
        legend=[(LEVEL_COLOR[k], LEVEL_LABEL[k], LEVEL_HELP[k]) for k in LEVEL_ORDER]).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "citecheck"
    sys_version = ""
    db_path: Path = DEFAULT_DB
    aliases_path = Path("data/law_aliases.json")
    case_words_path = Path("data/case_words_tps_civil.json")

    # --- logging: time, method, path, status only; never the request content
    def log_message(self, format, *args):  # noqa: A002
        pass

    def log_request(self, code="-", size="-"):
        path = urlsplit(self.path).path
        print(f"{time.strftime('%H:%M:%S')} {self.command} {path} {code}", file=sys.stderr, flush=True)

    # --- helpers
    def _send(self, status: int, body: bytes = b"", ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _plain(self, status: int, msg: str) -> None:
        self._send(status, msg.encode("utf-8"), "text/plain; charset=utf-8")

    def _port(self) -> int:
        return self.server.server_address[1]

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in (f"127.0.0.1:{self._port()}", f"localhost:{self._port()}")

    # --- routing
    def do_GET(self):
        if not self._host_ok():
            return self._plain(403, "Forbidden")
        if urlsplit(self.path).path != "/":
            return self._plain(404, "Not Found")
        self._send(200, render_form())

    def do_POST(self):
        if not self._host_ok():
            return self._plain(403, "Forbidden")
        if urlsplit(self.path).path != "/check":
            return self._plain(404, "Not Found")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://127.0.0.1:{self._port()}", f"http://localhost:{self._port()}"):
            return self._plain(403, "Forbidden")
        length = self.headers.get("Content-Length")
        if length is None or not length.isdigit() or int(length) > MAX_BODY:
            return self._plain(413, "Payload Too Large")        # body is not read
        form = parse_qs(self.rfile.read(int(length)).decode("utf-8", "replace"), keep_blank_values=True)
        text = (form.get("text") or [""])[0]
        want_l2 = "l2" in form
        if len(text) > MAX_CHARS:
            return self._send(200, render_form(text, MSG_TOO_LONG, want_l2))
        if not text.strip():
            return self._send(200, render_form("", MSG_EMPTY, want_l2))
        client = None
        if want_l2:
            try:
                base = client_from_env()
            except LLMConfigError as e:
                return self._send(200, render_form(text, f"{e}。{ENV_HELP}", want_l2))
            client = CachingClient(base, base.model)
        con = connect(self.db_path)                              # one connection per request
        try:
            lex = build_lexicon(con, self.aliases_path, self.case_words_path)
            report = build_report(text, "（網頁貼上）", con, lex, client)
        except IndexNotReady as e:
            return self._send(200, render_form(text, str(e), want_l2))
        finally:
            con.close()
        self._send(200, render_html(report, back_link=True).encode("utf-8"))

    def _not_found(self):
        self._plain(404, "Not Found")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _not_found


def make_server(host: str = "127.0.0.1", port: int = 8000, db: Path | str = DEFAULT_DB) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"db_path": Path(db)})
    return ThreadingHTTPServer((host, port), handler)


def serve(host: str = "127.0.0.1", port: int = 8000, db: Path | str = DEFAULT_DB) -> None:
    srv = make_server(host, port, db)
    print(f"http://127.0.0.1:{srv.server_address[1]}/  （按 Ctrl+C 結束）", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
