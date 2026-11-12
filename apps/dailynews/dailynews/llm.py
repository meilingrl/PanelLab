"""OpenAI 兼容的 Chat Completions 客户端（默认 DeepSeek），带每日调用上限与有限重试。"""
import json
import time
from datetime import date

from . import http
from .settings import LLMSettings
from .store import Store

RETRY_DELAYS = (2, 6)  # 429/5xx/超时的重试间隔（秒），最多重试 2 次
BAD_OUTPUT_RETRIES = 1


class LLMError(Exception):
    pass


class BudgetExceeded(LLMError):
    pass


class LLM:
    def __init__(self, settings: LLMSettings, api_key: str, store: Store, today: date,
                 sleep=time.sleep):
        self.settings = settings
        self._api_key = api_key
        self.store = store
        self.today = today
        self._sleep = sleep

    def _request(self, system, user, max_tokens):
        """发一次请求（先查并占用额度），返回 message.content 文本；传输层错误抛 LLMError(带 retryable)。"""
        if self.store.llm_calls_today(self.today) >= self.settings.max_calls_per_day:
            raise BudgetExceeded("今日大模型调用已达上限（%d 次）" % self.settings.max_calls_per_day)
        self.store.count_llm_call(self.today)
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": "Bearer " + self._api_key}
        try:
            out = http.post_json(url, payload, headers, self.settings.timeout)
        except http.FetchError as e:
            status = e.status
            if status is not None and 200 <= status < 300:
                return None  # 2xx 但内容不是 JSON 对象：按坏输出处理，不当作传输错误
            retryable = status is None or status == 429 or status >= 500
            err = LLMError("大模型请求失败：HTTP %d" % status if status else "大模型请求失败：连接或超时")
            err.retryable = retryable
            raise err
        try:
            content = out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        return content if isinstance(content, str) else None

    def chat_json(self, system: str, user: str, *, max_tokens: int = 2000) -> dict:
        transport_retries = 0
        bad_retries = 0
        while True:
            try:
                content = self._request(system, user, max_tokens)
            except BudgetExceeded:
                raise
            except LLMError as e:
                if not getattr(e, "retryable", False) or transport_retries >= len(RETRY_DELAYS):
                    raise
                self._sleep(RETRY_DELAYS[transport_retries])
                transport_retries += 1
                continue
            result = None
            if content is not None:
                try:
                    result = json.loads(content)
                except ValueError:
                    result = None
            if isinstance(result, dict):
                return result
            if bad_retries >= BAD_OUTPUT_RETRIES:
                raise LLMError("大模型返回的不是 JSON 对象")
            bad_retries += 1
