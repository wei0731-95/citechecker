"""Build eval/review/review_sheet.html: a blind worksheet for a human reviewer.

The sheet shows each question's brief text and the full text of the cited judgment, but NOT the
gold label, so the reviewer labels independently (docs/TESTSET_GUIDE.md section 2.6).
Only the 25 questions whose citation exists (L2 gold != NOT_RUN) need a label; F/G and the
statute statuses are mechanical facts already verified against the database.
Run: python eval/make_review_sheet.py
"""
import html
import json
import re
import sqlite3
from pathlib import Path

from citecheck.segment import segment

TESTSET = Path("eval/testset.jsonl")
OUT = Path("eval/review/review_sheet.html")
DB = Path("data/citecheck.db")

OPTIONS = [
    ("COURT_HOLDING", "是最高法院自己的見解"),
    ("PARTY_ARGUMENT", "只是當事人（上訴人／被上訴人）的主張，最高法院未採"),
    ("LOWER_COURT_VIEW", "只是原審（第二審）的見解，最高法院未採或已廢棄"),
    ("CONTRADICTED", "書狀的結論與最高法院自己的見解相反（不是在複述某一造的主張）"),
    ("NOT_IN_JUDGMENT", "判決中找不到對應的內容"),
    ("UNSURE", "無法判斷"),
]


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def judgment_html(con, jid: str) -> str:
    full = con.execute("SELECT full_text FROM judgments WHERE jid=?", (jid,)).fetchone()[0]
    parts = []
    for s in segment(full):
        if s.part in ("main", "reason"):
            parts.append(f"<p>{esc(s.text)}</p>")
    return "\n".join(parts)


def main() -> None:
    con = sqlite3.connect(DB)
    items = [json.loads(l) for l in TESTSET.read_text(encoding="utf-8").splitlines() if l.strip()]
    todo = []
    for it in items:
        j = next(c for c in it["citations"] if c["type"] == "JUDGMENT")
        if j["gold_l2"] != "NOT_RUN":
            todo.append((it, j))
    jids = sorted({j["gold_jid"] for _, j in todo})

    q = []
    for n, (it, j) in enumerate(todo, 1):
        brief = esc(it["brief_text"]).replace(esc(j["raw"]), f"<mark>{esc(j['raw'])}</mark>")
        key = it["id"]
        radios = "".join(
            f'<label><input type="radio" name="{key}" value="{v}"> {esc(t)}</label>' for v, t in OPTIONS)
        jn = jids.index(j["gold_jid"]) + 1
        q.append(f'''<section class="q" id="q-{key}">
<h3>第 {n} 題　<span class="id">{key}</span></h3>
<p class="brief">{brief}</p>
<p class="ref">被引用的判決：<a href="#j{jn}" data-j="j{jn}">{esc(j["raw"])}（展開全文）</a></p>
<div class="opts">{radios}</div>
<input class="note" type="text" name="note-{key}" placeholder="備註（選填，例如覺得題目有問題的地方）">
</section>''')

    docs = []
    for i, jid in enumerate(jids, 1):
        yr, no = jid.split(",")[1], jid.split(",")[3]
        docs.append(f'<details id="j{i}"><summary>最高法院{yr}年度台上字第{no}號判決（主文與理由全文）</summary>'
                    f"{judgment_html(con, jid)}</details>")

    page = f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>測試集複核表</title>
<style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d0d7de;--card:#f6f8fa;--hi:#fff3b0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14171a;--fg:#e6e8ea;--muted:#9aa4ad;--line:#343b42;--card:#1c2126;--hi:#5a4b10}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 "Noto Sans TC","Microsoft JhengHei",sans-serif}}
main{{max-width:900px;margin:0 auto;padding:20px 16px 80px}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:32px;border-bottom:1px solid var(--line)}}
.guide{{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 16px}}
.q{{border:1px solid var(--line);border-radius:6px;padding:10px 16px;margin:14px 0}}
.q h3{{margin:0 0 4px;font-size:1rem}} .id{{color:var(--muted);font-weight:400}}
.brief{{background:var(--card);padding:8px 12px;border-radius:4px}} mark{{background:var(--hi);color:inherit}}
.opts label{{display:block;padding:2px 0}} .note{{width:100%;box-sizing:border-box;margin-top:6px;padding:6px}}
details{{border:1px solid var(--line);border-radius:6px;padding:6px 14px;margin:8px 0}}
summary{{cursor:pointer;font-weight:600}} details p{{margin:.6em 0;white-space:pre-wrap;word-break:break-word}}
.bar{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;z-index:5}}
button,input[type=text].name{{padding:6px 12px;font-size:1rem}}
</style></head><body><main>
<h1>測試集複核表（共 {len(todo)} 題，獨立標註）</h1>
<div class="bar">複核者姓名：<input class="name" type="text" id="reviewer" placeholder="請輸入">
 <button id="export">完成後匯出答案（JSON）</button> <span id="count"></span></div>
<div class="guide">
<p><b>請先不要打開 eval/testset.jsonl 或 docs/TESTSET_STATUS.md</b>（裡面有我先前標的答案，看了就不是獨立標註）。答案會自動暫存在這個瀏覽器，關掉再開不會消失。</p>
<p><b>做法：</b>每題看「書狀文字」，再展開被引用的判決全文，判斷書狀的那句話屬於下列哪一種。請依判決<u>全文</u>判斷，不要只看某一段。</p>
<ul>
<li><b>是最高法院自己的見解</b>：「本院判斷」部分明確表達了與書狀相同的法律見解或認定。</li>
<li><b>只是當事人的主張</b>：書狀寫的是上訴人／被上訴人的說法，且最高法院沒有採納。</li>
<li><b>只是原審的見解</b>：書狀寫的是原審（高等法院）的認定或見解，最高法院未採或已廢棄。</li>
<li><b>相反</b>：書狀把最高法院的見解寫成相反的結論，且書狀文字並不是在複述某一造或原審的說法。</li>
<li><b>找不到對應內容</b>：整份判決都沒有談到書狀這句話的內容。</li>
</ul>
<p>提醒：當最高法院明確駁斥某一造或原審的說法時，「只是當事人／原審的主張」與「相反」可能很接近，請依你的判斷選，並在備註寫下猶豫的原因，這正是要找出的題目定義問題。</p>
</div>
<h2>題目</h2>
{"".join(q)}
<h2>判決全文</h2>
{"".join(docs)}
</main>
<script>
(function(){{
var KEY="citecheck-review-v1";
function load(){{try{{return JSON.parse(localStorage.getItem(KEY)||"{{}}")}}catch(e){{return {{}}}}}}
function save(s){{try{{localStorage.setItem(KEY,JSON.stringify(s))}}catch(e){{}}}}
var st=load();
document.querySelectorAll("input[type=radio]").forEach(function(r){{
  if(st[r.name]===r.value) r.checked=true;
  r.addEventListener("change",function(){{st[r.name]=r.value;save(st);count()}});
}});
document.querySelectorAll("input.note").forEach(function(n){{
  if(st[n.name]) n.value=st[n.name];
  n.addEventListener("input",function(){{st[n.name]=n.value;save(st)}});
}});
var rv=document.getElementById("reviewer"); rv.value=st["reviewer"]||"";
rv.addEventListener("input",function(){{st["reviewer"]=rv.value;save(st)}});
function count(){{var n=document.querySelectorAll("input[type=radio]:checked").length;
  document.getElementById("count").textContent="已完成 "+n+" / {len(todo)}"}}
count();
document.querySelectorAll("a[data-j]").forEach(function(a){{
  a.addEventListener("click",function(){{var d=document.getElementById(a.dataset.j);if(d)d.open=true}});
}});
document.getElementById("export").addEventListener("click",function(){{
  var ans={{}},notes={{}};
  document.querySelectorAll("input[type=radio]:checked").forEach(function(r){{ans[r.name]=r.value}});
  document.querySelectorAll("input.note").forEach(function(n){{if(n.value)notes[n.name.replace("note-","")]=n.value}});
  var out={{reviewer:rv.value||"reviewer",answers:ans,notes:notes}};
  var b=new Blob([JSON.stringify(out,null,2)],{{type:"application/json"}});
  var a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="review_answers.json";a.click();
}});
}})();
</script></body></html>'''
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}: {len(todo)} questions, {len(jids)} judgments, {len(page)//1024} KB")


if __name__ == "__main__":
    main()
