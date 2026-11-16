"""Report building, JSON and HTML output."""
import os
import sqlite3
import re
import time
from datetime import date, datetime
from pathlib import Path

from .db import SCHEMA_VERSION
from .extract import Lexicon, extract_full
from .models import DataCoverage, JudgmentCitation, L2Label, OtherAuthority, Report, RunStats, Span
from .normalize import normalize_with_map
from .judge import api_calls_of
from .prep import PREP_PROMPT_VERSION
from .segment import SEGMENT_RULES_VERSION
from .statute_check import STATUTE_PROMPT_VERSION, apply_statute_l2
from .verify import ARTICLE_URL, covered_years, imported_months, verify_judgment, verify_statute  # noqa: F401


def read_brief(path: Path) -> str:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text(encoding="utf-8-sig")
    if suffix == ".docx":
        import docx  # python-docx
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    raise ValueError(f"不支援的檔案格式：{suffix}（僅支援 .txt、.docx）")


def data_coverage(con: sqlite3.Connection) -> DataCoverage:
    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())

    def to_int(v):
        return int(v) if v else None

    return DataCoverage(
        laws_update_date=meta.get("laws_update_date") or None,
        judgment_year_min=to_int(meta.get("year_min")),
        judgment_year_max=to_int(meta.get("year_max")),
        judgment_date_min=meta.get("date_min") or None,
        judgment_date_max=meta.get("date_max") or None,
        judgment_count=con.execute("SELECT COUNT(*) FROM judgments").fetchone()[0],
        months_imported=imported_months(con),
        covered_years=sorted(covered_years(imported_months(con))),
    )


def build_report(raw_text: str, input_file: str, con: sqlite3.Connection, lex: Lexicon,
                 client=None) -> Report:
    """client=None -> L1 only (every l2_label is NOT_RUN)."""
    t0 = time.monotonic()
    text, ranges = normalize_with_map(raw_text)
    ex = extract_full(text, lex)
    statutes, judgments, warnings = ex.statutes, ex.judgments, ex.warnings

    def orig(span: Span) -> Span:
        return Span(start=ranges[span.start][0], end=ranges[span.end - 1][1])

    for c in (*statutes, *judgments, *ex.other_authorities, *ex.unrecognized):
        c.original_span = orig(c.span)
    statutes = [verify_statute(s, con) for s in statutes]
    judgments = [verify_judgment(j, con, lex) for j in judgments]
    stats = RunStats()
    if client is None:
        for j in judgments:
            j.l2_label = L2Label.NOT_RUN
    else:
        from .judge import apply_l2
        apply_l2(judgments, con, client, stats)
        from .suggest import apply_suggestions
        apply_suggestions(judgments, con, client, stats)
    apply_statute_l2(statutes, client, stats)
    stats.api_calls = api_calls_of(client) if client is not None else 0
    stats.cache_hits = getattr(client, "cache_hits", 0) if client is not None else 0
    stats.seconds = round(time.monotonic() - t0, 2)
    return Report(input_file=input_file, generated_at=datetime.now().astimezone().isoformat(),
                  data_coverage=data_coverage(con), normalized_text=text,
                  statutes=statutes, judgments=judgments, warnings=warnings, original_text=raw_text,
                  other_authorities=ex.other_authorities, unrecognized=ex.unrecognized,
                  llm_model=(os.environ.get("CITECHECK_MODEL") or getattr(getattr(client, "inner", client), "model", None)) if client is not None else None,
                  versions={"segment_rules": SEGMENT_RULES_VERSION, "prep_prompt": PREP_PROMPT_VERSION,
                            "statute_prompt": STATUTE_PROMPT_VERSION, "schema": SCHEMA_VERSION}, stats=stats)


def write_json(report: Report, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.json"
    p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------- HTML (SPEC_V2 section 11)
HEADER_STATEMENT = ("本工具只檢查書狀中引用的法條與最高法院民事裁判是否查得到、內容是否對得上，"
                    "不檢查書狀的主張有沒有道理，也不能取代律師。本工具標示為綠色的引用，仍請自行點連結核對原文。")
DISCLAIMER = "本工具僅提供引用查核輔助，不構成法律意見。"
SOURCES_LINE = "法規資料來源：全國法規資料庫；裁判資料來源：司法院開放資料。"
STALE_LAWS_LINE = "法規資料已超過 90 天沒有更新，近期修正的條文可能沒有反映。"
LAW_LINK_TEXT = "在全國法規資料庫看這部法規"
ARTICLE_LINK_TEXT = "在全國法規資料庫看這一條"
PDF_LINK_TEXT = "看裁判原文（司法院 PDF）"

# Level (shown as colour AND as a text label): ok green / check red / notice orange / unknown grey.
LEVEL_LABEL = {"ok": "查得到", "check": "請確認", "notice": "請留意", "unknown": "無法查核"}
LEVEL_COLOR = {"ok": "green", "check": "red", "notice": "orange", "unknown": "gray"}

# All user-facing wording lives here. Never use 「不存在」「偽造」「虛構」「錯誤引用」「建議引用」「應改引」.
ZH = {
    "STATUTE": {
        "LAW_NOT_FOUND": "在法律資料中查不到這部法規的名稱。",
        "ARTICLE_NOT_FOUND": "這部法規存在，但查不到這一條。",
        "PARAGRAPH_NOT_FOUND": "這一條存在，但沒有書狀所寫的這一項。",
        "SUBPARAGRAPH_NOT_FOUND": "這一項存在，但沒有書狀所寫的這一款。",
        "ARTICLE_DELETED": "這一條已經刪除。",
        "LAW_REPEALED": "這部法規已經廢止。",
        "LAW_NAME_UNCERTAIN": "無法確認書狀指的是哪一部法規，請核對法規全名。",
        "LAW_OUT_OF_SCOPE": "本工具未收錄行政命令與地方法規（施行細則、辦法、自治條例等），無法查核。",
        "UNRESOLVED_REF": "這裡只寫了條號，看不出是哪一部法規。",
        "FOUND:NOT_IN_FORCE": "查得到這一條，但全國法規資料庫註明這一條有尚未施行或施行日期另定的情形。"
                              "本工具顯示的條文可能不是目前有效的版本，請點連結確認。",
        "FOUND:MISMATCH": "書狀對這一條的說明，與現行條文對不上。本工具只有現行條文；"
                          "如果書狀引用的是修正前的舊條文，請自行確認。",
        "FOUND:MATCHES": "查得到這一條，書狀對它的說明與現行條文大致相符。",
        "FOUND:NOT_STATED": "查得到這一條。書狀沒有說明條文內容，所以沒有比對。",
        "FOUND:UNCERTAIN": "查得到這一條。本工具無法判斷書狀的說明與條文是否相符，請自行核對。",
        "FOUND:NOT_RUN": "查得到這一條。本工具只確認了條號，沒有比對書狀對條文內容的說明。",
    },
    "JUDGMENT": {
        "NOT_FOUND_IN_SCOPE": "在最高法院民事裁判的公開資料中查不到這個字號。請到司法院裁判書系統確認字號是否正確。",
        "NOT_FOUND_CIVIL_ONLY": "在最高法院民事裁判中查不到這個字號。書狀沒有寫明是民事或刑事；如果是刑事裁判，本工具無法查核。",
        "DATA_NOT_COVERED": "本工具的資料沒有涵蓋這個年度，無法查核。",
        "OUT_OF_SCOPE": "本工具只查最高法院民事判決與裁定，這一筆無法查核。",
        "AMBIGUOUS": "這個字號對應到不只一件裁判，無法確定書狀指的是哪一件。",
        "FOUND:COURT_HOLDING": "查得到這件裁判，且裁判中最高法院自己的論述有與書狀說法相符的段落。",
        "FOUND:CONTRADICTED": "這件裁判中最高法院的論述，與書狀的說法相反。",
        "FOUND:PARTY_ARGUMENT": "與書狀說法相符的內容，在這件裁判裡是當事人的主張，不是法院的見解。",
        "FOUND:LOWER_COURT_VIEW": "與書狀說法相符的內容，在這件裁判裡是下級法院的見解。"
                                  "最高法院有沒有接受這個見解，請看下方的主文與裁判原文。",
        "FOUND:NOT_IN_JUDGMENT": "查得到這件裁判，但裁判中找不到與書狀說法對應的內容。",
        "FOUND:UNCERTAIN": "查得到這個字號，但本工具無法判斷內容是否相符，請自行閱讀裁判原文。",
        "FOUND:NOT_RUN": "查得到這個字號。本工具沒有比對裁判內容是否與書狀說法相符。",
    },
    "NOTES": {
        "court_inferred": "書狀沒有寫法院名稱，本工具依字別推定為最高法院。",
        "procedural_dismissal": "這是一件以程序理由駁回的裁定，通常不包含最高法院對法律問題的見解。",
        "latest_year_note": "這個年度的案件可能尚未裁判或尚未公開。",
    },
    "OTHER": "本工具無法查核這一類依據，請自行確認。",
    "ROLE": {
        "MAIN_TEXT": "主文", "PARTY_CLAIM": "當事人主張", "LOWER_COURT": "原審",
        "SUPREME_COURT": "本院（最高法院）", "UNKNOWN": "無法判定",
    },
    "RELATION": {"SUPPORTS": "相符", "CONTRADICTS": "相反", "UNRELATED": "無關"},
    "UNRECOGNIZED_TITLE": "這幾句話看起來有引用，但本工具沒有辨識出來，請自行核對",
    "SIMILAR_TITLE": "論述相近的最高法院民事裁判（僅供自行查閱）",
    "SIMILAR_NOTE": "以下裁判中，有段落與這句話的意思相近。本工具沒有判斷它們是否適用於您的案件，"
                    "也不保證是最新或唯一的見解。請點連結閱讀原文後自行決定。",
    "CAP_NOTE": "這份書狀的引用太多，有 {n} 筆沒有比對內容。",
}

_STATUTE_LEVEL = {
    "LAW_NOT_FOUND": "check", "ARTICLE_NOT_FOUND": "check", "PARAGRAPH_NOT_FOUND": "check",
    "SUBPARAGRAPH_NOT_FOUND": "check", "ARTICLE_DELETED": "notice", "LAW_REPEALED": "notice",
    "LAW_NAME_UNCERTAIN": "unknown", "LAW_OUT_OF_SCOPE": "unknown", "UNRESOLVED_REF": "unknown",
    "FOUND:NOT_IN_FORCE": "notice", "FOUND:MISMATCH": "notice", "FOUND:MATCHES": "ok",
    "FOUND:NOT_STATED": "ok", "FOUND:UNCERTAIN": "ok", "FOUND:NOT_RUN": "ok",
}
_JUDGMENT_LEVEL = {
    "NOT_FOUND_IN_SCOPE": "check", "NOT_FOUND_CIVIL_ONLY": "notice", "DATA_NOT_COVERED": "unknown",
    "OUT_OF_SCOPE": "unknown", "AMBIGUOUS": "notice",
    "FOUND:COURT_HOLDING": "ok", "FOUND:CONTRADICTED": "check", "FOUND:PARTY_ARGUMENT": "notice",
    "FOUND:LOWER_COURT_VIEW": "notice", "FOUND:NOT_IN_JUDGMENT": "notice", "FOUND:UNCERTAIN": "unknown",
    "FOUND:NOT_RUN": "unknown",
}


def _key(c) -> str:
    status = c.status.value
    if status != "FOUND":
        return status
    if c.type == "STATUTE":
        return "FOUND:NOT_IN_FORCE" if c.not_in_force_note else f"FOUND:{c.l2_label.value}"
    return f"FOUND:{c.l2_label.value}"


def describe(c) -> tuple[str, str, list[str]]:
    """(level, explanation, extra notes) for one citation (SPEC_V2 11.2 / 11.3 / 11.4)."""
    if isinstance(c, OtherAuthority):
        return "unknown", ZH["OTHER"], []
    key = _key(c)
    if c.type == "STATUTE":
        return _STATUTE_LEVEL[key], ZH["STATUTE"][key], []
    notes = [ZH["NOTES"][n] for n in ("court_inferred", "procedural_dismissal", "latest_year_note") if getattr(c, n)]
    return _JUDGMENT_LEVEL[key], ZH["JUDGMENT"][key], notes


def level_of(c) -> str:
    """ok / check / notice / unknown."""
    return describe(c)[0]


def stats_line(st: RunStats) -> str:
    return (f"API 呼叫 {st.api_calls} 次、快取命中 {st.cache_hits} 次、新整理裁判 {st.prep_new} 份、"
            f"因上限略過 {st.skipped_by_cap} 筆、耗時 {st.seconds} 秒")


def laws_age_days(laws_update_date: str | None, today: date | None = None) -> int | None:
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", laws_update_date or "")
    if not m:
        return None
    return ((today or date.today()) - date(int(m.group(1)), int(m.group(2)), int(m.group(3)))).days


def month_range_note(months: list[str]) -> str:
    if not months:
        return "尚未匯入裁判資料"
    lo, hi = int(months[0][:4]) * 12 + int(months[0][4:]) - 1, int(months[-1][:4]) * 12 + int(months[-1][4:]) - 1
    missing = (hi - lo + 1) - len(set(months))
    return f"{months[0]}–{months[-1]}（缺漏 {missing} 個月）"


def render_html(report: Report, today: date | None = None) -> str:
    from jinja2 import Environment, PackageLoader, select_autoescape

    use_orig = bool(report.original_text)
    text = report.original_text if use_orig else report.normalized_text

    def sp(c):
        return c.original_span if (use_orig and c.original_span) else c.span

    items = sorted([*report.statutes, *report.judgments, *report.other_authorities],
                   key=lambda c: (sp(c).start, sp(c).end))
    rows, pieces, pos = [], [], 0
    counts = {k: 0 for k in LEVEL_LABEL}
    for n, c in enumerate(items, 1):
        level, explanation, notes = describe(c)
        counts[level] += 1
        row = {"n": n, "level": level, "color": LEVEL_COLOR[level], "label": LEVEL_LABEL[level],
               "explanation": explanation, "notes": notes, "kind": getattr(c, "type", "OTHER"),
               "raw": text[sp(c).start:sp(c).end] if use_orig else c.raw}
        if row["kind"] == "STATUTE":
            row.update(law_name=c.law_name, article=c.article, paragraph=c.paragraph,
                       subparagraph=c.subparagraph, article_text=c.article_text,
                       law_url=c.law_url, article_url=c.article_url)
        elif row["kind"] == "JUDGMENT":
            row.update(court=c.court, year=c.year, case_word=c.case_word, case_no=c.case_no,
                       doc_kind=c.doc_kind, jid=c.jid, pdf_url=c.pdf_url, main_text=c.main_text,
                       claim_sentence=c.claim_sentence, evidence=c.l2_evidence, similar=c.similar)
            if c.l2_evidence:
                row["evidence_role"] = ZH["ROLE"][c.l2_evidence.segment_role.value]
                row["evidence_relation"] = ZH["RELATION"][c.l2_evidence.relation.value]
        rows.append(row)
        start, end = max(sp(c).start, pos), sp(c).end    # an overlapping span starts where the previous ended
        if end > start:
            pieces.append({"text": text[pos:start], "color": None, "n": n})
            pieces.append({"text": text[start:end], "color": LEVEL_COLOR[level], "label": LEVEL_LABEL[level], "n": n})
            pos = end
    pieces.append({"text": text[pos:], "color": None, "n": 0})

    cov = report.data_coverage
    age = laws_age_days(cov.laws_update_date, today)
    env = Environment(loader=PackageLoader("citecheck", "templates"), autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("report.html.j2").render(
        r=report, cov=cov, rows=rows, pieces=pieces, counts=counts, labels=LEVEL_LABEL, colors=LEVEL_COLOR,
        header=HEADER_STATEMENT, disclaimer=DISCLAIMER, sources=SOURCES_LINE, zh=ZH,
        cap_note=ZH["CAP_NOTE"].format(n=report.stats.skipped_by_cap) if report.stats.skipped_by_cap > 0 else None,
        months_note=month_range_note(cov.months_imported),
        years_note="、".join(str(y) for y in cov.covered_years) or "無",
        stale=STALE_LAWS_LINE if age is not None and age > 90 else None,
        stats_line=stats_line(report.stats), law_link=LAW_LINK_TEXT, article_link=ARTICLE_LINK_TEXT,
        pdf_link=PDF_LINK_TEXT, versions="、".join(f"{k} {v}" for k, v in report.versions.items()),
        model=report.llm_model or "未使用")


def write_html(report: Report, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.html"
    p.write_text(render_html(report), encoding="utf-8")
    return p
