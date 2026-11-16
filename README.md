# citecheck
書狀引用查核器。第一階段規格見 SPEC.md，第二階段見 SPEC_V2.md，測試集指南見 docs/TESTSET_GUIDE.md，進度見 docs/PROGRESS.md。

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
