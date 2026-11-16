import pytest


@pytest.fixture(autouse=True)
def _failure_log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("CITECHECK_FAILURE_LOG", str(tmp_path / "llm_failures.log"))
