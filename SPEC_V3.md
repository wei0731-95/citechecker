# citecheck 修正規格（SPEC_V3）

> **使用方式**：對 code 模式說「讀 SPEC_V3.md，依序做 Phase H、Phase V，一次只做一個 Phase」。
> 工作規則完全沿用 SPEC_V2.md §1（不要猜、Docker 指令、測試、AI 標註、必須停下來的情況、每個 Phase 結束時的回報格式）。本文件與 SPEC_V2.md 衝突時，以本文件為準。

---

## 0. 背景

SPEC_V2 的 Phase A–G 已完成（pytest 310 項通過；用 gpt-6-luna 跑 `eval/testset_v2.jsonl`：綠色精確率 14/15、放行檢查通過）。2026-11 驗證時發現 5 個問題，這份文件修正它們（Phase H），並把還沒用真實 API 驗證過的部分驗證完（Phase V）。

本文件中的規則與數字，都是對現有資料庫（3,030 份裁判，其中判決 433 份）實際量測後定下的。**照抄，不要增減。**

優先順序不變（SPEC_V2 §0）：最不能犯的錯是把錯的引用標成綠色。

---

## 1. Phase H — 修正

做的順序：H1 → H2 → H3 → H4 → H5 → 重建索引 → 測試與評測。

### H1. 原審理由的結尾要當成分界（`segment.py`）

**問題**：最高法院判決的結構是「當事人主張 → 原審理由 → 本院判斷」。原審理由常分成㈠㈡㈢小段，其中以「查」「按」開頭的小段會被現有規則切成本院（`SUPREME_COURT`），沒有標記詞的小段又沿用前一段的錯誤角色。結果是原審的見解被當成最高法院見解，產生錯誤的綠色。`eval/testset_v2.jsonl` 的 C04（115 年度台上字第 114 號）就是這樣答錯的。

**依據**：原審理由的最後一句幾乎固定是「…等詞，為其判斷之基礎。」。433 份判決中有 212 份的理由段有這種結尾句；緊接在它後面的那一段，211 份是本院判斷（其中 16 份沒有任何標記詞，例如「四、經查：」「四、關於廢棄發回部分：」，現在被錯標成原審），1 份是另一段以「原審」開頭的敘述。裁定中沒有這種句型（0 份）。

定義兩個正規表示式（常數，放在 `segment.py`）：

```python
# 原審理由的結尾句。用在「段落去掉頭尾空白後的最後 45 個字」上。
_LOWER_END = re.compile(r"等[詞語情][，,]?資?為其(?:判斷|論斷|心證)之基礎。?$")
# 同一句型出現在段落中間（後面還有文字）。
_LOWER_END_MID = re.compile(r"等[詞語情][，,]?資?為其(?:判斷|論斷|心證)之基礎。")
```

在 `segment()` 處理理由段（`_reflow(reason)` 的結果）時，加入三條規則。只作用於理由段；主文、header、footer 不動。

**規則一（拆段）**：在判斷角色之前，逐一檢查 `_reflow(reason)` 回傳的每個段落。若 `_LOWER_END_MID` 在段落中找到比對，且比對結束位置之後還有非空白文字 → 在比對結束位置把段落拆成兩段（前段含結尾句的句號）。一個段落只拆第一個比對處；拆出來的後段不再檢查。
（依據：有 11 個段落把原審理由的結尾與「惟按…」「惟查…」黏在同一段。）

**規則二（往後）**：照現有方式逐段決定角色（`role_of` 有標記詞就切換 `state`，否則沿用）。新增：某一段決定完角色後，若它符合 `_LOWER_END` → 把 `state` 設為 `SUPREME_COURT`。下一段若有自己的標記詞，照常由標記詞決定。

**規則三（往前回填）**：全部段落都決定完角色後，由前往後掃描理由段。對每一個符合 `_LOWER_END` 的段落（位置 `i`）：
1. 設 `p` 為前一個符合 `_LOWER_END` 的段落位置（沒有就是理由段開頭的前一個位置）。
2. 在 `p` 之後到 `i`（含）之間，找出**最前面**一個 `marker` 屬於 `LOWER_MARKERS`（`原審`、`原法院`、`原判決`、`第一審`）的段落，位置 `a`。
3. 找得到 → 把位置 `a` 到 `i`（含）之間所有段落的角色改為 `LOWER_COURT`（`marker` 欄位不改）。找不到 → 不動。

（依據：以現有資料模擬，規則三會改動 11 份判決的 33 段，原角色為 `SUPREME_COURT` 16 段、`PARTY_CLAIM` 17 段；逐一看過的樣本都是原審理由的小段，例如「㈠查496地號土地…」「㈣審酌系爭土地周邊環境…」「㈢從而，上訴人依…請求…為無理由，不應准許等詞」。）

其他：
- `SEGMENT_RULES_VERSION` 改為 `2`。
- 更新 `docs/SEGMENT_RULES.md`：新增一節說明這三條規則與上面的依據數字。
- 重新產生樣本：`python -m citecheck.segment` 與 `python -m citecheck.segment --ruling`（覆寫 `docs/segment_samples/`、`docs/segment_samples_ruling/`）。

測試（用寫在測試裡的假判決全文，放 `tests/test_segment.py`；每個假全文都要有「主文」「理由」標題）：
1. 理由段依序為：`一、上訴人主張：甲。`／`二、原審以：`／`㈠查乙事實。`／`㈡按丙見解。`／`㈢從而上訴人之請求為無理由等詞，為其判斷之基礎。`／`三、按丁見解。` → 第 2–5 段都是 `LOWER_COURT`，第 6 段是 `SUPREME_COURT`。
2. 同上，但最後一段改成沒有標記詞的 `四、關於廢棄發回部分：` 再接一段 `戊見解。` → 這兩段都是 `SUPREME_COURT`（規則二）。
3. 一個段落是 `二、原審以：乙見解等詞，為其判斷之基礎。惟按丙見解。` → 拆成兩段，前段 `LOWER_COURT`、後段 `SUPREME_COURT`（規則一）。
4. 兩輪：`二、原審以：甲等詞，為其判斷之基礎。`／`三、按乙見解。`／`四、原審就另一部分以：丙等詞，為其判斷之基礎。`／`五、按丁見解。` → 角色依序為 `LOWER_COURT`、`SUPREME_COURT`、`LOWER_COURT`、`SUPREME_COURT`（規則三不得把第 2 段回填成原審）。
5. 理由段有結尾句但前面沒有任何 `LOWER_MARKERS` 的段落 → 規則三不改任何角色；規則二仍生效。
6. 既有的切段測試全部通過（有調整預期值的列清單）。

### H2. 轉述句整理的結果只沿用同一個模型的（`prep.py`、`suggest.py`）

**問題**：`judgment_prep` 記錄了整理時用的模型，但判斷「已整理過」時不看模型。目前 92 份已整理的裁判中，18 份是本地模型 `qwen2.5:14b` 整理的，之後用正式模型查核時會直接沿用品質較差的結果。

**修正**：
1. `prep.py` 新增 `current_model(client) -> str | None`：回傳 `os.environ.get("CITECHECK_MODEL") or getattr(client, "model", None)`（與 `_write` 目前取模型名稱的方式相同；`_write` 改用這個函式）。
2. `_current_ok(con, jid)` 改為 `_current_ok(con, jid, model)`：除了原有條件（`status='ok'`、`prompt_version`、`segment_rules_version` 相符），再加上 `judgment_prep.model` 等於 `model`（兩者都是 `None` 也算相等）。所有呼叫端（`plan_prep`、`ensure_prepared`、`judge.apply_l2`、`suggest.py`）都傳入 `current_model(client)`。
3. 模型不同時視為未整理，下次用到時重做並覆寫（既有的 `INSERT OR REPLACE` 與「先刪除該 jid 的 `quoted_sentences`」行為不變）。

測試：資料庫裡有一筆 `model='A'` 的成功整理紀錄；用 `model='B'` 的 client 查核 → 會重新呼叫並把紀錄更新為 `B`；再用 `model='B'` 查一次 → 不呼叫。

### H3. 憲法法庭判決重複出現（`extract.py`）

**問題**：`憲法法庭112年憲判字第4號判決` 同時被當成判決引用（`OUT_OF_SCOPE`）與其他依據（`憲判字`），報告裡出現兩次。

**修正**：`extract_full` 在兩種抽取都完成後，移除所有「`span` 與任何一筆 `other_authorities` 的 `span` 有重疊」的判決引用。之後才做主張句附加與 A13 的未辨識句子判斷。

測試：上面那句 → `judgments` 為空，`other_authorities` 有 1 筆 `憲判字`。

### H4. 網頁的請求大小上限與字數上限互相衝突（`server.py`）

**問題**：字數上限 50,000 字，但請求本文上限 400,000 位元組。表單編碼後一個中文字佔 9 個位元組，所以大約 44,500 個中文字就會被回 413，使用者看不到「請分段查核」的訊息。這是 SPEC_V2 規格本身的錯誤。

**修正**：`MAX_BODY = 600_000`。其他不變。

測試：送出 50,000 個中文字 → 得到報告（不是 413）；50,001 個中文字 → 得到表單頁與「文字超過 50,000 字，請分段查核。」；本文超過 600,000 位元組 → 413。記錄舊行為的那個測試改為上述預期，並列入「被調整預期值的既有測試」。

### H5. 項、款的連續列舉（`extract.py`、`statute_check.py`、`models.py`）

**問題**：`民法第195條第1項、第5項` 只抽出第 1 項，「第5項」被略過不查。現有 3,030 份裁判中，「第N項、第M項」出現 559 次、「第N款、第M款」出現 200 次，這是很常見的寫法。

**修正**：
1. `StatuteCitation` 新增欄位 `is_continuation: bool = False`。
2. `extract.py` 新增：
   ```python
   _ITEM_CONT = re.compile(
       r"(?:、|及|或|與|暨|至)\s*(?:第(\d+)項(?:第(\d+)款)?|第(\d+)款)(?:前段|後段|但書)?")
   ```
3. 在 `_extract_statutes` 中，每產生一筆引用 `c`（不論來自 `_ART` 或 `_SAME_ART`）並加入結果後，若 `c.paragraph` 或 `c.subparagraph` 至少有一個不是 `None`，就從 `c` 的結尾位置開始，反覆用 `_ITEM_CONT.match(text, pos)` 比對：
   - 比對到 `第N項` 的形式 → 新引用的 `paragraph = N`，`subparagraph` = 後面的款號（沒有就是 `None`）。
   - 比對到只有 `第M款` 的形式 → 新引用的 `paragraph` 沿用這一串中**前一筆**的 `paragraph`，`subparagraph = M`。
   - 新引用的 `law_name`、`article` 與 `c` 相同；`status` 與 `c` 抽取當下的 `status` 相同；`is_continuation=True`；`raw` 與 `span` 從比對中的「第」字開始，到比對結尾。
   - 把新引用加入結果，`pos` 移到比對結尾，繼續比對，直到比對不到為止。
   - 這一串處理完後，迴圈裡的 `prev` 設為這一串的最後一筆、`prev_end` 設為最後的 `pos`（這樣後面的「、第185條」「同條」仍能正確承接）。
   - 「至」只輸出起訖兩筆，不展開中間（與 SPEC.md §6.3 S9 對條號的處理一致）。
4. 若某個 `_ART` 候選的起始位置落在已處理過的這一串範圍內（`start < prev_end`），跳過該候選。（`_ART` 需要「條」字，正常不會發生；這是保險。）
5. `verify_statute` 對 `is_continuation` 的引用照常做 L1 與 B1 的項款檢查。
6. `statute_check.apply_statute_l2`：`is_continuation` 為 True 的引用**不呼叫 LLM**，`l2_label` 維持 `NOT_RUN`。（同一句、同一條已經由這一串的第一筆比對過，不重複花費。）
7. `attach_statute_claims` 照常填 `claim_sentence`。

測試（`tests/test_extract.py` 與 `tests/test_statute_check.py`）：
1. `民法第195條第1項、第5項` → 兩筆，第二筆 `article="195"`、`paragraph=5`、`is_continuation=True`、`raw="第5項"`。
2. `民法第1052條第1項第3款、第11款` → 兩筆，第二筆 `paragraph=1`、`subparagraph=11`。
3. `民法第184條第1項、第185條` → 兩筆，條號分別是 184、185，都不是 continuation（行為不變）。
4. `民法第184條第1項至第3項` → 兩筆（第 1 項、第 3 項）。
5. `民法第184條、第2項` → 一筆（前一筆沒有項或款，不啟動連續列舉）。
6. `民法第184條第1項及第2項，同條第1項後段` → 三筆，第三筆承接到第 184 條。
7. 用假資料庫：條文有 2 項，引用「第1項、第5項」→ 第二筆狀態 `PARAGRAPH_NOT_FOUND`。
8. continuation 的引用在有 `client` 時不產生 LLM 呼叫（用計數的 `MockClient`）。

### H 的收尾
1. 執行 `citecheck build-index`（版本從 1 變 2，會自動清空並重建 `segments`、`passages`、`passages_fts`，也會清空 `quoted_sentences` 與 `judgment_prep`）。回報段落數與耗時。
2. 重跑不需要 API 的評測並回報「之前 → 之後」：`eval`（`--no-l2`）、`eval-retrieval`、`eval-suggest`，v1 與 v2 各一次。
3. 重跑下面這個量測並把結果貼進 PROGRESS.md：對所有 `doc_kind='判決'` 的裁判，計算「理由段中，位置在某個符合 `_LOWER_END` 的段落之前（含），且從該輪最前面的 `LOWER_MARKERS` 段落起算，角色卻不是 `LOWER_COURT`」的段數。H1 完成後這個數字必須是 0。
4. 更新 `docs/DATA_SCHEMA.md`（無結構變動則不用）、README（無指令變動則不用）。

### Phase H 驗收
H1–H5 每一項的測試都在且通過；既有測試全部通過（調整過預期值的列清單）；收尾第 2、3 點的數字已回報；commit。

---

## 2. Phase V — 用真實 API 驗證

這個 Phase 需要 API 金鑰（SPEC_V2 §1.2 的環境變數）。**沒有金鑰 → 依 SPEC_V2 §1.6 第 4 點停下來**，不要用假資料代替。使用的模型名稱寫進 PROGRESS.md；所有數字後面加「（AI 標註，未經人工複核）」。

Phase V 不改任何規則、門檻、提示詞。發現問題只回報。

### V1. 放行檢查重跑
`citecheck eval eval/testset_v2.jsonl --out eval/results_v3`（含 L2）。H1 改了切段，所有裁判都會重新做轉述句整理。

檢查 SPEC_V2 §8 C5 的兩個條件：綠色精確率 ≥ 0.90；金標為 `PARTY_ARGUMENT` 或 `LOWER_COURT_VIEW` 而系統輸出 `COURT_HOLDING` 的題數 ≤ 1。

回報：
- 與 `eval/results_api/`（H 之前的結果）逐題比較，列出每一題判決標籤有變化的題目：題號、金標、之前、之後。
- C04 之後的標籤（預期是 `LOWER_COURT_VIEW`；不是的話寫出被當成證據的段落與它的角色）。
- 兩個條件任何一個不符合 → 停下來，依 SPEC_V2 §8 C5 的格式列出每一題答錯的綠色。

### V2. 第一版測試集
`citecheck eval eval/testset.jsonl --out eval/results_v3_v1`（含 L2）。只回報數字與答錯的題目。

### V3. 轉述句整理的樣本
`citecheck prep-samples`（覆寫 `docs/quoted_samples/`）。回報：40 份中整理成功幾份、失敗幾份、被標為 `LOWER_COURT`／`PARTY` 的句子各幾句、`data/llm_failures.log` 新增幾筆。

### V4. 找相近裁判功能

1. `citecheck eval-suggest` 新增選項 `--with-llm`（需要 `client`）。對金標 L2 為 `COURT_HOLDING` 的每一題：把它當成「字號查不到」來處理——用它的主張句執行 `suggest.py` 的完整流程（G2 第 2–5 步），**不略過金標 jid**。輸出到 `--out` 指定的資料夾：
   - `suggest_metrics.json`：題數、至少列出 1 筆的題數、金標 jid 出現在 `similar` 中的題數、列出的總筆數、其中 jid 不是金標的筆數。
   - `suggest_extra.jsonl`：每一筆「jid 不是金標」的結果一行，欄位 `item_id`、`claim`、`jid`、`display`、`passage`、`confidence`。
2. 對 v2 執行：`citecheck eval-suggest eval/testset_v2.jsonl --with-llm --out eval/results_suggest`。
3. 由 AI 複核 `suggest_extra.jsonl` 的每一筆（用子代理，一次一筆，只給它 `claim` 與 `passage`）：問「這段裁判文字，是否表達了與這句話實質相同的見解？只回答 是 或 否，加一句理由。」把答案寫回該行的 `ai_review`、`ai_reason` 欄位。
4. 回報：金標命中題數／題數；列出的總筆數；「金標命中筆數＋複核為『是』的筆數」÷ 總筆數（稱為相近裁判精確率）。
5. 相近裁判精確率低於 0.80 → 在回報中列出每一筆複核為「否」的結果（主張句、段落、confidence），然後停下來。不要調整 `SUGGEST_THRESHOLD`。

測試：`--with-llm` 的流程用 `MockClient` 測一題（金標命中 1 筆、額外 1 筆）。

### V5. 網頁含內容比對的流程
在 Docker 啟動 `citecheck serve --host 0.0.0.0 --port 8000`（`-p 127.0.0.1:8000:8000`，帶 API 環境變數）。取 `eval/testset_v2.jsonl` 中第一題 A 類與第一題 S2 類的 `brief_text` 接成一段文字，用 `curl` 以表單方式 `POST /check`，帶 `l2=on`。確認回應：HTTP 200；含「查得到」標籤；含「請留意」標籤；含「API 呼叫」那一行統計；含頁首聲明。回報這五項各自是否成立，以及耗時。測完停止伺服器。

### Phase V 驗收
V1–V5 的結果都寫進 PROGRESS.md；V4 的 `--with-llm` 有測試；commit。

---

## 3. 之後

Phase V 通過後，剩下的是 SPEC_V2 §13 的 Phase E（最高法院民事全年度資料）。**資料還沒放進來之前不要做。** 目前沒有任何案號年度算完整涵蓋（115 年度缺 2026 年 1 月），所以編造的字號只會顯示「資料未涵蓋」；補上 `202601` 這一個月並重跑 `ingest-judgments`、`build-index` 後，115 年度就會生效。

這份文件沒有處理、維持現狀的已知限制（不要自行處理）：
- 裁定中「原法院以…」的轉述沒有固定的結尾句型，H1 的規則對裁定不生效；裁定仍靠轉述句整理與程序駁回規則把關。
- `前條`、`次條`、`同項`、`（109）台上字第123號`、法院簡稱等寫法仍抽不到，由「看起來有引用但沒辨識到」的區塊提示。
- 測試集仍是 AI 出題與標註。
