"""Report building, JSON and HTML output."""
import sqlite3
from datetime import datetime
from pathlib import Path

from .extract import Lexicon, extract_all
from .models import DataCoverage, JudgmentCitation, L2Label, Report
from .normalize import normalize
from .verify import verify_judgment, verify_statute


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
    )


def build_report(raw_text: str, input_file: str, con: sqlite3.Connection, lex: Lexicon,
                 client=None) -> Report:
    """client=None -> L1 only (every l2_label is NOT_RUN)."""
    text = normalize(raw_text)
    statutes, judgments, warnings = extract_all(text, lex)
    statutes = [verify_statute(s, con) for s in statutes]
    judgments = [verify_judgment(j, con, lex) for j in judgments]
    if client is None:
        for j in judgments:
            j.l2_label = L2Label.NOT_RUN
    else:
        from .judge import apply_l2
        apply_l2(judgments, con, client)
    return Report(input_file=input_file, generated_at=datetime.now().astimezone().isoformat(),
                  data_coverage=data_coverage(con), normalized_text=text,
                  statutes=statutes, judgments=judgments, warnings=warnings)


def write_json(report: Report, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.json"
    p.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------- HTML
DISCLAIMER = "本工具僅提供引用查核輔助，不構成法律意見。"

# All user-facing status wording lives here (SPEC 8). Do not use 「不存在」「偽造」「虛構」.
ZH = {
    "STATUTE": {
        "FOUND": "查得現行條文",
        "LAW_NOT_FOUND": "查無此法規名稱",
        "ARTICLE_NOT_FOUND": "法規可查得，但查無此條",
        "ARTICLE_DELETED": "該條文現行內容為「（刪除）」",
        "UNRESOLVED_REF": "無法承接前文之法規名稱，無法查核",
    },
    "JUDGMENT": {
        "FOUND": "查得該裁判",
        "NOT_FOUND_IN_SCOPE": "查無公開裁判",
        "OUT_OF_SCOPE": "非最高法院民事裁判，無法查核",
        "AMBIGUOUS": "同字號對到多筆裁判，無法確定",
        "DATA_NOT_COVERED": "年度不在已匯入資料範圍，無法查核",
    },
    "L2": {
        "COURT_HOLDING": "屬最高法院見解",
        "PARTY_ARGUMENT": "僅為當事人主張",
        "LOWER_COURT_VIEW": "僅為原審見解",
        "CONTRADICTED": "最高法院見解與書狀說法相反",
        "NOT_IN_JUDGMENT": "判決中找不到對應內容",
        "UNCERTAIN": "無法判斷，需人工複核",
        "NOT_RUN": "未執行",
    },
    "ROLE": {
        "MAIN_TEXT": "主文", "PARTY_CLAIM": "當事人主張", "LOWER_COURT": "原審",
        "SUPREME_COURT": "本院（最高法院）", "UNKNOWN": "無法判定",
    },
    "RELATION": {"SUPPORTS": "相符", "CONTRADICTS": "相反", "UNRELATED": "無關"},
}

_RED = {"NOT_FOUND_IN_SCOPE", "LAW_NOT_FOUND", "ARTICLE_NOT_FOUND", "CONTRADICTED"}
_ORANGE = {"PARTY_ARGUMENT", "LOWER_COURT_VIEW", "ARTICLE_DELETED", "AMBIGUOUS"}


def color_of(c) -> str:
    """green / red / orange / gray per SPEC 8."""
    status = c.status.value
    if c.type == "STATUTE":
        return "green" if status == "FOUND" else "red" if status in _RED else \
            "orange" if status in _ORANGE else "gray"
    l2 = c.l2_label.value
    if status == "FOUND" and l2 == "COURT_HOLDING":
        return "green"
    for v in (status, l2):
        if v in _RED:
            return "red"
    for v in (status, l2):
        if v in _ORANGE:
            return "orange"
    return "gray"


def render_html(report: Report) -> str:
    from collections import Counter

    from jinja2 import Environment, PackageLoader, select_autoescape

    items = sorted([*report.statutes, *report.judgments], key=lambda c: (c.span.start, c.span.end))
    cites, pieces, pos = [], [], 0
    text = report.normalized_text
    for n, c in enumerate(items, 1):
        row = {"n": n, "kind": c.type, "raw": c.raw, "cls": color_of(c),
               "status_zh": ZH[c.type][c.status.value], "l2_zh": None, "l2_cls": "gray"}
        if c.type == "STATUTE":
            row.update(law_name=c.law_name, article=c.article, paragraph=c.paragraph,
                       subparagraph=c.subparagraph, article_text=c.article_text)
        else:
            row.update(court=c.court, year=c.year, case_word=c.case_word, case_no=c.case_no,
                       doc_kind=c.doc_kind, jid=c.jid, claim_sentence=c.claim_sentence,
                       evidence=c.l2_evidence)
            if c.status.value == "FOUND" and c.l2_label.value != "NOT_RUN":
                row["l2_zh"] = ZH["L2"][c.l2_label.value]
                row["l2_cls"] = color_of(c)
            if c.l2_evidence:
                row["evidence_role_zh"] = ZH["ROLE"][c.l2_evidence.segment_role.value]
                row["evidence_relation_zh"] = ZH["RELATION"][c.l2_evidence.relation.value]
        cites.append(row)
        if c.span.start >= pos:  # overlapping spans stay listed in the details but are not re-highlighted
            pieces.append({"text": text[pos:c.span.start], "cls": None, "n": n})
            pieces.append({"text": text[c.span.start:c.span.end], "cls": row["cls"], "n": n})
            pos = c.span.end
    pieces.append({"text": text[pos:], "cls": None, "n": 0})

    counts = Counter()
    for s in report.statutes:
        counts[("法條", ZH["STATUTE"][s.status.value])] += 1
    for j in report.judgments:
        counts[("判決（L1）", ZH["JUDGMENT"][j.status.value])] += 1
        if j.status.value == "FOUND" and j.l2_label.value != "NOT_RUN":
            counts[("判決（L2）", ZH["L2"][j.l2_label.value])] += 1
    env = Environment(loader=PackageLoader("citecheck", "templates"),
                      autoescape=select_autoescape(["html", "j2"]))
    return env.get_template("report.html.j2").render(
        r=report, cov=report.data_coverage, cites=cites, pieces=pieces,
        summary=sorted(counts.items()), disclaimer=DISCLAIMER)


def write_html(report: Report, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.html"
    p.write_text(render_html(report), encoding="utf-8")
    return p
