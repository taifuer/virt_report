"""LLM provider 抽象 + 通用 OpenAI 兼容实现 (DeepSeek / GLM 等)。

DeepSeek: https://api.deepseek.com/chat/completions  (OpenAI 兼容)
GLM:      https://open.bigmodel.cn/api/paas/v4/chat/completions
两者请求/响应格式与 OpenAI 一致，故用通用 provider；在 config.yaml 里切
base_url / api_key_env / model 即可在 DeepSeek 与 GLM 之间切换，不改代码。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from virt_report.collectors import base
from virt_report.config import LLMConfig

log = logging.getLogger(__name__)


class LLMProvider:
    name = "base"

    def complete(self, prompt: str, *, system: str | None = None,
                 model: str | None = None, temperature: float = 0.3,
                 max_tokens: int = 2048, json_mode: bool = False) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    """通用 OpenAI 兼容 /chat/completions provider (DeepSeek, GLM, OpenAI 等)。"""

    def __init__(self, base_url: str, api_key: str, default_model: str, name: str = "openai"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.name = name
        self.last_usage: dict[str, int] = {}
        self.last_finish_reason: str | None = None
        self.last_reasoning_chars: int = 0

    def complete(self, prompt: str, *, system: str | None = None,
                 model: str | None = None, temperature: float = 0.4,
                 max_tokens: int = 2048, json_mode: bool = False,
                 thinking: str | None = "enabled",
                 reasoning_effort: str | None = None,
                 retries: int = 3) -> str:
        """调用 /chat/completions。

        thinking: "enabled"/"disabled"/None。DeepSeek v4 思考模式开关；思考模式不支持 temperature。
        reasoning_effort: "high"/"max"。思考强度。
        对 429/5xx 与网络异常做退避重试。
        """
        if not self.api_key:
            raise RuntimeError(f"{self.name} API key not set")
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if thinking:
            body["thinking"] = {"type": thinking}  # 思考模式不支持 temperature
        else:
            body["temperature"] = temperature
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": base.HTTP_UA,
        }
        url = self.base_url + "/chat/completions"
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                # DeepSeek 可能先排队并通过空行保活；读取超时按官方最长等待窗口放宽。
                resp = requests.post(url, headers=headers, json=body, timeout=(30, 660))
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_exc = RuntimeError(f"HTTP {resp.status_code}")
                    if attempt < retries - 1:
                        time.sleep(min(2 ** attempt, 8))
                        continue
                    break
                if resp.status_code >= 400:
                    detail = resp.text[:500]
                    resp.raise_for_status()
                    raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
                data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
                self.last_usage = data.get("usage") or {}
                self.last_finish_reason = choice.get("finish_reason")
                self.last_reasoning_chars = len(message.get("reasoning_content") or "")
                log.info("LLM %s/%s: finish=%s usage=%s reasoning_chars=%d",
                         self.name, body["model"], self.last_finish_reason,
                         self.last_usage, self.last_reasoning_chars)
                return message.get("content") or ""
            except requests.RequestException as e:
                last_exc = e
                status = e.response.status_code if e.response is not None else None
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                break
        raise last_exc if last_exc else RuntimeError("LLM 调用失败")


def get_provider(config: LLMConfig) -> LLMProvider | None:
    """返回配置的 provider；若无 API key 返回 None (调用方走降级)。"""
    if not config.api_key:
        log.warning("LLM provider %s: 未设置 API key (%s)，将使用降级模板摘要",
                    config.provider, config.api_key_env)
        return None
    return OpenAICompatibleProvider(
        config.base_url, config.api_key, config.daily_model, name=config.provider
    )


def extract_json(text: str) -> dict | None:
    """从模型输出中提取 JSON 对象 (容错：可能带 ```json 围栏或多余文本)。"""
    if not text:
        return None
    s = text.strip()
    # 去代码围栏
    if s.startswith("```"):
        s = s.split("```", 2)
        # 取围栏内的内容
        s = s[1] if len(s) >= 2 else text
        if s.startswith("json"):
            s = s[4:]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # 兜底：找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None
