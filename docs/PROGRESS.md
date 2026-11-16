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

## SPEC_V2 Phase T

### 1. 完成了什麼（對應 SPEC_V2 §5）
- T1–T4：`eval/build_testset_v2.py` 產出 `eval/testset_v2.jsonl`（94 題，15 題 Markdown 樣式），自動驗證全部通過。
- T5：36 題由 36 個子代理各自獨立第二次標註；初次一致 35/36（97.2%），唯一不一致 B06 已重讀定案並記錄。`eval/apply_second_annotation.py` 負責合併。詳見 `docs/TESTSET_STATUS.md`。
- T6：`evaluate.py` 支援 `requires_covered_year`（`skipped_not_covered`）、`gold_statute_l2`（法條不符偵測率、誤報率、混淆矩陣）、`gold_not_in_force`／`gold_other_authorities`／`gold_unrecognized`（符合／總數）、綠色精確率、有效判斷率、逐題標籤正確率；`errors.csv` 增加 `STATUTE_L2_MISMATCH`；`citecheck eval` 同時印出這些指標。
- B1 的 `parse_article`（`src/citecheck/statute_check.py`，附 9 個測試）先於 Phase T 實作；對本機資料庫的民法 1,374 條現行條文全部可解析、0 條回傳 None（用一次性腳本檢查，沒有寫成讀取 `data/citecheck.db` 的測試）。

### 2. 我做的解讀
1. 題目的 `id` 沿用「類別代碼＋兩位流水號」，因此 S1–S11 的 id 是 `S101…`、`S601` 這樣（類別為 `S6` 時流水號接在後面）。
2. 帶 Markdown 的 15 題：A 2、B 1、C 1、D 1、E 1、F1 1、S1 2、S2 1、S3 1、S6 1、S7 1、G 1、其他依據 1；樣式輪流用 `**粗體**`、行首 `- `、行首 `> `。
3. 「重新讀全文後定案」的金標改動只發生在 B06（見 TESTSET_STATUS）；所有金標都在跑系統之前定案。
4. 子代理傳題方式：每題的提示詞寫成 `out/t5/<題號>.txt`（不含金標、gold_passage、note），請子代理只讀那一個檔案；規格要求「給它」這些資料，我認為檔案傳遞等價，但獨立性靠指示。
5. 決議字號（68 年度第 13 次民事庭會議決議(一)）沒有在資料裡找到可驗證的真實案例；該題不描述決議內容，只測試抽取。判例字號同理（G 類第 3 題）。

### 3. 被調整預期值的既有測試
無（Phase T 只新增測試）。

### 4. pytest
149 項通過（Phase A 結束時 136 項）。

### 5. 評測數字（AI 標註，未經人工複核；沒有 API 金鑰，全部 `--no-l2`）
- `eval/testset.jsonl`（v1）：Phase T 之前 → 之後：抽取 42/42 → 42/42；L1 39/42 → 39/42（沒有變化）。
- `eval/testset_v2.jsonl`（新）：Phase T 之前沒有 → 之後：抽取 91/91；L1 75/84（略過 7 筆年度未涵蓋）；錯誤 9 筆 = S6 5 題、S7 4 題，原因：Phase B 才會實作項、款檢查。

### 6. 沒做或沒驗證的事
- 沒有跑 L2（沒有 API 金鑰）；`citecheck eval` 的 L2 與法條 L2 指標只用單元測試驗證。
- 第二次標註的獨立性（見解讀 4）；判例、決議字號沒有對照官方來源。
- 第二次標註只涵蓋 36 題（A–E、I）；其餘題目金標由資料庫或字號事實決定。

## SPEC_V2 Phase B

### 1. 完成了什麼（對應 SPEC_V2 §6）
- B1：`check_structure` 接進 `verify_statute`（只在 `FOUND`、法規為「民法」、不是尚未施行條文時做）：項數超出 → `PARAGRAPH_NOT_FOUND`；款不在該項的款號清單 → `SUBPARAGRAPH_NOT_FOUND`（沒寫項且只有 1 項時看第 1 項；沒寫項且有多項時不檢查，`structure_unchecked=True`）；無法解析或不是民法 → `structure_unchecked=True`。兩種「不存在」狀態仍附 `article_text`。
- B2：`attach_statute_claims`（`extract.py`）、`STATUTE_SYSTEM_PROMPT`／`STATUTE_USER_TEMPLATE`（逐字）、`STATUTE_PROMPT_VERSION = 1`、`article_label`、`apply_statute_l2`（已接進 `build_report`）：只對「FOUND＋民法＋非尚未施行」的引用呼叫；條文超過 3,000 字不呼叫、標 `UNCERTAIN`；一筆一次呼叫；解析失敗重試 1 次（失敗回應記入 `data/llm_failures.log`），兩次失敗標 `UNCERTAIN`；`confidence >= 0.6` 才採信，否則 `UNCERTAIN`；`l2_confidence` 存 LLM 回的值。
- 報告顏色：法條 `FOUND` 且 `MISMATCH` 暫時用橘色（完整的四等級顯示留給 Phase D）。

### 2. 我做的解讀
1. `statute_check.py` 從 `judge.py` 匯入 `CONFIDENCE_THRESHOLD` 與 `_log_failure`（重用既有的失敗記錄）。
2. 法條 L2 的快取、重試與記錄沿用 `CachingClient`／`query_llm` 的作法；快取鍵含完整提示詞，所以提示詞版本改變時不會誤用舊結果。
3. `apply_statute_l2` 在 `client=None` 時把所有引用設為 `NOT_RUN`（包含不符合資格者）。
4. 主張句超過 300 字時，在該句內取「引用結尾往前 200 字到往後 100 字」，不超出句界。

### 3. 被調整預期值的既有測試
無。

### 4. pytest
174 項通過（Phase T 結束時 149 項）。

### 5. 評測數字（AI 標註，未經人工複核；沒有 API 金鑰，全部 `--no-l2`）
- `eval/testset.jsonl`（v1）：Phase B 之前 → 之後：抽取 42/42 → 42/42；L1 39/42 → 39/42（沒有變化；F01–F03 仍因 115 年度未涵蓋而為「資料未涵蓋」）。
- `eval/testset_v2.jsonl`：抽取 91/91 → 91/91；L1 75/84 → **84/84**（Phase T 的 9 個錯誤 S6、S7 全部修正）。沒有變差的指標。
- 法條 L2 指標（不符偵測率、誤報率、混淆矩陣）：**未執行**（沒有 `CITECHECK_MODEL` 與 API 金鑰）。

### 6. 沒做或沒驗證的事
- 沒有用真實 API 跑過法條 L2；提示詞只用 `MockClient` 測過格式與流程。
- 法條 L2 對 v2 的 S1／S2／S3（共 21 題）的實際表現未知，需要金鑰後執行 `eval eval/testset_v2.jsonl`。

### SPEC_V2 Phase B 補充：真實 API 的評測結果（使用者執行；`eval/testset_v2.jsonl`；AI 標註，未經人工複核）
- 抽取 91/91；L1 84/84（略過 7 筆）；L2 問題偵測召回率 19/21（90.5%）；L2 誤報率 0/15；不確定率 0/36；**綠色精確率 15/17（88.2%）**；有效判斷率 36/36；逐題標籤正確率 31/36（86.1%）；**法條不符偵測率 8/8、法條誤報率 0/13**。
- 6 個錯誤與原因（判決內容 5、法條 1）：
  - **B04（1042 號）、C04（114 號）→ 系統給 COURT_HOLDING（誤放行，綠色精確率的 2 個誤判）**：
    - B04：證據段落是本院段落中「上訴人於事實審主張…」的引述，被當成本院見解。這是 F2／C2（轉述句拆出去）要處理的問題。
    - C04：證據段落是原審理由「㈣審酌…年息3%…」，被切段標成 SUPREME_COURT；原因是它的上一段「㈠查496地號…」以「查」開頭，而「查」是 SUPREME 標記詞，在原審理由的小段落內誤觸發。
  - C05（1966 號）→ PARTY_ARGUMENT：原審理由「㈡被上訴人主張…為上訴人所否認，…有法律上利益存在」被「當事人陳述句」規則誤標為 PARTY_CLAIM（同類型誤觸發）。
  - B01、B05 → LOWER_COURT_VIEW：與書狀相符的內容在原審理由裡複述當事人抗辯，角色標為 LOWER_COURT；金標為 PARTY_ARGUMENT（第二標註也同意），屬原審複述當事人主張的邊界情形。
  - S301（民法第179條，金標 NOT_STATED）→ MATCHES：題句「被告無法律上原因受有利益，原告爰依民法第179條規定請求返還」其實描述了條文內容，金標可能標得太嚴；依 §1.5 不因系統輸出改金標。
- 觀察：C04、C05 是切段規則在「原審理由的小段落」誤觸發「查」與「當事人陳述句」；SPEC_V2 沒有安排修切段規則。

## SPEC_V2 Phase F

### 1. 完成了什麼（對應 SPEC_V2 §7）
- F1：`segment.SEGMENT_RULES_VERSION = 1`；`index.py` 與 `citecheck build-index [--rebuild]`（寫入 `segments`、`passages`、`judgments.procedural_dismissal`，每 500 筆 commit，可中斷重跑，版本不同會清空重做，並寫入 `meta.segment_rules_version`）；`judge.apply_l2` 改讀 `passages` 表，沒有索引或版本不符就在任何 API 呼叫之前報錯「請先執行 citecheck build-index」（`check`、`eval`、`prep-samples` 皆處理成 exit code 2）；`verify_judgment` 的 `main_text` 改從 `segments` 取；`ingest-judgments` 結束提示執行 build-index。
- F2：`prep.py`（`split_sentences`、`PREP_SYSTEM_PROMPT`／`PREP_PROMPT_VERSION = 1`、批次 ≤20,000 字且不拆 passage、輸出上限 2,000 token、回應驗證、重試 1 次並記錄失敗回應、`ensure_prepared`、`load_quotes`）；`llm.py` 的各 client 都接受選用參數 `max_tokens=600`（快取鍵不含它）；`prep-samples` 指令。
- F3：`build_report` 依序執行抽取與 L1 → 判決整理 → 判決比對（同一份書狀內 (主張句, jid) 相同只呼叫一次）→ 法條比對；`judge.run_limited`（最多 4 個並行、LLM 呼叫之外的事都在主執行緒）；`MAX_API_CALLS_PER_CHECK = 120`；`CachingClient` 有執行緒安全的 `api_calls`／`cache_hits`；`RunStats`、`Report.versions`、`Report.llm_model` 已填；`check` 結束印出「API 呼叫 N 次、快取命中 N 次、新整理裁判 N 份、因上限略過 N 筆、耗時 N 秒」；`eval-retrieval` 指令。

### 2. 我做的解讀
1. 重建索引時，除了清空 `segments`、`passages`，也一併清空 `quoted_sentences` 與 `judgment_prep`（它們的 `passage_seq` 指向舊的 passages，留著會對不上）。
2. 呼叫上限的計算：送出工作前檢查「實際 API 呼叫數 ＋ 正在進行中的工作數」是否已達上限（只看已完成的呼叫數，在並行時會一口氣送出超過上限的工作）；重試造成的額外呼叫不另外預留，所以上限是軟性的。
3. 第 2 步（判決整理）也受呼叫上限約束：被略過的裁判，引用它的判決引用不做比對（`NOT_RUN`，計入 `skipped_by_cap`）。目前 Phase F 還沒有用整理結果去修正比對（那是 Phase C），整理失敗也不影響判決標籤。
4. `procedural_dismissal` 在 build-index 時依 §8 C3 的規則算出並存入；「使用」（`COURT_HOLDING` → `UNCERTAIN`）屬 Phase C。
5. `quoted_sentences` 的 `sent_idx` 是該句在 `split_sentences(passage.text)` 的位置（從 0 起算）。

### 3. 被調整預期值的既有測試
| 測試 | 原預期 | 新預期 | 對應 |
|---|---|---|---|
| `tests/test_report.py` 的 fixture | 直接用資料庫跑含 L2 的 `build_report` | fixture 先 `build_index`（判決內容比對改讀 passages 表，沒有索引會報錯） | F1 第 3 點 |

### 4. pytest
213 項通過（Phase B 結束時 174 項）。

### 5. 評測數字（AI 標註，未經人工複核；沒有 API 金鑰，全部 `--no-l2`）
- `eval/testset.jsonl`（v1）：Phase F 之前 → 之後：抽取 42/42 → 42/42；L1 39/42 → 39/42（沒有變化）。
- `eval/testset_v2.jsonl`：抽取 91/91 → 91/91；L1 84/84 → 84/84（沒有變化）。
- `eval-retrieval`（BM25，`TOP_K = 5` 不變，只回報）：v1 有金標段落的 23 題，金標段落落在前 1／3／5／10 名為 17／22／23／23 題（與規格引用的數字相同）；v2 有金標段落的 32 題為 30／31／31／32 題。
- 真實資料庫索引：3,030 份判決，22,120 個 segments、21,813 個 passages，耗時約 7 秒；`procedural_dismissal` 標為 1 的有 1,632 份裁定、0 份判決。
- 含 L2 的評測：未執行（沒有 API 金鑰；上次使用者自行執行的結果見 Phase B 補充，Phase F 沒有改變判決標籤的決定邏輯）。

### 6. 沒做或沒驗證的事
- `prep-samples`（40 份樣本的轉述句整理）：**未執行**，需要 API 金鑰。
- `ensure_prepared`／`call_prep` 只用 `MockClient` 測過；真實模型是否能穩定回傳符合格式的 JSON、輸出上限 2,000 token 是否足夠，未驗證。
- 並行與呼叫上限只用計數的 `MockClient` 與時間測試驗證，沒有對真實 API 壓測。

## SPEC_V2 Phase C

### 1. 完成了什麼（對應 SPEC_V2 §8）
- C1：`judge.decide` 改為新的七條規則（本院 SUPPORTS 且沒有本院 CONTRADICTS → COURT_HOLDING；兩者都有 → UNCERTAIN；只有本院 CONTRADICTS → CONTRADICTED；LOWER_COURT SUPPORTS → LOWER_COURT_VIEW；PARTY_CLAIM SUPPORTS → PARTY_ARGUMENT；UNKNOWN／MAIN_TEXT → UNCERTAIN；其他 → NOT_IN_JUDGMENT），`l2_evidence` 依規格取信心最高者。`SYSTEM_PROMPT`、`USER_TEMPLATE` 一個字未改，傳給比對用 LLM 的內容仍只有段落文字（有測試確認不含角色）。
- C2：`retrieve.apply_quotes`（本院 passage 依轉述句標註在記憶體中重新拆分，同說話者的連續句子併成一段，`segment_index` 沿用）；`apply_l2` 在整理成功時用它；整理失敗時用原段落，結果為 COURT_HOLDING 就改為 UNCERTAIN（證據保留）。`split_sentences` 搬到 `retrieve.py`（避免 `prep`／`judge`／`retrieve` 循環 import），`prep.split_sentences` 仍可使用。
- C3：`procedural_dismissal` 為 True 的裁判，結果為 COURT_HOLDING 就改為 UNCERTAIN（Phase F 已在 build-index 時算出旗標）。
- C4：「綠色精確率」「有效判斷率」在 Phase T 已實作並有手算測試。

### 2. 我做的解讀
1. C2 第 2 點與 C3 的降級發生在 `decide` 之後、同一次 `apply_l2` 內；整理被呼叫上限略過的裁判，其引用本來就是 `NOT_RUN`，不受影響。
2. `apply_quotes` 以 passage 在清單中的位置當作 `seq`（`passages` 表的 `seq` 從 0 連續編號，與 `load_passages` 的順序一致）。
3. 報告上的 §11 加註文字（程序駁回、書狀沒寫法院名稱…）留給 Phase D 的報告重寫。

### 3. 被調整預期值的既有測試
無（舊的規則 1、3、4、5、6 的測試結果在新規則下不變；規則 2 是新的行為，新增測試）。

### 4. pytest
229 項通過（Phase F 結束時 213 項）。

### 5. 評測數字（AI 標註，未經人工複核）
- 沒有 API 金鑰，只跑 `--no-l2`：v1 抽取 42/42 → 42/42、L1 39/42 → 39/42；v2 抽取 91/91 → 91/91、L1 84/84 → 84/84（沒有變化）。
- **含 L2 的評測與 C5 放行檢查：未執行**（沒有 `CITECHECK_MODEL`／API 金鑰）。Phase B 補充記錄的最近一次真實 API 結果（Phase C 之前）：綠色精確率 15/17（88.2%）；金標為 PARTY_ARGUMENT 或 LOWER_COURT_VIEW 而系統輸出 COURT_HOLDING 的題數 2（B04、C04）。這兩項都還沒達到 C5 的門檻（≥0.90、≤1 題），Phase C 的效果沒有驗證。

### 6. 沒做或沒驗證的事
- **C5 放行檢查未執行**（§1.6 第 4 點）。有金鑰時：先 `citecheck prep-samples`（選用），再 `citecheck eval eval/testset_v2.jsonl --out eval/results_v2`（第一次會替被引用的裁判做轉述句整理，之後走快取），檢查綠色精確率 ≥ 0.90 與「金標 PARTY_ARGUMENT／LOWER_COURT_VIEW 卻輸出 COURT_HOLDING」題數 ≤ 1。若不符，依 §8 C5 在 PROGRESS.md 逐題列出錯誤，**不要自行調整門檻、提示詞或規則**。
- 已知不會被 Phase C 修正的缺陷：切段規則在原審理由的小段落被「查」或「當事人陳述句」誤觸發（C04、C05 的成因）；SPEC_V2 沒有安排修切段規則。

## SPEC_V2 Phase D

### 1. 完成了什麼（對應 SPEC_V2 §9，並實作 §11 的報告顯示規則）
- `server.py` 與 `citecheck serve [--port 8000] [--host 127.0.0.1] [--db 路徑]`：標準函式庫 `ThreadingHTTPServer`＋自己的 `BaseHTTPRequestHandler`（沒有用 `SimpleHTTPRequestHandler`）；只有 `GET /`（表單頁 `templates/form.html.j2`）與 `POST /check`（與 `check` 指令相同的 HTML 報告），其他路徑一律 404。
- 勾選框文字、按鈕處理中文字、兩個提示訊息（超過 50,000 字、空白）、頁首聲明都照規格逐字；L2 預設不勾（沒勾 → `client=None`）；勾了但環境變數沒設 → 回表單頁、HTTP 200、顯示錯誤與需要設定的環境變數名稱；缺索引時（勾選 L2 且有查得到的判決）同樣回表單頁並顯示「請先執行 citecheck build-index」。
- 安全檢查依序：`Host` 不是 `127.0.0.1:<port>`／`localhost:<port>` → 403；`POST` 的 `Origin` 不是這兩者 → 403；`Content-Length` 缺少、非整數或 > 400,000 → 413（不讀本文）。每個請求自己開並關閉 SQLite 連線；貼上的文字與報告不寫入磁碟；`log_message` 靜音、`log_request` 只印時間、方法、路徑、狀態碼；全部走 `autoescape` 的 Jinja2；表單頁只有那一段內嵌 JavaScript，沒有外部資源。
- §11 報告顯示：`ZH` 重寫（法條 15 列、判決 12 列、附加說明、其他依據、未辨識句子、相近裁判、上限略過）；`color_of` 改為 `level_of`（ok／check／notice／unknown，顏色＋文字標籤）；報告模板重寫為規格的頁面結構（頁首聲明 → 摘要四等級 → 原文 → 逐筆明細 → 未辨識的句子 → 頁尾涵蓋、法規資料過期提示、產生條件、來源、聲明）；明細依規格顯示連結、`這件裁判的主文`、主張句、證據段落；`stats_line` 供 `check` 與報告共用。
- README 補上 `serve` 的 Docker 用法（`--host 0.0.0.0` 搭配 `-p 127.0.0.1:8000:8000`）。

### 2. 我做的解讀
1. §11 沒有明確指定在哪個 Phase 實作；因為 §9 第 10 點要求每份報告顯示 §11 的頁首聲明，我把整個 §11 在 Phase D 一起做完。
2. 摘要的四個等級筆數把「其他依據」算在 unknown。
3. 兩個固定上限互相作用：表單把一個中文字編成 9 個位元組，所以約 44,500 個中文字就會超過 400,000 位元組的本文上限，回 413（不是友善訊息）；英數字文字才能碰到 50,000 字的訊息。這是規格兩個數字的結果，**沒有放寬**；有測試記錄這個行為。
4. `stats` 指令的「法規資料已超過 90 天沒有更新」提示（§11.5）尚未加；報告頁尾已有。
5. Host 檢查用伺服器實際綁定的埠號；`--port 0` 時用系統配置的埠。
6. 勾選 L2 時，沿用既有的 `CachingClient` 快取（只存模型回應，檔名是雜湊，不含貼上文字）。

### 3. 被調整預期值的既有測試
| 測試 | 原預期 | 新預期 | 對應 |
|---|---|---|---|
| `tests/test_report.py::test_colors_follow_spec` | `color_of` 回傳 green／orange／red／gray | `level_of` 回傳 ok／notice／check／unknown（法條 1 筆 ok、已刪除 notice、查無此條 check；判決查無 check、範圍外 unknown） | §11.1 |
| `test_report.py::test_html_is_self_contained_and_escaped` | 報告含「查無公開裁判」 | 含「在最高法院民事裁判的公開資料中查不到這個字號」 | §11.3 |
| `test_report.py::test_zh_wording_has_no_banned_terms` | `ZH["JUDGMENT"]["NOT_FOUND_IN_SCOPE"] == "查無公開裁判"` | 以 §11.3 的說明文字開頭 | §11.3 |

### 4. pytest
284 項通過（Phase C 結束時 229 項）。新增 `test_server.py`（16 項）、`test_display.py`（39 項：§11.2、11.3 每一列各一個、附加說明、頁面結構、頁尾、連結）。

### 5. 評測數字（AI 標註，未經人工複核；沒有 API 金鑰，全部 `--no-l2`）
- v1：抽取 42/42 → 42/42；L1 39/42 → 39/42。v2：抽取 91/91 → 91/91；L1 84/84 → 84/84。沒有變化（Phase D 沒有改抽取或查核邏輯）。
- 手動驗證：在瀏覽器看過報告新版面；在 Docker 內啟動 `serve`（`-p 127.0.0.1:8000:8000`）用 curl 確認 GET／、404、POST、錯誤 Host 403、外來 Origin 403。

### 6. 沒做或沒驗證的事
- 勾選 L2 的網頁流程只用 `MockClient`／環境變數缺失的情況測過，沒有對真實 API 試過。
- 沒有在各種瀏覽器檢查表單頁按鈕停用行為（只確認 HTML 內有那段 JavaScript）。

## SPEC_V2 Phase G

### 1. 完成了什麼（對應 SPEC_V2 §10）
- G1 `index.py`：`build_fts`（每次 `build-index` 結尾整個重建 `passages_fts`，trigram；只放 `role='SUPREME_COURT'`、判決或裁定、`procedural_dismissal=0` 的 passage）、`fts_query`（連續中文字／英數字片段取 3 字窗口、去重保序、前 60 個、加雙引號以 OR 相接）、`fts_search`（BM25 前 200 筆）、`has_fts`。`build-index` 會印出全文索引的段落數。
- G2 `suggest.py`：`apply_suggestions` 在 `build_report` 內接在 `apply_l2` 之後（只有帶 `client` 時）。觸發、同裁判只取最高排名一段、略過原引用 jid、5 段一次比對（`judge.SYSTEM_PROMPT`／`build_user_prompt`／`query_llm` 原封不動）、`SUGGEST_THRESHOLD=0.8`、`ensure_prepared` 等價流程（失敗或該段有任何轉述句 → 捨棄）、依 confidence 取前 3、`passage` 最長 400 字、相同主張句共用結果；所有 API 呼叫走 `run_limited`，計入 120 次上限。報告模板與 `ZH` 的相近裁判區塊在 Phase D 已備妥，這次只是讓 `similar` 有內容。
- G3 `citecheck eval-suggest <testset>` 與 `evaluate.eval_suggest`（不呼叫 API）。

### 2. 我做的解讀
1. 主張句長度的「15 個字」以 `clean_query` 之後去掉所有空白的字元數計。
2. 相同主張句共用結果時，「略過書狀原本引用的 jid」取這些引用的 jid 聯集。
3. 資料庫還沒有 `passages_fts`（沒重跑 `build-index`）時，`apply_suggestions` 靜默不動，不報 `IndexNotReady`，以免舊流程突然失敗；`eval-suggest` 則會報錯。
4. 「該 passage 有任何一句被標為轉述」以 `quoted_sentences` 中有該 `passage_seq` 的任何一列判定。
5. 比對提示只送 FTS 的原始 passage（未做 `apply_quotes` 重切），因為規格要求之後整段捨棄含轉述句的 passage。
6. `eval-suggest` 的名次以去重後的 jid 順序計；主張句不足 15 字或抽不到對應引用的題目算「查不到」。

### 3. 被調整預期值的既有測試
無。

### 4. pytest
310 項通過（Phase D 結束時 284 項）。新增 `tests/test_phase_g.py`（26 項）：`fts_query`、`build_fts` 的收錄條件、門檻 0.8、只算 SUPPORTS、同裁判去重、略過原引用、轉述句捨棄、整理失敗捨棄、最多 3 筆、主張句太短、各觸發狀態與標籤、共用結果、無 client／無索引、顯示格式與 400 字、API 上限。

### 5. 評測數字（AI 標註，未經人工複核；不呼叫 API；資料庫 3030 份裁判、4680 個 FTS 段落）
- v1：金標 L2 為 COURT_HOLDING 的判決引用 10 題；金標裁判在前 5 名 10/10、前 20 名 10/10。
- v2：15 題；前 5 名 15/15、前 20 名 15/15。
- 只回報，沒有據以改規則。資料庫目前只有部分月份，Phase E 匯入整年資料後候選會多很多，這個數字屆時要重跑。

### 6. 沒做或沒驗證的事
- G2 對真實模型沒有試過（沒有跑含 L2 的完整流程）；SUGGEST_THRESHOLD 0.8 是否合適未知。
- 相近裁判區塊在瀏覽器上的實際外觀沒有重看（模板 Phase D 已有）。

## 本地模型試跑（非 SPEC_V2 的 Phase；2026-11 補記）

### 做了什麼
- `llm.py` 新增兩個環境變數：`CITECHECK_OPENAI_BASE_URL`（相容 OpenAI 的伺服器完整網址，預設仍是 OpenAI；從 Docker 連本機 Ollama 用 `http://host.docker.internal:11434/v1/chat/completions`）、`CITECHECK_TIMEOUT`（單次請求秒數，預設 60）。程式不新增依賴，連線仍只發生在 LLM 呼叫。
- 用 Ollama 的 `qwen2.5:14b`（約 9 GB，使用者同意下載）跑 `eval/testset_v2.jsonl` 含 L2；結果在 `eval/results_local/`。指令：`CITECHECK_PROVIDER=openai CITECHECK_MODEL=qwen2.5:14b OPENAI_API_KEY=local CITECHECK_REASONING_EFFORT=off CITECHECK_TIMEOUT=300 CITECHECK_OPENAI_BASE_URL=... citecheck eval eval/testset_v2.jsonl --out eval/results_local`。

### 結果（AI 標註，未經人工複核；本地模型，不是正式 C5）
綠色精確率 13/16＝81.2%；金標為 LOWER_COURT_VIEW 卻輸出 COURT_HOLDING 的有 2 題（C04、C06）。兩個 C5 條件都不符。逐題標籤正確率 29/36，L2 問題偵測召回 18/21，誤報 2/15，法條不符偵測 7/8、法條誤報 0/13，沒有 UNCERTAIN，沒有格式失敗。**沒有調整任何門檻、提示詞或規則。**

### 答錯的綠色
| 題號 | 金標→系統 | 主張句（摘要） | 被當成證據的段落 | `quoted_sentences` 標註 | 判斷的錯誤原因 |
|---|---|---|---|---|---|
| C04 | LOWER_COURT_VIEW→COURT_HOLDING（0.9） | 無權占用山坡地，不當得利以申報地價年息 3% 計算 | 第 8 段（切成本院）：「…上訴人占用系爭土地所受利益，應以土地申報地價年息3%計算為適當…等詞，為其判斷之基礎。」 | 該段沒有任何句子被標為轉述 | 切段（原審理由的轉述被切成本院）＋F2 漏標（最後一句「…等詞，為其判斷之基礎」明顯是在轉述原判決） |
| C06 | LOWER_COURT_VIEW→COURT_HOLDING（0.8） | 買受人購買廠房作重型機車銷售工廠，出賣人應交付與契約相符的標的 | 第 6 段（本院，起首標記「按」）：「則以占用鄰地之圍牆、鐵棚等系爭增建…被上訴人簽訂系爭買賣契約，目的在購買重型機車銷售工廠…顯有疑義，應詳予究明。」 | 該段沒有任何句子被標為轉述 | 比對用 LLM 判錯（段落是本院對事實的質疑，沒有表達主張句的見解）＋切段／F2 漏標（開頭「則以…」是原審見解的延續） |
| D05 | CONTRADICTED→COURT_HOLDING（0.9） | 給付不能可歸責於債權人時，債務人不得請求對待給付 | 第 6 段（本院，「按」）：「當事人之一方因可歸責於他方之事由，致不能給付者，得請求對待給付…」（民法第 267 條） | 該段沒有被標為轉述（同判決其他段落有 LOWER_COURT／PARTY 標記） | 比對用 LLM 判錯（段落意思與主張句相反，卻判成 SUPPORTS） |

### 沒做
- 這不是正式的 C5：C5 要求用指定的真實 API。Phase C 的 C5 結果仍是「未通過、未用正式 API 重跑」。
- `prep-samples`、單獨的法條 L2 沒有另外跑（法條 L2 已包含在上面的整體評測）。
- 切段修正（弱標記不在下級法院或當事人子段落內切換角色）沒有做，因為不在 SPEC_V2 範圍，使用者要求先不處理；C04、C06 與此有關。

## 其他
- `citecheck stats` 在法規資料超過 90 天沒更新時顯示提醒（SPEC_V2 §11.5，Phase D 當時未做）。

## SPEC_V2 C5 放行檢查（真實 API，gpt-6-luna，使用者執行）

指令：`citecheck eval eval/testset_v2.jsonl --out eval/results_api`（`CITECHECK_PROVIDER=openai`；API 拒絕 `temperature`，依既有機制省略後重試）。結果在 `eval/results_api/`。

結果（AI 標註，未經人工複核）：
- 綠色精確率 14/15＝93.3% → 條件 1（≥ 0.90）**符合**。
- 金標為 PARTY_ARGUMENT 或 LOWER_COURT_VIEW、系統輸出 COURT_HOLDING：只有 C04，共 1 題 → 條件 2（≤ 1）**符合**。
- 其他：L2 問題偵測召回 20/21、誤報 1/15、不確定 0/36、逐題標籤正確 30/36、法條不符偵測 8/8、法條誤報 0/13、抽取 91/91、L1 84/84。
- 唯一答錯的綠色是 C04（原因見上一節：原審理由的轉述被切成本院，且轉述句整理漏標）。其餘 5 題判決錯誤皆不是綠色：B01、B05、C05、I02 為角色標籤不同，D01 金標 CONTRADICTED 系統輸出 NOT_IN_JUDGMENT；法條 S301 金標 NOT_STATED 系統輸出 MATCHES（金標是否過嚴未改）。
- 沒有調整任何門檻、提示詞或規則。C5 通過，Phase C 的放行檢查完成。

未做：`prep-samples` 仍未用真實 API 跑；Phase G 的 0.8 門檻未驗證。

## SPEC_V3 Phase H

### 1. 完成了什麼
- H1（`segment.py`）：`_LOWER_END`／`_LOWER_END_MID` 兩個常數與三條規則（拆段、往後、往前回填）；`SEGMENT_RULES_VERSION` 1 → 2；`docs/SEGMENT_RULES.md` 新增第 7 節；重新產生 `docs/segment_samples/`、`docs/segment_samples_ruling/`（各 20 份；不在新摘要裡的舊樣本檔已刪除，因為現在的資料庫多了 202305，抽到的樣本不同）。
- H2（`prep.py`、`judge.py`、`suggest.py`）：`current_model(client)`；`_current_ok(con, jid, model)` 多比對 `judgment_prep.model`；所有呼叫端傳入 `current_model(client)`。
- H3（`extract.py`）：`extract_full` 移除與其他依據 `span` 重疊的判決引用，之後才附加主張句與找未辨識句子。
- H4（`server.py`）：`MAX_BODY = 600_000`。
- H5（`models.py`、`extract.py`、`statute_check.py`）：`StatuteCitation.is_continuation`；`_ITEM_CONT` 連續列舉；`_ART` 候選落在已處理範圍內時跳過；continuation 不呼叫 LLM。
- 收尾：`build-index` 重建（見第 5 點）；H1 量測歸零；README 沒有指令變動、`DATA_SCHEMA.md` 沒有結構變動，都沒改。
- 使用者另外要求「給使用者的介面要好用、一目瞭然」（不在 SPEC_V3 內）：
  - 表單頁：分成「1 貼上書狀文字」「2 選擇查核範圍」兩步，加上使用說明，以及「報告怎麼看」的四個等級說明。
  - 報告頁：摘要改成四個色塊，依「請確認、請留意、無法查核、查得到」排列，每塊附一句說明；摘要下新增「各筆引用一覽」，依處理優先順序排列，點選可跳到明細；明細卡片左側加等級色條；書狀原文的文字標籤改成行內標籤；連續列舉的「第5項」後面補上「（民法第195條）」；網頁版報告頂端加「← 再查一份書狀」連結；加上列印樣式。
  - 這些都只動版面與 CSS。表單頁的 JavaScript 仍只有原本那一段，報告頁沒有 JavaScript，也不載入外部資源。SPEC_V2 規定逐字使用的文字都沒改。

### 2. 我做的解讀
1. H2：`plan_prep` 多一個 `model` 參數；`_write` 改為直接收 `model`，由 `store_prep` 用 `current_model(client)` 取得。這樣「沒有本院段落、直接記為 ok」的那筆紀錄也會寫入目前的模型，否則換模型後每次都會重寫。
2. H3：`_extract_judgments` 產生的警告不隨之移除（規格只說移除判決引用）。
3. H5：continuation 的 `raw` 從「第」開始，不含前面的「、」「及」等連接詞（依規格）；第 4 點的保險只套用在 `_ART` 候選，不套用在「同條」。
4. 介面新增的文字（`report.py` 的 `LEVEL_HELP`、`OVERVIEW_TITLE`、`BACK_LINK_TEXT`；`server.py` 的 `TEXT_HINT`、`L2_HINT`、`LEGEND_TITLE`）不在 SPEC_V2 §11 裡，是依使用者要求加的。已確認不含 §11 禁用的詞。`render_html` 多了 `back_link` 參數，預設不顯示，所以 `check` 指令輸出的靜態報告不受影響。
5. 摘要四個等級的排列由「查得到、請確認、請留意、無法查核」改成「請確認、請留意、無法查核、查得到」；§11.5 只規定要有四個等級的筆數，沒有規定順序。

### 3. 被調整預期值的既有測試
| 測試 | 原預期 | 新預期 | 對應 |
|---|---|---|---|
| `test_server.py::test_oversized_or_missing_content_length_is_413_without_reading_the_body` | `Content-Length: 400001` → 413 | `600001` → 413 | H4 |
| `test_server.py::test_very_long_cjk_text_hits_the_body_limit_first`（改名為 `test_h4_50000_cjk_characters_fit_under_the_body_limit`） | 45,000 個中文字 → 413；40,000 個 → 200 | 50,000 個中文字 → 報告；50,001 個 → 表單與「文字超過 50,000 字，請分段查核。」；600,001 位元組 → 413 | H4 |

切段的既有測試都不需要調整。

### 4. pytest
332 項通過（Phase H 之前 310 項）。新增：H1 5 項（`test_segment.py`）、H2 2 項（`test_phase_f.py`）、H3 1 項與 H5 6 項（`test_extract.py`）、H5 2 項（`test_statute_check.py`）、介面 6 項（`test_display.py` 4 項、`test_server.py` 2 項）；H4 取代 1 項。

### 5. 評測數字（AI 標註，未經人工複核；`--no-l2`，不呼叫 API）
「之前」是用 Phase H 之前的程式碼（HEAD `302f28e`）對重建前的索引跑的。

| 指標 | v1 之前 → 之後 | v2 之前 → 之後 |
|---|---|---|
| 抽取召回率 | 42/42 → 42/42 | 91/91 → 91/91 |
| L1 正確率 | 39/42 → 39/42 | 84/84 → 84/84（略過 7 筆：年度未涵蓋） |
| eval-retrieval 前 1／3／5／10 名 | 17／22／23／23 → 相同 | 30／31／31／32 → 相同 |
| eval-suggest 前 5／20 名 | 10／10 → 相同 | 15／15 → 相同 |

- v1 的 3 筆 L1 錯誤在 Phase H 之前就有：F01、F02、F03 的金標是 `NOT_FOUND_IN_SCOPE`，系統輸出 `DATA_NOT_COVERED`。原因是 115 年度缺 2026 年 1 月，還不算完整涵蓋（SPEC_V3 §3）。
- `build-index`（版本 1 → 2）：3,030 份全部重建，耗時 19.8 秒（含 Docker 啟動）。段落 22,120 → 22,129；passages 21,813 → 21,815；全文索引段落 4,680 → 4,729；`quoted_sentences` 351 → 0、`judgment_prep` 92 → 0（依規格清空，下次用到時重新整理）。
- 收尾第 3 點的量測（所有判決中，原審理由範圍內角色卻不是 LOWER_COURT 的段數）：33 段（11 份判決）→ **0 段**。
- C04（115 年度台上字第 114 號）：被當成證據的第 8 段（「…等詞，為其判斷之基礎。」）現在是 LOWER_COURT；之前第 5–8 段（㈠查…到㈣審酌…）都被切成 SUPREME_COURT，現在全部是 LOWER_COURT。實際的 L2 標籤要等 Phase V 用 API 驗證。

### 6. 沒做或沒驗證的事
- 所有需要 API 的驗證（L2、轉述句重新整理）都留給 Phase V。
- 介面只在本機 Docker 用瀏覽器看過深色與淺色模式；手機寬度只靠 CSS 斷點，沒有實機測試。
