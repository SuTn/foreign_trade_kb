# app/llm/cloud_llm.py
import os
from app.llm.interfaces import LLM
from app.config import settings


class CloudLLM(LLM):
    """云端 LLM。支持 anthropic 与 openai 两种 provider。
    openai 走 OpenAI 兼容接口 (可配 api_base 指向第三方/自建网关)。"""

    def __init__(self, provider=None, model=None, api_base=None, api_key=None):
        self.provider = provider or settings.llm_provider
        self.model = model or settings.llm_model
        self.api_base = api_base or settings.llm_api_base
        self.api_key = api_key or settings.llm_api_key

    def _resolve_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
        else:
            key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "未配置 LLM API key: 请设置 KB_LLM_API_KEY"
                f" (或 {self.provider} 对应的环境变量)")
        return key

    def generate(self, system, user, max_tokens=1024):
        if self.provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(
                api_key=self._resolve_key(),
                base_url=self.api_base,  # None=官方端点
            )
            resp = client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": user}],
            )
            return resp.content[0].text
        else:
            # OpenAI 兼容接口 (官方 / 第三方网关 / 自建)
            import openai
            client = openai.OpenAI(api_key=self._resolve_key(), base_url=self.api_base)
            resp = client.chat.completions.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return resp.choices[0].message.content
