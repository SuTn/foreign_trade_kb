# app/llm/cloud_llm.py
import os
from app.llm.interfaces import LLM
from app.config import settings

class CloudLLM(LLM):
    def __init__(self, provider=None, model=None):
        self.provider = provider or settings.llm_provider
        self.model = model or settings.llm_model

    def generate(self, system, user, max_tokens=1024):
        if self.provider == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            resp = client.messages.create(model=self.model, max_tokens=max_tokens,
                                          system=system, messages=[{"role": "user", "content": user}])
            return resp.content[0].text
        else:
            import openai
            client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
            resp = client.chat.completions.create(model=self.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return resp.choices[0].message.content
