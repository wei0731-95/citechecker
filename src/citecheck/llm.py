"""LLM access (SPEC 7.4). Model and key come only from environment variables."""
import hashlib
import os
import threading
from pathlib import Path
from typing import Protocol

DEFAULT_CACHE_DIR = Path("data/llm_cache")


class LLMConfigError(RuntimeError):
    pass


class LLMClient(Protocol):
    def complete(self, system: str, user: str, max_tokens: int = 600) -> str: ...


class AnthropicClient:
    """temperature 0, max_tokens 600, 60 s timeout, 2 retries (SDK-level)."""

    def __init__(self, model: str, api_key: str):
        import anthropic
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=2)

    @classmethod
    def from_env(cls) -> "AnthropicClient":
        model = os.environ.get("CITECHECK_MODEL")
        key = os.environ.get("ANTHROPIC_API_KEY")
        missing = [n for n, v in (("CITECHECK_MODEL", model), ("ANTHROPIC_API_KEY", key)) if not v]
        if missing:
            raise LLMConfigError("未設定環境變數：" + "、".join(missing))
        return cls(model, key)

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=0, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


class MockClient:
    """Returns fixed responses (a str, or a list consumed in order; the last one repeats)."""

    def __init__(self, responses: str | list[str]):
        self.responses = [responses] if isinstance(responses, str) else list(responses)
        self.calls: list[tuple[str, str]] = []
        self.max_tokens: list[int] = []
        self.cache_hits = 0

    @property
    def api_calls(self) -> int:
        return len(self.calls)

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        self.calls.append((system, user))
        self.max_tokens.append(max_tokens)
        i = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[i]


class CachingClient:
    """Disk cache keyed by SHA-256 of (model, system, user); a hit never calls the inner client.

    max_tokens is not part of the key. api_calls / cache_hits are thread-safe counters.
    """

    def __init__(self, inner: LLMClient, model: str, cache_dir: Path = DEFAULT_CACHE_DIR):
        self.inner, self.model, self.dir = inner, model, Path(cache_dir)
        self.api_calls = 0
        self.cache_hits = 0
        self._lock = threading.Lock()

    def _path(self, system: str, user: str) -> Path:
        h = hashlib.sha256("\x00".join((self.model, system, user)).encode("utf-8")).hexdigest()
        return self.dir / f"{h}.txt"

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        p = self._path(system, user)
        if p.exists():
            with self._lock:
                self.cache_hits += 1
            return p.read_text(encoding="utf-8")
        with self._lock:
            self.api_calls += 1
        out = self.inner.complete(system, user, max_tokens=max_tokens)
        self.dir.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        return out

    def forget(self, system: str, user: str) -> None:
        """Drop a cached response (used when it failed to parse, so a retry reaches the API)."""
        self._path(system, user).unlink(missing_ok=True)


class OpenAIClient:
    """OpenAI Chat Completions over plain HTTPS (stdlib only; no extra dependency).

    Mirrors AnthropicClient: temperature 0, 600 output tokens, 60 s timeout, 2 retries.
    To keep reasoning models from spending the 600-token budget on thinking, reasoning_effort is
    sent (default "low"; CITECHECK_REASONING_EFFORT overrides, "off" omits it).
    If the API rejects temperature or reasoning_effort for the chosen model, that parameter is
    dropped, a warning is printed and the call is repeated (deviation from the fixed settings).
    """

    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str, api_key: str, url: str | None = None):
        self.model, self._key, self._url = model, api_key, url or self.URL
        effort = os.environ.get("CITECHECK_REASONING_EFFORT", "low").strip().lower()
        self._optional: dict = {"temperature": 0}
        if effort and effort != "off":
            self._optional["reasoning_effort"] = effort

    @classmethod
    def from_env(cls) -> "OpenAIClient":
        model = os.environ.get("CITECHECK_MODEL")
        key = os.environ.get("OPENAI_API_KEY")
        missing = [n for n, v in (("CITECHECK_MODEL", model), ("OPENAI_API_KEY", key)) if not v]
        if missing:
            raise LLMConfigError("未設定環境變數：" + "、".join(missing))
        return cls(model, key)

    def _post(self, system: str, user: str, max_tokens: int = 600) -> str:
        import json
        import urllib.error
        import urllib.request

        body = {"model": self.model, "max_completion_tokens": max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        body.update(self._optional)
        req = urllib.request.Request(
            self._url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if not content:
            import sys
            u = data.get("usage", {}).get("completion_tokens_details", {}) or {}
            print(f"警告：模型回傳空內容（finish_reason={choice.get('finish_reason')}，"
                  f"reasoning_tokens={u.get('reasoning_tokens')}）。若為 length，代表 600 token 上限"
                  "被思考過程用完。", file=sys.stderr)
        return content

    def complete(self, system: str, user: str, max_tokens: int = 600) -> str:
        import sys
        import time
        import urllib.error

        last: Exception | None = None
        for attempt in range(3):  # first try + 2 retries
            try:
                return self._post(system, user, max_tokens)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                rejected = [k for k in self._optional if k in detail]
                if e.code == 400 and rejected:
                    for k in rejected:
                        print(f"警告：此模型不接受 {k}，已省略該參數重試。", file=sys.stderr)
                        del self._optional[k]
                    continue
                if e.code in (401, 403, 404):
                    raise LLMConfigError(f"OpenAI API 回應 {e.code}：{detail[:200]}") from e
                if e.code == 400:
                    raise LLMConfigError(f"OpenAI API 拒絕請求：{detail[:200]}") from e
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(2 ** attempt)
        raise RuntimeError(f"OpenAI API 呼叫失敗：{last}")


def client_from_env():
    """CITECHECK_PROVIDER = anthropic (default) | openai."""
    provider = os.environ.get("CITECHECK_PROVIDER", "anthropic").strip().lower()
    if provider == "openai":
        return OpenAIClient.from_env()
    if provider == "anthropic":
        return AnthropicClient.from_env()
    raise LLMConfigError(f"CITECHECK_PROVIDER 只能是 anthropic 或 openai，目前為：{provider}")
