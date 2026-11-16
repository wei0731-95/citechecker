# 進度紀錄

## 骨架（Phase 0 之前）
- 建立目錄結構、pyproject.toml（requires-python >=3.11）、Dockerfile（python:3.11-slim）、data/law_aliases.json（規格 6.3 固定內容）。
- 專案根目錄即 laws_test（非 citecheck/ 子目錄）。
- Phase 0 已完成資料檢查，見 docs/DATA_SCHEMA.md；等待使用者確認 §D 待確認問題。

## Phase 1 — 匯入（完成）
- 實作 db.py、ingest_laws.py、ingest_judgments.py、cli.py（ingest-laws / ingest-judgments / stats）。
- 結果：法規 1029（排除 322 部廢止）、條文 40954、判決 2521、案號年度 110~115、裁判日期 20260203~20260827；與 Phase 0 統計一致。重跑兩次判決數不變。
- pytest（Docker python:3.11-slim）4 項通過；測試用假資料在 tests/fixtures（法規 XML）與測試內動態產生（判決）。
- 決議：涵蓋範圍以「案號年度」min~max 判斷（meta: year_min/year_max）；doc_kind 的提案裁定、大法庭裁定歸為「裁定」。
- 與規格不符／備註：法規來源為 XML；pyproject 等檔案曾含 BOM，已移除。執行方式：`docker run --rm -v <專案>:/app -w /app citecheck citecheck stats`。
- 未解：meta 尚未記錄大法庭判斷（Phase 2 以字別含「大」處理）。

## Phase 2 — 抽取與 L1（完成）
- 實作 models.py、normalize.py、extract.py、verify.py、report.py（JSON）、`check --no-l2`（可加 `--db`）。pytest 58 項全過（Docker python:3.11）。
- 測試涵蓋：§6.1 各轉換、J1–J10、S1–S10、主張句、L1 各狀態（含 AMBIGUOUS、DATA_NOT_COVERED）、端到端報告。
- 實測（假書狀 tests/fixtures/fake_brief.txt 對真實資料庫）：FOUND／ARTICLE_NOT_FOUND／UNRESOLVED_REF／OUT_OF_SCOPE／DATA_NOT_COVERED 皆正確。

### 我做的解讀（規格未明寫，請確認）
1. 連列引用（J5/J6）：尾端的「判決／裁定」套用到同一串所有案號。
2. 最高法院但字別不在民事字別清單（如台非）→ OUT_OF_SCOPE；字別含「大」→ OUT_OF_SCOPE。
3. 案號前沒有可辨識的法院名（且非「同院」可承接）→ 不列入引用，記入 warnings。法院名僅辨識：最高法院、最高行政法院、同院、智財法院、憲法法庭、臺灣○○地方法院、（臺灣）高等法院（含分院）。
4. 單獨的「第N條」（無法規名、無「同法」、不在頓號／及／至 連列中）一律 UNRESOLVED_REF，即使前文出現過法規。
5. 條文連列分隔詞：、 及 暨 與 或 至（不含逗號）。
6. 「同條」承接前一筆法條引用的法規與條號；「同法」承接前一筆的法規。
7. 條號只認「第N條(之M)」；法規簡稱只用 data/law_aliases.json 固定內容。
8. 主張句字數門檻（15 字）只計文字／數字，不計標點空白；併入前一句以「，」連接。
9. normalize 對整份書狀做（含 臺→台），Span 與 raw 皆為正規化後文字；原文版本尚未保留（Phase 5 顯示時再議）。

### 已知限制
- 法院名稱若不是上述形式（如「臺北地院」簡稱）不會辨識。
- 資料庫僅 110–115 案號年度，書狀引用常見的舊判決多為 DATA_NOT_COVERED。

## Phase 3 — 切段（完成，等待人工檢查）
- 實作 segment.py；規則與驗證見 docs/SEGMENT_RULES.md；tests/test_segment.py（pytest 共 65 項全過）。
- 樣本：docs/segment_samples/（20 份判決，seed=42，另有 _summary.md）；重新產生：`python -m citecheck.segment`。
- 全部 336 份判決：理由段 UNKNOWN 1.4%（規格起始清單為 10.4%），未達 §11 的三成門檻。
- 與規格不符／新增：新增「當事人陳述句」判定；header／footer 另列 part，Phase 4 擬略過（待確認）。
- **在你確認前不進 Phase 4。**

## Phase 4 — L2（完成）
- 實作 retrieve.py（400 字 passage、字元 bigram BM25、前 5 筆）、llm.py（AnthropicClient、MockClient、磁碟快取 CachingClient）、judge.py（逐字提示、JSON 驗證、§7.5 規則）；`check` 不加 `--no-l2` 時執行 L2。pytest 共 83 項通過，全程不呼叫真實 API。
- 以 MockClient 對真實資料庫整合測試：流程可跑通（檢索 → 提示 → 解析 → 規則）。**尚未用真實 API 跑過**（需設定 CITECHECK_MODEL、ANTHROPIC_API_KEY）。
- 實作時的決定：
  1. 檢索略過 header／footer（retrieve.INCLUDE_HEADER_FOOTER=False），與規格「UNKNOWN 也納入」不同；案號、當事人、法官名單無說理內容。
  2. 判決段落不足 5 段時，提示詞省略缺的 `[n]` 行，其餘格式不變。
  3. 解析失敗時，重試前會從快取刪除該壞回應，避免重試又讀到同一個壞結果。
  4. 檢索不到任何段落（主張句去除引用後為空）→ UNCERTAIN。
  5. AnthropicClient 重試 2 次與 60 秒逾時由 SDK 設定；temperature 固定 0（若所選模型拒絕該參數會報錯，屆時再議）。
- **發現的問題（影響 L2 準確度）**：切段只用判決驗證。裁定的理由段 UNKNOWN 高達 22.6%（1268 / 5617 段），2185 份裁定中 486 份過半段落為 UNKNOWN。原因：裁定常以「一、上訴第三審法院，非以…」直接說理，沒有「本院／按／查」標記。被引用的若是裁定，其最高法院見解可能落入規則 5 → UNCERTAIN。建議：為裁定補充標記詞或在 Phase 5 先量測 UNCERTAIN 比例再決定。

### Phase 4 後續：裁定切段補強
- segment.py：私人使用區字元視為編號；裁定理由段起始角色改為 SUPREME_COURT。pytest 86 項通過。
- 裁定理由段 UNKNOWN 22.6% → 0.0%；判決維持 1.4%。詳見 docs/SEGMENT_RULES.md §6，含誤判風險。
- 裁定樣本：docs/segment_samples_ruling/（20 份，seed=42；重新產生 `python -m citecheck.segment --ruling`），等待人工檢查。

## Phase 5 — 報告與評測（完成）
- report.py：`render_html`／`write_html`（Jinja2 模板 templates/report.html.j2，單一檔案、無外部資源、無 JavaScript）；狀態中文說明集中在 `ZH` 字典；顏色規則 `color_of`；固定頁尾聲明。`check` 同時輸出 report.json 與 report.html。
- evaluate.py：抽取召回率、L1 正確率、L2 問題偵測召回率／誤報率／不確定率、混淆矩陣；`eval` 指令輸出 metrics.json、errors.csv（含空白的 cause 欄供人工填錯誤原因）。
- tests/fixtures/fake_testset.jsonl（5 題 7 筆）以 `eval --no-l2` 驗證，與手算一致：抽取 6/7、L1 5/6；L2 指標另以手算案例測試。pytest 共 92 項通過。
- 評測定義的解讀：配對時系統引用須同類型且 Span 重疊（以正規化後文字定位金標 raw）；未被抽到的金標在 L2 偵測召回率算「未偵測」，在誤報率不計；不確定率分母＝已配對且金標 L2 不是 NOT_RUN 的判決引用；混淆矩陣橫軸多一欄 MISSING（未抽到）。
- 判決顏色：L1 為 FOUND 時依 L2 標籤上色（COURT_HOLDING 綠；CONTRADICTED 紅；PARTY_ARGUMENT／LOWER_COURT_VIEW 橘；其餘灰，含 NOT_IN_JUDGMENT，依規格）；L1 非 FOUND 依 L1 狀態。
- 尚未做：eval/testset.jsonl（由你依 docs/TESTSET_GUIDE.md 製作）；尚未用真實 API 跑 L2。

### 加入 OpenAI（ChatGPT API）支援（依使用者要求）
- llm.py：新增 `OpenAIClient`（標準函式庫 urllib 直接呼叫 Chat Completions，**未新增套件**）與 `client_from_env()`；以環境變數 `CITECHECK_PROVIDER=openai` 切換（預設 anthropic）。需 `CITECHECK_MODEL`、`OPENAI_API_KEY`。
- 設定同 Anthropic：temperature 0、600 輸出 token（參數名 max_completion_tokens）、60 秒逾時、失敗重試 2 次。
- 偏離規格處：若所選模型拒絕 temperature=0（部分新模型如此），改用模型預設值重試一次並在 stderr 警告。
- pytest 95 項通過（HTTP 以假回應測試，未呼叫真實 API）。尚未用真實 API 跑過。

### 冒煙測試集（tests/fixtures/fake_smoke_testset.jsonl）
- 5 題，全部引用同一份真實判決（TPSV,114,台上,1281，廢棄發回），分別對應 A（本院見解）、B（當事人抗辯）、C（原審見解）、D（相反）、E（無關）。**標註者只有 Claude 一人，僅供冒煙測試，不可當正式評測。**
- `eval --no-l2`：抽取 5/5、L1 5/5。檢索（不用 API）：A、B、C、D 的第一名段落角色都符合預期；C 另有一個本院段落（seg18「原審雖…認定…」複述原審見解）也在前 5，可能讓 C 被判成 COURT_HOLDING（切段以段落為單位的已知限制）。
- OpenAI：Luna 在 C 題兩次回傳空內容（推理 token 用完 600 上限）。改為送 `reasoning_effort`（預設 low，環境變數 CITECHECK_REASONING_EFFORT 可覆寫，off 為不送）；API 拒絕 temperature 或 reasoning_effort 時各自省略並警告。max_completion_tokens 仍為 600。空回應時印出 finish_reason 與 reasoning_tokens；解析失敗的原始回應記於 data/llm_failures.log。
- 冒煙測試（gpt-6-luna，reasoning_effort=low，5 題）：抽取 5/5、L1 5/5、L2 不確定率 0%；L2 答對 A、B、D、E，C（原審見解）被判成 COURT_HOLDING。快取回應顯示 C 的第 1 筆（原審段落）與第 2 筆（本院段落「原審雖依…認定…，然…程序瑕疵」複述原審認定）皆 SUPPORTS → 規則 1。屬已知限制：角色以段落為單位，本院段落內複述原審見解時會被當成本院見解。是否改為句子層級的角色判定（規格固定項目）待決；建議先用 30 題正式測試集統計此類錯誤的頻率。

### 測試集輔助（eval/make_candidates.py → eval/candidates.md）
- 產生挑題用清單：12 份「廢棄」判決（C 類與 A/D/E 素材）、12 份「上訴駁回」判決（B 類與 A/D/E 素材），各附本院／當事人／原審段落節錄；6 個已確認資料庫 0 筆的改號字號（F 類）；已刪除條文與不存在條號素材（法條題）。重新產生：`python eval/make_candidates.py`（seed=42）。
- 角色為規則式切段結果，僅供挑題；金標必須由兩位標註者回判決全文獨立確認。30 題本身（含金標）尚未製作，eval/testset.jsonl 待標註者完成。

### 30 題測試集（eval/testset.jsonl，單一 AI 標註者）
- 由 eval/build_testset.py 產生並自動驗證；詳見 docs/TESTSET_STATUS.md。`eval --no-l2`：抽取 42/42、L1 42/42。L2 尚待用真實 API 跑（25 次呼叫）。未達 TESTSET_GUIDE 的雙人獨立標註要求，結果不能當正式證據。

### 30 題 L2 評測結果（gpt-6-luna，reasoning_effort=low，無 temperature；單一 AI 標註者；25 次 API 呼叫）
- 抽取 42/42、L1 42/42；L2 問題偵測召回率 15/15、誤報率 0/10、不確定率 0/25（皆達 TESTSET_GUIDE §5 的「繼續」門檻，但樣本小、單一標註者，不能當正式證據）。
- 逐題標籤正確率 17/25：A 10/10、D 3/3、E 2/2、B 1/5、C 1/5。B、C 的 8 個錯誤全部被判成 CONTRADICTED（不是 PARTY_ARGUMENT／LOWER_COURT_VIEW）。
- 原因（由快取回應重建）：B、C 題的設計是「最高法院明確不採」，所以本院段落必然與書狀主張相反 → §7.5 規則 2（本院 CONTRADICTS）先於規則 3、4。8 題中 6 題另有原審／當事人段落判 SUPPORTS（B02、B03、C02–C05），另 2 題（B01、B05）檢索到的當事人段落被判無關。屬規格規則順序與指南 B／C 定義的衝突，不是模型判斷錯誤。
- 未依結果修改金標（TESTSET_GUIDE §2.7）。
- **決定（使用者）**：維持規格 §7.5 的規則順序，不修改程式。報告 L2 結果時需同時寫出「問題偵測 15/15」與「逐題標籤 17/25」，並說明 B、C 類被判為 CONTRADICTED 的原因。金標不修改。

### 測試集人工複核工具
- eval/make_review_sheet.py → eval/review/review_sheet.html：盲測複核表（只顯示書狀與判決全文，不顯示金標），25 題（L2 金標非 NOT_RUN 者）；答案暫存於瀏覽器，可匯出 review_answers.json。
- eval/compare_review.py：比對複核者答案與金標，算初次一致率並列出不一致題目；`--apply` 只寫 annotators／agreed_first_pass，**不改金標**。F、G 類與法條狀態是資料庫可驗證的事實，不需人工標註。

## SPEC_V2 Phase A

### 1. 完成了什麼（對應 SPEC_V2 §4）
- A0：`git init` 並 commit（`chore: snapshot before SPEC_V2`）；`.gitignore` 加 `data/*.db`、`data/llm_failures.log`、`out/`；§3 的 Enum、模型欄位、資料表一次加好（`SCHEMA_VERSION = 2`）；舊資料庫改名 `data/citecheck.v1.db`，`connect()` 對舊結構報錯。
- A7：`strip_markdown`（`**`、`__`、反引號；行首 `#`、`- `、`* `、`> `）。
- A8：`normalize_with_map` 與 `_sub`；`Report.original_text`；每筆引用（含 `other_authorities`、`unrecognized`）的 `original_span`；HTML 全文改顯示原文，重疊時後一筆從前一筆結尾開始上色。
- A1：`boundary_ok` 與兩個常數（逐字）；`data/law_aliases.json` 新增「刑法」「憲法」。
- A2：`NON_STATUTE_SUFFIXES`、`BACKREF_SUFFIXES`、`ORDER_SUFFIXES`（逐字）與五段判斷表；`verify_statute` 不覆蓋 `LAW_NAME_UNCERTAIN`、`LAW_OUT_OF_SCOPE`。
- A11：廢止法規匯入（`repealed=1`），詞典含廢止法規名稱，`LAW_REPEALED`，`stats` 分開顯示。
- A9：`url`、`pcode`、`pdf_url` 入庫；`verify` 填 `law_url`／`article_url`／`pdf_url`（`ARTICLE_URL` 常數定義在 `verify.py`，`report.py` 重新匯出）。
- A10：`effective_date`、`effective_note` 入庫；`articles_in_note`；`not_in_force_note`＋`structure_unchecked`。
- A3：`judgments.month`、`months_imported`、`covered_years`、新的 `verify_judgment` 流程（先查資料庫、查不到才看涵蓋範圍）、`stats` 新增三行與低筆數月份、`data_coverage` 填 `months_imported`／`covered_years`。
- A4：`domain_stated`；A5：`判例`／`決議` 為 OUT_OF_SCOPE；A6：沒寫法院時依字別推定；A12：`other_authorities`；A13：`unrecognized`。
- `extract_full()` 回傳 `ExtractResult`（含 `other_authorities`、`unrecognized`）；`extract_all()` 保留，仍回傳三元組。
- 重新匯入：8 個月（202305、202602–202608）、3,030 筆判決；法規 1,029 現行、322 廢止、47,377 條。`stats` 輸出：已涵蓋的案號年度「無」（缺漏月份 32 個：202306–202601）。詳見 `docs/DATA_SCHEMA.md` §E。

### 2. 我做的解讀（文件沒寫到、我自己決定的地方）
1. `verify_judgment`：同一字號查到多筆、且書狀寫的 `doc_kind` 一筆都對不上時 → `AMBIGUOUS`（不是查無）。理由：資料庫確實有這個字號，標成紅色「查無」違反 §0 的優先順序。
2. A2 的 `run` 只取「緊鄰」`第N條` 前面的連續中文字，不跳過空白。
3. A12 的「決議」比對在 `normalize` 之後的文字上做；`normalize` 已把 `第七四八號` 轉成 `第748號`，所以 `raw` 是阿拉伯數字寫法。
4. `ARTICLE_URL` 常數放在 `verify.py`（`verify_statute` 要用，`report.py` 又 import `verify`，放在 `report.py` 會循環 import）；`report.py` 從 `verify` 匯入，`report.ARTICLE_URL` 仍可用。
5. A9「條號不符合 `^\d+(-\d+)?$` 只給 `law_url`」：資料庫裡純數字條號（如憲法增修條文的 `1`）符合該式，所以也會有 `article_url`；只有真正帶其他寫法的條號才只給法規連結。
6. `main_text`（§11）在 `verify_judgment` 查得到時就用 `segment()` 取出 `part == "main"` 的段落、以換行相接填入。
7. 暫時讓 `report.ZH`、`color_of` 認得新狀態（文字照 §11.2、§11.3 的說明文字，顏色依 §11.1 的等級對應），避免報告當掉；完整的四等級重寫（`level_of`）留給後面的 Phase。
8. `A6`：沒寫法院、字別在清單內時，`last_court`（供「同院」承接用）不更新，因為這個法院不是書狀寫出來的。

### 3. 被調整預期值的既有測試
| 測試 | 原預期 | 新預期 | 對應 |
|---|---|---|---|
| `test_ingest_laws` | 回傳 `{"laws":2,"articles":4,"abolished_skipped":1}`；廢止法不在 `laws` | 回傳 `{"laws":2,"repealed":1,"articles":5}`；廢止法 `repealed=1` | A11 |
| `test_ingest_laws_idempotent` | 條文 4 筆 | 5 筆（廢止法條文也匯入） | A11 |
| `test_court_missing_is_skipped_with_warning` → `test_court_missing_is_inferred_for_supreme_civil_case_word` | 找不到法院 → 略過並警告 | 字別在清單內 → 輸出，`court_inferred=True`，無警告 | A6 |
| `test_judgment_not_found_in_scope` | 115 台上 8888 → `NOT_FOUND_IN_SCOPE` | 書狀寫「民事」→ `NOT_FOUND_IN_SCOPE`；沒寫 → `NOT_FOUND_CIVIL_ONLY` | A3、A4 |
| `test_eval_no_l2_matches_hand_computation`（含 `fake_testset.jsonl`） | 抽取 6/7、L1 5/6、錯誤 2 列（E01 抽取失敗） | 抽取 7/7、L1 6/7、錯誤 1 列（E01 改為推定法院後抽得到）；B01 的 raw 改為「…第8號民事判決」，維持金標 `NOT_FOUND_IN_SCOPE` | A3、A4、A6 |
| `test_report.py` 的 `test_colors_follow_spec` 等 | 書狀「…第99號判決」→ 紅 | 書狀改寫「…第99號民事判決」仍為紅 | A3、A4 |
| 各測試資料庫 fixture | `INSERT … VALUES` 位置參數；以 `year_min`／`year_max` 當涵蓋 | 改用具名欄位；加 `months_imported` | §3.3、A3 |

### 4. pytest
136 項通過（Phase A 之前 96 項）。

### 5. 評測數字（AI 標註，未經人工複核；沒有 API 金鑰，全部加 `--no-l2`）
- `eval/testset.jsonl`（v1）：Phase A 之前 → 之後
  - 抽取召回率 42/42 → 42/42
  - L1 正確率 42/42 → **39/42（92.9%）**
  - 變差的題號：**F01、F02、F03**（最高法院 115 年度台上字第 7756、3081、2224 號）。原因：金標 `NOT_FOUND_IN_SCOPE`，現在是 `DATA_NOT_COVERED`——資料缺 202601，115 年度不算已涵蓋（§4 A3 已預告）。
- `eval/testset_v2.jsonl`：尚不存在（Phase T 才建）。

### 6. 沒做或沒驗證的事
- 還沒有用真實資料庫對真實判決的全文做 A1／A2 的大量量測（SPEC_V2 的 99.3% 是它自己量的）。
- A12／A13 的規則只用單元測試驗證。
- HTML 報告改顯示原文後，只有單元測試涵蓋（沒有開瀏覽器檢查版面）。
- §11 的四等級顯示、`level_of`、頁首聲明等尚未實作（後面的 Phase）。
