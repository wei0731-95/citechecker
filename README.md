# citecheck
書狀引用查核器。規格見 SPEC.md，測試集指南見 docs/TESTSET_GUIDE.md。

## 開發環境（Python 3.11 in Docker）
docker build -t citecheck .
docker run --rm -it -v "%cd%":/app citecheck bash
