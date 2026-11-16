from pathlib import Path

import typer

from .db import DEFAULT_DB, connect
from .ingest_judgments import ingest_judgments as _ij
from .ingest_laws import ingest_laws as _il

app = typer.Typer(help="書狀引用查核器")


@app.command("ingest-laws")
def ingest_laws_cmd(path: Path):
    typer.echo(_il(path))


@app.command("ingest-judgments")
def ingest_judgments_cmd(path: Path):
    typer.echo(_ij(path))


@app.command()
def stats():
    con = connect(DEFAULT_DB)

    def q(sql):
        return con.execute(sql).fetchone()[0]

    meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    typer.echo(f"法規數: {q('SELECT COUNT(*) FROM laws')}")
    typer.echo(f"條文數: {q('SELECT COUNT(*) FROM articles')}")
    typer.echo(f"判決數: {q('SELECT COUNT(*) FROM judgments')}")
    typer.echo(f"法規資料日期: {meta.get('laws_update_date', '-')}")
    typer.echo(f"案號年度範圍: {meta.get('year_min', '-')} ~ {meta.get('year_max', '-')}")
    typer.echo(f"裁判日期範圍: {meta.get('date_min', '-')} ~ {meta.get('date_max', '-')}")


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
    report = build_report(read_brief(brief), str(brief), con, lex, client)
    path = write_json(report, out)
    html_path = write_html(report, out)
    typer.echo(f"法條引用 {len(report.statutes)} 筆：{dict(Counter(s.status.value for s in report.statutes))}")
    typer.echo(f"判決引用 {len(report.judgments)} 筆：{dict(Counter(j.status.value for j in report.judgments))}")
    if client:
        typer.echo(f"L2：{dict(Counter(j.l2_label.value for j in report.judgments))}")
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
    metrics, errors = evaluate(load_testset(testset), con, lex, client)
    write_results(metrics, errors, out)
    fmt = lambda m: "-" if m["rate"] is None else f"{m['rate']:.1%} ({m['num']}/{m['den']})"
    typer.echo(f"抽取召回率：{fmt(metrics['extraction_recall'])}")
    typer.echo(f"L1 正確率：{fmt(metrics['l1_accuracy'])}")
    if metrics["l2"]:
        typer.echo(f"L2 問題偵測召回率：{fmt(metrics['l2']['problem_detection_recall'])}")
        typer.echo(f"L2 誤報率：{fmt(metrics['l2']['false_alarm_rate'])}")
        typer.echo(f"L2 不確定率：{fmt(metrics['l2']['uncertain_rate'])}")
    typer.echo(f"錯誤 {len(errors)} 筆，已輸出至 {out}")


if __name__ == "__main__":
    app()
