# citecheck
書狀引用查核器：檢查書狀中引用的法條與最高法院民事裁判，在資料庫裡查不查得到，並（選用）用 LLM 比對書狀的說法是不是最高法院自己的見解。

**本工具僅提供引用查核輔助，不構成法律意見，也不能取代律師。** 標示為綠色的引用仍請自行點連結核對原文。評測數字都是 AI 標註、未經人工複核。

規格見 SPEC.md、SPEC_V2.md；測試集指南見 docs/TESTSET_GUIDE.md；進度與已知問題見 docs/PROGRESS.md。

## 它檢查什麼
- **L1（不需要 API）**：法條的法規與條號是否存在、是否已廢止或尚未施行；判決字號是否在資料庫中（資料來源：全國法規資料庫、司法院裁判書開放資料）。
- **L2（需要 API）**：判決中最相關的段落是不是最高法院自己的見解（而非原審見解或當事人主張）；民法條文與書狀敘述是否相符；找不到時列出論述相近的裁判供自行查閱。

範本書狀在 samples/，其中 01–04 只靠資料庫就能驗證，05 需要 L2。

## 開發環境（Python 3.11 in Docker）
```
docker build -t citecheck .
docker run --rm -v "${PWD}:/app" -w /app citecheck pytest -q
```

## 準備資料
```
citecheck ingest-laws data/raw/laws
citecheck ingest-judgments data/raw/judgments
citecheck build-index
citecheck stats
```

## 命令列查核
```
citecheck check 書狀.txt --out out/r1 --no-l2     # 只查法條與判決是否查得到
citecheck check 書狀.txt --out out/r1             # 含內容比對（需要 API 金鑰）
```
含內容比對需要環境變數：`CITECHECK_MODEL`，以及 `ANTHROPIC_API_KEY`（預設）或 `CITECHECK_PROVIDER=openai` 搭配 `OPENAI_API_KEY`。
接本機或其他相容 OpenAI 的伺服器（例如 Ollama）：另設 `CITECHECK_OPENAI_BASE_URL`（完整的 chat/completions 網址），必要時 `CITECHECK_TIMEOUT`（秒）。在 Docker 內連本機 Ollama 用 `http://host.docker.internal:11434/v1/chat/completions`。
每次查核最多送出 120 次 API 呼叫；回應快取在 `data/llm_cache`。

## 本機網頁
```
citecheck serve --port 8000
```
開啟 http://127.0.0.1:8000/ ，貼上書狀文字即可。網頁只綁定本機位址，貼上的文字不會寫入磁碟，伺服器也不記錄請求內容。

在 Docker 內執行時，伺服器必須綁定容器的所有位址，並且只把埠映射到本機：
```
docker run --rm -p 127.0.0.1:8000:8000 -v "${PWD}:/app" -w /app citecheck citecheck serve --host 0.0.0.0 --port 8000
```
勾選「同時比對內容是否相符」時，含有引用的句子會被傳送到外部 AI 服務；請先自行刪除個人資料。

## 資料與隱私
- `data/raw/`、`data/*.db`、`data/llm_cache/` 不進版本控制，需自行下載與匯入。
- 程式只在 LLM 呼叫時對外連線；API 金鑰只從環境變數讀取。

## 授權
MIT，見 LICENSE。
