# 書狀引用查核器（citecheck）— 給 code 模式的實作規格

> 使用方式：把這整份文件貼給 code 模式（或存成 repo 根目錄的 `SPEC.md`，再請它「依 SPEC.md 從 Phase 0 開始實作」）。
> 一次只做一個 Phase，該 Phase 的驗收條件全部通過後才進下一個。

---

## 0. 給你（coding agent）的工作規則

1. **不要猜。** 本文件沒寫到、或與實際資料不符的地方，停下來列出問題問我，不要自行假設後繼續寫。
2. **資料格式一律以實際檔案為準。** 本文件中標示「預期」的欄位名稱都尚未驗證。Phase 0 必須先打開真實檔案確認，寫進 `docs/DATA_SCHEMA.md`，之後的程式只能依該文件寫。
3. **禁止的事：**
   - 不得爬取任何網站（含司法院裁判書查詢系統、Lawsnote、法源、七法）。資料只能來自我放進 `data/raw/` 的官方開放資料檔。
   - 不得編造法條內容、判決字號、判決內容，測試用的假資料必須放在 `tests/fixtures/` 並在檔名加 `fake_`。
   - 不得把整份書狀送給 LLM。只能送「主張句」與「判決段落」（見 §7）。
   - 不得新增本文件未列出的相依套件、網頁伺服器、向量資料庫、前端框架。
4. **每個 Phase 結束時**：跑 `pytest`，全部通過後，在 `docs/PROGRESS.md` 寫下完成項目、未解問題、與規格不符之處，然後停下來等我確認。
5. 所有面向使用者的輸出文字用繁體中文；程式碼、識別字、commit 訊息用英文。
6. 輸出用語限制：查不到判決時只能寫「查無公開裁判」，不得寫「不存在」「偽造」「虛構」。報告頁尾固定加上：「本工具僅提供引用查核輔助，不構成法律意見。」

---

## 1. 專案目標（一句話）

輸入一份民事書狀的文字，找出其中引用的**法條**與**最高法院民事裁判**，回報：(L1) 這些引用是否查得到；(L2) 書狀對該判決的說法，是否真的是**最高法院自己的見解**，而不是當事人主張或被廢棄的原審見解。

## 2. MVP 範圍（固定，不得擴大）

| 項目 | 範圍內 | 範圍外（一律回報「無法查核」） |
|---|---|---|
| 判決 | 最高法院**民事**判決與裁定 | 其他法院、刑事、行政、判例、決議、大法庭裁定、釋字、函釋、學說 |
| 法條 | 全國法規資料庫**現行**中央法規 | 歷史條文、地方法規、行政規則 |
| 輸入 | `.txt`（UTF-8）、`.docx` | PDF、圖片、掃描檔 |
| 介面 | CLI + 靜態 HTML 報告 | 網站、登入、資料庫伺服器 |
| L2 | 只對 L1 結果為 `FOUND` 的最高法院民事判決執行 | 法條內容語意比對（不做） |

## 3. 技術選型（固定）

- Python 3.11
- 儲存：SQLite（標準函式庫 `sqlite3`），單一檔案 `data/citecheck.db`
- 套件（僅此清單）：`typer`、`pydantic>=2`、`python-docx`、`rank-bm25`、`jinja2`、`anthropic`、`pytest`
- 檢索：BM25，斷詞方式固定為**字元 bigram**（不使用 jieba、不使用向量嵌入）
- LLM：透過 `LLMClient` 介面呼叫（§7.4）。模型名稱**只能**從環境變數 `CITECHECK_MODEL` 讀取，API 金鑰從 `ANTHROPIC_API_KEY` 讀取；任一未設定就報錯結束，不得寫死預設值。

## 4. 目錄結構（固定）

```
citecheck/
  SPEC.md
  pyproject.toml
  README.md
  docs/
    DATA_SCHEMA.md        # Phase 0 產出
    SEGMENT_RULES.md      # Phase 3 產出
    PROGRESS.md
  data/
    raw/laws/             # 我放入：全國法規資料庫開放資料檔
    raw/judgments/        # 我放入：司法院裁判書開放資料檔（解壓後）
    law_aliases.json      # 法規簡稱對照表（§6.3）
    citecheck.db          # 由 ingest 產生，不進版控
  src/citecheck/
    __init__.py
    cli.py                # typer 進入點
    models.py             # 所有 pydantic 模型與 Enum（§5）
    normalize.py          # 文字正規化（§6.1）
    ingest_laws.py
    ingest_judgments.py
    extract.py            # 引用抽取（§6）
    verify.py             # L1（§6.5）
    segment.py            # 判決切段（§7.1）
    retrieve.py           # BM25（§7.2）
    llm.py                # LLMClient 介面與實作（§7.4）
    judge.py              # L2 組合邏輯（§7.3、§7.5）
    report.py             # JSON 與 HTML 輸出（§8）
    evaluate.py           # 評測（§9）
    templates/report.html.j2
  tests/
    fixtures/
    test_normalize.py
    test_extract.py
    test_verify.py
    test_segment.py
    test_judge.py
    test_evaluate.py
  eval/
    testset.jsonl         # 我提供
```

## 5. 資料模型（`models.py`，名稱與值固定）

```python
class CitationType(str, Enum):
    STATUTE = "STATUTE"
    JUDGMENT = "JUDGMENT"

class StatuteStatus(str, Enum):
    FOUND = "FOUND"                         # 法規與條號都查得到
    LAW_NOT_FOUND = "LAW_NOT_FOUND"         # 查無此法規名稱（含別名展開後）
    ARTICLE_NOT_FOUND = "ARTICLE_NOT_FOUND" # 法規存在，查無此條
    ARTICLE_DELETED = "ARTICLE_DELETED"     # 條文內容為「（刪除）」
    UNRESOLVED_REF = "UNRESOLVED_REF"       # 「同法」「同條」找不到前文可承接

class JudgmentStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND_IN_SCOPE = "NOT_FOUND_IN_SCOPE" # 屬範圍內（最高法院民事）但查無 → 顯示「查無公開裁判」
    OUT_OF_SCOPE = "OUT_OF_SCOPE"             # 非最高法院民事 → 顯示「無法查核」
    AMBIGUOUS = "AMBIGUOUS"                   # 同字號對到多筆且無法以判決/裁定區分
    DATA_NOT_COVERED = "DATA_NOT_COVERED"     # 年度不在已匯入資料的涵蓋範圍

class SegmentRole(str, Enum):
    MAIN_TEXT = "MAIN_TEXT"           # 主文
    PARTY_CLAIM = "PARTY_CLAIM"       # 上訴人／被上訴人／原告／被告之主張或抗辯
    LOWER_COURT = "LOWER_COURT"       # 原審（或第一審）之認定與理由
    SUPREME_COURT = "SUPREME_COURT"   # 本院之判斷
    UNKNOWN = "UNKNOWN"

class Relation(str, Enum):             # LLM 只能回這三個值
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    UNRELATED = "UNRELATED"

class L2Label(str, Enum):
    COURT_HOLDING = "COURT_HOLDING"               # 是最高法院見解 → 通過
    PARTY_ARGUMENT = "PARTY_ARGUMENT"             # 只是當事人主張
    LOWER_COURT_VIEW = "LOWER_COURT_VIEW"         # 只是原審見解
    CONTRADICTED = "CONTRADICTED"                 # 最高法院見解與書狀說法相反
    NOT_IN_JUDGMENT = "NOT_IN_JUDGMENT"           # 判決中找不到對應內容
    UNCERTAIN = "UNCERTAIN"                       # 無法判斷，需人工複核
    NOT_RUN = "NOT_RUN"                           # L1 非 FOUND，未執行 L2
```

其他模型：

```python
class Span(BaseModel):
    start: int   # 在「正規化後」書狀文字中的字元位移，含
    end: int     # 不含

class StatuteCitation(BaseModel):
    type: Literal["STATUTE"]
    raw: str                  # 書狀原文片段
    span: Span
    law_name: str | None      # 別名展開後的正式名稱；無法承接時為 None
    article: str | None       # 正規化條號，格式固定為 "184" 或 "15-1"
    paragraph: int | None     # 項
    subparagraph: int | None  # 款
    status: StatuteStatus
    article_text: str | None  # 查到時附上現行條文全文

class JudgmentCitation(BaseModel):
    type: Literal["JUDGMENT"]
    raw: str
    span: Span
    court: str | None         # 正規化法院名稱，如 "最高法院"
    year: int | None          # 民國年
    case_word: str | None     # 字別，如 "台上"
    case_no: int | None
    doc_kind: str | None      # "判決" | "裁定" | None
    status: JudgmentStatus
    jid: str | None           # 查到時的裁判 ID
    claim_sentence: str | None  # §6.4
    l2_label: L2Label
    l2_evidence: L2Evidence | None

class L2Evidence(BaseModel):
    segment_index: int
    segment_role: SegmentRole
    passage: str              # 判決原文，最長 400 字
    relation: Relation
    confidence: float         # 0~1

class Report(BaseModel):
    input_file: str
    generated_at: str         # ISO 8601
    data_coverage: DataCoverage   # 法規資料日期、判決資料涵蓋年月
    statutes: list[StatuteCitation]
    judgments: list[JudgmentCitation]
```

## 6. 抽取與 L1 查核

### 6.1 正規化（`normalize.py`）

對書狀與資料庫欄位套用同一個 `normalize(text)`：

1. 全形數字、全形英文字母 → 半形。
2. `臺` → `台`（只在比對用的欄位做；顯示時保留原文）。
3. 移除數字與「年、度、字、第、號、條、項、款」之間的空白。
4. 中文數字轉阿拉伯數字，**只在引用片段內**做（不要轉整份文件）。必須支援：`一○九`→109、`一百零九`→109、`一百八十四`→184、`十五`→15、`二十`→20、`第一百八十四條之一`→184-1。

`normalize` 不得改變字元數以外的語意；`Span` 一律以正規化後文字為準，報告需保存正規化後全文以供對照。

### 6.2 判決引用抽取規則

必須辨識以下形式（每一種都要有對應的單元測試）：

| # | 書狀寫法 | 預期輸出 |
|---|---|---|
| J1 | `最高法院109年度台上字第123號判決` | court=最高法院, year=109, case_word=台上, case_no=123, doc_kind=判決 |
| J2 | `最高法院 109 年度臺上字第 123 號民事判決` | 同 J1 |
| J3 | `最高法院一○九年度台上字第一二三號判決` | 同 J1 |
| J4 | `最高法院109年台上字第123號`（無「度」、無文書種類） | doc_kind=None，其餘同 J1 |
| J5 | `最高法院109年度台上字第123號、110年度台抗字第45號裁定` | 兩筆，法院皆為最高法院 |
| J6 | `最高法院109年度台上字第123號、第124號判決` | 兩筆，第二筆承接年度與字別 |
| J7 | `同院110年度台上字第5號判決`（前文最近一筆法院為最高法院） | court=最高法院 |
| J8 | `臺灣高等法院109年度上字第10號判決` | 抽取成功，status=OUT_OF_SCOPE |
| J9 | `最高法院109年度台上字第123號刑事判決` | 抽取成功，status=OUT_OF_SCOPE |
| J10 | `最高法院109年度台上大字第2012號裁定` | 抽取成功，status=OUT_OF_SCOPE（大法庭） |

- 字別（`case_word`）的合法清單**不得寫死**：Phase 0 從實際資料統計最高法院民事案件出現過的所有字別，存成 `data/case_words_tps_civil.json`，抽取時用它判斷。
- 判斷「民事」的方式以 Phase 0 在 `DATA_SCHEMA.md` 記錄的結論為準（預期：裁判 ID 的法院代碼部分可區分民刑事）。書狀明寫「刑事」時直接 OUT_OF_SCOPE。
- 抽不完整（缺年度或號數）的片段不輸出為引用，但要記入 `Report` 的 `warnings` 清單。

### 6.3 法條引用抽取規則

| # | 書狀寫法 | 預期輸出 |
|---|---|---|
| S1 | `民法第184條第1項前段` | law=民法, article=184, paragraph=1 |
| S2 | `民法第一百八十四條` | law=民法, article=184 |
| S3 | `民事訴訟法第277條但書` | law=民事訴訟法, article=277 |
| S4 | `公寓大廈管理條例第15條之1` | article=15-1 |
| S5 | `民訴第277條`（簡稱） | law=民事訴訟法（查 `law_aliases.json`） |
| S6 | `民法第184條、第185條` | 兩筆，第二筆承接法規名稱 |
| S7 | `同法第195條`（前文最近一筆法規為民法） | law=民法, article=195 |
| S8 | `同條第2項` | 承接前一筆的法規與條號 |
| S9 | `民法第184條至第186條` | 只輸出起訖兩筆（184、186），不展開中間 |
| S10 | `依第184條規定`（前文無任何法規名稱） | status=UNRESOLVED_REF |

- 法規名稱的辨識方式：以資料庫中所有現行法規名稱 + `law_aliases.json` 的鍵，做**最長匹配**。不要用「以法、條例結尾的任意字串」這種寬鬆規則。
- `law_aliases.json` 初始內容固定如下，不得自行增加（要加先問我）：

```json
{
  "民訴": "民事訴訟法",
  "民訴法": "民事訴訟法",
  "刑訴": "刑事訴訟法",
  "刑訴法": "刑事訴訟法",
  "消保法": "消費者保護法",
  "勞基法": "勞動基準法",
  "個資法": "個人資料保護法",
  "強執法": "強制執行法",
  "家事法": "家事事件法",
  "公寓大廈條例": "公寓大廈管理條例"
}
```

### 6.4 主張句（`claim_sentence`）的取法（固定規則，不用 LLM）

對每一筆判決引用：
1. 以 `。`、`；`、換行切句。
2. 取**包含該引用的那一句**。
3. 如果該句去掉引用片段與「參照、意旨、可資參照、可參、亦同此旨」等字樣後，剩餘不足 15 個字，則再往前併入一句。
4. 最長 300 字，超過從尾端（靠近引用處）保留。
5. 同一句內的多筆引用共用同一個 `claim_sentence`。

### 6.5 L1 查核（`verify.py`）

法條：
1. `law_name` 為 None → `UNRESOLVED_REF`。
2. 查無法規 → `LAW_NOT_FOUND`。
3. 查無條號 → `ARTICLE_NOT_FOUND`。
4. 條文內容經去空白後等於 `（刪除）` 或 `(刪除)` → `ARTICLE_DELETED`。
5. 其餘 → `FOUND`，附 `article_text`。

判決（依序判斷）：
1. 法院不是最高法院，或為刑事、大法庭 → `OUT_OF_SCOPE`。
2. 年度不在已匯入資料的涵蓋範圍（由 ingest 時記錄的最小、最大裁判年月推得）→ `DATA_NOT_COVERED`。
3. 以 (year, case_word, case_no) 查詢：
   - 0 筆 → `NOT_FOUND_IN_SCOPE`
   - 1 筆 → `FOUND`
   - 多筆：若書狀有寫 `doc_kind`，用它篩選；篩完剩 1 筆 → `FOUND`，否則 → `AMBIGUOUS`

## 7. L2：是不是最高法院自己的見解

### 7.1 判決切段（`segment.py`）

目標：把一份最高法院民事裁判全文切成有序段落，每段標上 `SegmentRole`。

做法固定為**規則式**，不使用 LLM：
1. 以「主文」「理由」等標題與段落換行切出段落。
2. 依段落開頭的標記詞判斷角色，並採「**狀態延續**」：沒有標記詞的段落沿用前一段的角色。
3. 起始候選標記詞（Phase 3 必須用真實資料驗證後修訂，結果寫入 `docs/SEGMENT_RULES.md`）：
   - `PARTY_CLAIM`：`上訴人主張`、`被上訴人主張`、`本件上訴人主張`、`被上訴人則以`、`上訴人則以`、`原告主張`、`被告則以`、`再抗告人主張`、`抗告意旨`、`上訴意旨`、`上訴論旨`
   - `LOWER_COURT`：`原審`、`原法院`、`原判決`、`第一審`
   - `SUPREME_COURT`：`本院`、`按`、`惟按`、`惟查`、`查`、`經核`、`據上論結`
4. 特別規則：`上訴意旨`、`上訴論旨` 若出現在 `SUPREME_COURT` 段落的**句中**（例如「上訴論旨，指摘原判決不當，非有理由」），不切換角色。只有出現在段落開頭才切換。
5. 無法判定的段落標 `UNKNOWN`，不得硬猜。

Phase 3 驗證方式：從資料庫隨機抽 20 份最高法院民事**判決**（固定亂數種子 42），輸出切段結果到 `docs/segment_samples/`，由我人工檢查。**我確認前不得進入 Phase 4。**

### 7.2 段落檢索（`retrieve.py`）

- 把每個段落再切成不超過 400 字的 passage（以句號為界，不跨段落），保留其 `segment_index` 與 `segment_role`。
- `MAIN_TEXT` 與 `UNKNOWN` 的 passage 也納入檢索。
- 用 `claim_sentence`（去掉引用片段）對該判決的所有 passage 做 BM25（字元 bigram），取前 **5** 筆。

### 7.3 LLM 判斷

對前 5 筆 passage，**一次呼叫**，輸入主張句與 5 段原文，要求回傳 JSON。

系統提示（逐字使用）：

```
你是法律文件比對助手。你會收到一句「書狀主張」和數段編號的「判決原文段落」。
你的工作只有一件事：判斷每一段原文與書狀主張的關係。
規則：
1. 只能依據提供的段落判斷，不得使用任何你自己的法律知識或記憶。
2. relation 只能是 SUPPORTS、CONTRADICTS、UNRELATED 三者之一。
   - SUPPORTS：該段表達的法律見解或認定，與書狀主張實質相同。
   - CONTRADICTS：該段表達的法律見解或認定，與書狀主張相反。
   - UNRELATED：該段未談到書狀主張的內容，或無法判斷。
3. 不要判斷該段是誰的意見，也不要判斷書狀主張是否正確。
4. confidence 為 0 到 1 的小數。沒有把握時給低分，並選 UNRELATED。
5. 只輸出 JSON，不要任何其他文字。
```

使用者提示格式（逐字使用，變數以實際內容代入）：

```
書狀主張：
{claim_sentence}

判決原文段落：
[1] {passage_1}
[2] {passage_2}
[3] {passage_3}
[4] {passage_4}
[5] {passage_5}

請輸出：
{"results":[{"id":1,"relation":"...","confidence":0.0},{"id":2,...},{"id":3,...},{"id":4,...},{"id":5,...}]}
```

注意：**不把段落角色傳給 LLM**（避免它依角色猜答案）。角色只在 §7.5 的規則裡使用。

### 7.4 `LLMClient`（`llm.py`）

```python
class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...
```

- `AnthropicClient`：temperature 固定 0；max_tokens 固定 600；逾時 60 秒；失敗重試 2 次。
- `MockClient`：建構時傳入固定回應，供單元測試使用。**所有測試不得呼叫真實 API。**
- 回應解析：用 pydantic 驗證 JSON。解析失敗重試 1 次，仍失敗則該筆引用 `l2_label = UNCERTAIN`。
- 需有磁碟快取：以 (model, system, user) 的 SHA-256 為鍵，存於 `data/llm_cache/`，命中時不呼叫 API。

### 7.5 最終標籤的決定規則（`judge.py`，純規則，固定）

設信心門檻 `T = 0.6`（寫成模組常數 `CONFIDENCE_THRESHOLD`）。只看 `confidence >= T` 的結果：

1. 有任一筆 `SUPPORTS` 且角色為 `SUPREME_COURT` → `COURT_HOLDING`
2. 否則，有任一筆 `CONTRADICTS` 且角色為 `SUPREME_COURT` → `CONTRADICTED`
3. 否則，有任一筆 `SUPPORTS` 且角色為 `LOWER_COURT` → `LOWER_COURT_VIEW`
4. 否則，有任一筆 `SUPPORTS` 且角色為 `PARTY_CLAIM` → `PARTY_ARGUMENT`
5. 否則，有任一筆 `SUPPORTS` 或 `CONTRADICTS` 且角色為 `UNKNOWN` 或 `MAIN_TEXT` → `UNCERTAIN`
6. 否則（全部 `UNRELATED` 或信心不足）→ `NOT_IN_JUDGMENT`

`l2_evidence` 取觸發該規則的那一筆（多筆時取 confidence 最高者）；規則 6 時為 None。

L1 狀態不是 `FOUND` 的引用，`l2_label = NOT_RUN`。

## 8. CLI 與輸出

```
citecheck ingest-laws data/raw/laws
citecheck ingest-judgments data/raw/judgments
citecheck stats                         # 印出法規數、條文數、判決數、涵蓋年月
citecheck check <書狀檔> --out <輸出資料夾> [--no-l2]
citecheck eval eval/testset.jsonl --out eval/results [--no-l2]
```

- `check` 產出 `report.json`（即 `Report` 模型）與 `report.html`。
- `report.html`：單一檔案、無外部資源、無 JavaScript 框架。內容依序為：
  1. 摘要表：各狀態的筆數。
  2. 書狀全文，引用處以底色標示：綠（`FOUND` 且 `COURT_HOLDING`，或法條 `FOUND`）、紅（`NOT_FOUND_IN_SCOPE`、`LAW_NOT_FOUND`、`ARTICLE_NOT_FOUND`、`CONTRADICTED`）、橘（`PARTY_ARGUMENT`、`LOWER_COURT_VIEW`、`ARTICLE_DELETED`、`AMBIGUOUS`）、灰（其餘）。
  3. 逐筆明細：原文片段、狀態的中文說明、（法條）現行條文、（判決）主張句與證據段落原文及其角色。
  4. 資料涵蓋範圍與固定頁尾聲明（§0 第 6 點）。
- 狀態的中文說明文字集中定義在 `report.py` 的一個字典，不得散落各處。

## 9. 評測（`evaluate.py`）

測試集格式見另一份文件 `TESTSET_GUIDE.md`。`eval` 指令輸出 `metrics.json` 與 `errors.csv`（每筆答錯的引用一列）。

指標（定義固定）：
- **抽取召回率** = 被抽到的金標引用數 ÷ 金標引用總數。配對方式：同類型且 `Span` 重疊。
- **L1 正確率** = 狀態與金標相同的筆數 ÷ 被配對到的引用數。
- **L2 問題偵測召回率** = 金標為 `PARTY_ARGUMENT`、`LOWER_COURT_VIEW`、`CONTRADICTED`、`NOT_IN_JUDGMENT` 的引用中，系統輸出不是 `COURT_HOLDING` 也不是 `UNCERTAIN` 的比例。
- **L2 誤報率** = 金標為 `COURT_HOLDING` 的引用中，系統輸出為上述四種問題標籤之一的比例。
- **L2 不確定率** = 輸出 `UNCERTAIN` 的比例。
- L2 六類的混淆矩陣。

## 10. 分期與驗收

### Phase 0 — 資料格式確認（不寫功能程式）
1. 列出 `data/raw/laws/` 與 `data/raw/judgments/` 的檔案結構，各讀 3 個樣本。
2. 寫 `docs/DATA_SCHEMA.md`：實際欄位名稱、型別、範例值；判決 ID 的組成；如何辨識「最高法院」與「民事」；條號欄位的實際寫法；刪除條文的實際寫法；是否含已廢止法規及其標示方式。
3. 統計並輸出 `data/case_words_tps_civil.json`。
4. 對照下列「預期」，逐項標示相符或不符：
   - 預期（未驗證）判決為每筆一個 JSON，欄位含 `JID`、`JYEAR`、`JCASE`、`JNO`、`JDATE`、`JTITLE`、`JFULL`。
   - 預期（未驗證）法規為 JSON，含法規名稱、修正日期、條文陣列（條號與條文內容）。
5. **停下來等我確認。** 有任何不符，提出你建議的調整方式，不得直接進 Phase 1。

### Phase 1 — 匯入
- 實作 `ingest-laws`、`ingest-judgments`（只匯入最高法院民事）、`stats`。
- 資料表：`laws(law_name PK, modified_date)`、`articles(law_name, article, text, PK(law_name, article))`、`judgments(jid PK, year, case_word, case_no, doc_kind, date, title, full_text)`，並在 `(year, case_word, case_no)` 建索引；`meta(key PK, value)` 記錄涵蓋年月與法規資料日期。
- 匯入必須可重複執行（重跑不產生重複資料）。
- 驗收：`stats` 的數字與 Phase 0 統計一致；`tests/fixtures/` 用小型假資料測試匯入邏輯。

### Phase 2 — 抽取與 L1
- 實作 `normalize.py`、`extract.py`、`verify.py`、`check --no-l2`、JSON 報告。
- 驗收：§6.1 的每個轉換例、§6.2 的 J1–J10、§6.3 的 S1–S10 各有一個測試且全數通過。

### Phase 3 — 切段
- 實作 `segment.py`，產出 20 份樣本到 `docs/segment_samples/`，寫 `docs/SEGMENT_RULES.md`。
- **停下來等我人工檢查。**

### Phase 4 — L2
- 實作 `retrieve.py`、`llm.py`、`judge.py`，接上 `check`。
- 驗收：§7.5 的六條規則各有一個使用 `MockClient` 的測試；JSON 解析失敗的路徑有測試；快取命中有測試。

### Phase 5 — 報告與評測
- 實作 `report.html`、`evaluate.py`、`eval` 指令。
- 驗收：用 `tests/fixtures/` 的小型測試集跑 `eval --no-l2`，指標計算與手算結果一致。

## 11. 遇到以下情況必須停下來問我

- 實際資料欄位與 `DATA_SCHEMA.md` 或本文件不符。
- 最高法院民事判決在資料中無法與刑事或其他法院可靠區分。
- 切段標記詞在樣本中的適用率明顯偏低（你估計超過三成段落為 `UNKNOWN`）。
- 需要新增套件、別名、狀態值，或更動任何標示「固定」的項目。
- 任何你想用「合理假設」帶過的地方。
