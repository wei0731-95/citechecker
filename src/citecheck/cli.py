from pathlib import Path

import typer

from .db import DEFAULT_DB, connect
from .ingest_judgments import ingest_judgments as _ij
from .ingest_laws import ingest_laws as _il
from .verify import covered_years, imported_months

app = typer.Typer(help="書狀引用查核器")


@app.command("ingest-laws")
def ingest_laws_cmd(path: Path):
    typer.echo(_il(path))


@app.command("ingest-judgments")
def ingest_judgments_cmd(path: Path):
    typer.echo(_ij(path))
    typer.echo("請接著執行 citecheck build-index")


@app.command("build-index")
def build_index_cmd(rebuild: bool = typer.Option(False, "--rebuild", help="清空 segments／passages 重做"),
                    db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑")):
    from .index import build_index
    from .segment import SEGMENT_RULES_VERSION

    con = connect(db)
    r = build_index(con, rebuild)
    typer.echo(f"切段規則版本 {SEGMENT_RULES_VERSION}：本次新建索引 {r['indexed_now']} 份，"
               f"先前已有 {r['already_indexed']} 份{'（已清空重做）' if r['rebuilt'] else ''}")


@app.command()
def stats():
    con = connect(DEFAULT_DB)

    def q(sql):
        return con.execute(sql).fetchone()[0]

    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    typer.echo(f"法規數（現行）: {q('SELECT COUNT(*) FROM laws WHERE repealed=0')}")
    typer.echo(f"法規數（已廢止）: {q('SELECT COUNT(*) FROM laws WHERE repealed=1')}")
    typer.echo(f"條文數: {q('SELECT COUNT(*) FROM articles')}")
    typer.echo(f"判決數: {q('SELECT COUNT(*) FROM judgments')}")
    typer.echo(f"法規資料日期: {meta.get('laws_update_date', '-')}")
    typer.echo(f"案號年度範圍: {meta.get('year_min', '-')} ~ {meta.get('year_max', '-')}")
    typer.echo(f"裁判日期範圍: {meta.get('date_min', '-')} ~ {meta.get('date_max', '-')}")
    months = imported_months(con)
    typer.echo(f"已匯入月份: {', '.join(months) if months else '-'}")
    if months:
        lo, hi = int(months[0][:4]) * 12 + int(months[0][4:]) - 1, int(months[-1][:4]) * 12 + int(months[-1][4:]) - 1
        have = set(months)
        missing = [f"{i // 12:04d}{i % 12 + 1:02d}" for i in range(lo, hi + 1)
                   if f"{i // 12:04d}{i % 12 + 1:02d}" not in have]
        typer.echo(f"缺漏月份（最早到最晚之間）: {', '.join(missing) if missing else '無'}")
        counts = dict(con.execute("SELECT month, COUNT(*) FROM judgments GROUP BY month").fetchall())
        vals = sorted(counts.values())
        median = (vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2)
        low = [m for m in months if counts.get(m, 0) < median / 2]
        typer.echo(f"裁判筆數低於各月中位數一半的月份: {', '.join(low) if low else '無'}")
    typer.echo(f"已涵蓋的案號年度: {', '.join(str(y) for y in sorted(covered_years(months))) or '無'}")


@app.command()
def check(
    brief: Path,
    out: Path = typer.Option(..., "--out", help="輸出資料夾"),
    no_l2: bool = typer.Option(False, "--no-l2", help="只做 L1，不執行 L2"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑"),
):
    from collections import Counter

    from .llm import CachingClient, LLMConfigError, client_from_env

    from .extract import build_lexicon
    from .report import build_report, read_brief, write_html, write_json

    client = None
    if not no_l2:
        try:
            base = client_from_env()
        except LLMConfigError as e:
            typer.echo(f"{e}。請設定後再執行，或加上 --no-l2 只做 L1。", err=True)
            raise typer.Exit(2)
        client = CachingClient(base, base.model)
    con = connect(db)
    lex = build_lexicon(con, Path("data/law_aliases.json"), Path("data/case_words_tps_civil.json"))
    from .index import IndexNotReady
    try:
        report = build_report(read_brief(brief), str(brief), con, lex, client)
    except IndexNotReady as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    path = write_json(report, out)
    html_path = write_html(report, out)
    typer.echo(f"法條引用 {len(report.statutes)} 筆：{dict(Counter(s.status.value for s in report.statutes))}")
    typer.echo(f"判決引用 {len(report.judgments)} 筆：{dict(Counter(j.status.value for j in report.judgments))}")
    if client:
        typer.echo(f"L2：{dict(Counter(j.l2_label.value for j in report.judgments))}")
    from .report import stats_line
    typer.echo(stats_line(report.stats))
    for w in report.warnings:
        typer.echo(f"警告：{w}")
    typer.echo(f"已輸出：{path}、{html_path}")


@app.command("eval")
def eval_cmd(
    testset: Path,
    out: Path = typer.Option(..., "--out", help="輸出資料夾"),
    no_l2: bool = typer.Option(False, "--no-l2", help="只評 L1，不執行 L2"),
    db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑"),
):
    from .evaluate import evaluate, load_testset, write_results
    from .extract import build_lexicon
    from .llm import CachingClient, LLMConfigError, client_from_env

    client = None
    if not no_l2:
        try:
            base = client_from_env()
        except LLMConfigError as e:
            typer.echo(f"{e}。請設定後再執行，或加上 --no-l2 只做 L1。", err=True)
            raise typer.Exit(2)
        client = CachingClient(base, base.model)
    con = connect(db)
    lex = build_lexicon(con, Path("data/law_aliases.json"), Path("data/case_words_tps_civil.json"))
    from .index import IndexNotReady
    try:
        metrics, errors = evaluate(load_testset(testset), con, lex, client)
    except IndexNotReady as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    write_results(metrics, errors, out)
    fmt = lambda m: "-" if m["rate"] is None else f"{m['rate']:.1%} ({m['num']}/{m['den']})"
    typer.echo(f"抽取召回率：{fmt(metrics['extraction_recall'])}")
    typer.echo(f"L1 正確率：{fmt(metrics['l1_accuracy'])}（略過 {metrics['skipped_not_covered']} 筆：年度未涵蓋）")
    for label, key in (("尚未施行標記符合", "not_in_force_match"), ("其他依據抽取符合", "other_authorities_match"),
                       ("未辨識句子符合", "unrecognized_match")):
        if metrics[key]["den"]:
            typer.echo(f"{label}：{fmt(metrics[key])}")
    if metrics["l2"]:
        typer.echo(f"L2 問題偵測召回率：{fmt(metrics['l2']['problem_detection_recall'])}")
        typer.echo(f"L2 誤報率：{fmt(metrics['l2']['false_alarm_rate'])}")
        typer.echo(f"L2 不確定率：{fmt(metrics['l2']['uncertain_rate'])}")
        typer.echo(f"綠色精確率：{fmt(metrics['l2']['green_precision'])}（AI 標註，未經人工複核）")
        typer.echo(f"有效判斷率：{fmt(metrics['l2']['valid_judgment_rate'])}")
        typer.echo(f"逐題標籤正確率：{fmt(metrics['l2']['label_accuracy'])}")
        if metrics["statute_l2"]:
            typer.echo(f"法條不符偵測率：{fmt(metrics['statute_l2']['mismatch_detection_rate'])}")
            typer.echo(f"法條誤報率：{fmt(metrics['statute_l2']['false_alarm_rate'])}")
    typer.echo(f"錯誤 {len(errors)} 筆，已輸出至 {out}")


@app.command()
def serve(port: int = typer.Option(8000, "--port", help="埠號"),
          host: str = typer.Option("127.0.0.1", "--host", help="綁定位址；在 Docker 內用 0.0.0.0"),
          db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑")):
    """啟動本機網頁（貼上書狀、取得報告）。"""
    from .server import serve as _serve

    _serve(host, port, db)


@app.command("eval-retrieval")
def eval_retrieval_cmd(testset: Path, db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑")):
    """金標段落落在 BM25 前 1／3／5／10 名的題數（不呼叫 API；TOP_K 不因結果而改）。"""
    from .evaluate import eval_retrieval, load_testset
    from .extract import build_lexicon
    from .index import IndexNotReady

    con = connect(db)
    lex = build_lexicon(con, Path("data/law_aliases.json"), Path("data/case_words_tps_civil.json"))
    try:
        r = eval_retrieval(load_testset(testset), con, lex)
    except IndexNotReady as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    typer.echo(f"有金標段落的判決引用 {r['total']} 題（其中 {r['not_found']} 題的金標段落不在任何 passage 內或未抽取）")
    typer.echo(f"金標段落落在前 1／3／5／10 名：{r['top1']}／{r['top3']}／{r['top5']}／{r['top10']} 題")


@app.command("prep-samples")
def prep_samples_cmd(db: Path = typer.Option(DEFAULT_DB, "--db", help="資料庫路徑")):
    """對切段樣本的 40 份裁判做轉述句整理，輸出到 docs/quoted_samples/（需要 API 金鑰）。"""
    import glob
    import os

    from .index import IndexNotReady, check_index_ready
    from .llm import CachingClient, LLMConfigError, client_from_env
    from .prep import ensure_prepared, load_quotes, split_sentences

    try:
        base = client_from_env()
    except LLMConfigError as e:
        typer.echo(f"{e}。prep-samples 需要 API，未執行。", err=True)
        raise typer.Exit(2)
    client = CachingClient(base, base.model)
    jids = []
    for d in ("docs/segment_samples", "docs/segment_samples_ruling"):
        for p in sorted(glob.glob(d + "/TPSV_*.txt")):
            jids.append(",".join(os.path.basename(p)[:-4].split("_")))
    con = connect(db)
    try:
        check_index_ready(con, jids)
    except IndexNotReady as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    out_dir = Path("docs/quoted_samples")
    out_dir.mkdir(parents=True, exist_ok=True)
    for jid in jids:
        ok = ensure_prepared(jid, con, client)
        quotes = load_quotes(con, jid)
        lines = [f"# {jid}（整理{'成功' if ok else '失敗'}）", ""]
        for seq, text in con.execute(
                "SELECT seq, text FROM passages WHERE jid=? AND role='SUPREME_COURT' ORDER BY seq", (jid,)):
            for k, sent in enumerate(split_sentences(text)):
                sp = quotes.get((seq, k))
                lines.append((f">>{sp} " if sp else "") + sent)
            lines.append("")
        (out_dir / (jid.replace(",", "_") + ".txt")).write_text("\n".join(lines), encoding="utf-8")
    typer.echo(f"已輸出 {len(jids)} 份到 {out_dir}")


if __name__ == "__main__":
    app()
