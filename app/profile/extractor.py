# app/profile/extractor.py
"""LLM 画像抽取: 从聊天摘要抽取字段, 单行覆盖语义 (遇 manual 跳过)。"""
import json
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

EXTRACT_PROMPT = """从以下客户聊天摘要抽取画像字段, 输出 JSON 对象 {{field: value}}。
字段: company, country, product_interest, inquiry_history, communication_preference, language, deal_stage
摘要: {summary}"""

def extract_profile(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> dict:
    resp = llm.generate("你是外贸客户画像抽取助手", EXTRACT_PROMPT.format(summary=chat_summary))
    try:
        fields = json.loads(resp)
    except Exception:
        return {}
    for field, value in fields.items():
        store.upsert_profile_field(customer_id, field, str(value), "auto")  # 遇 manual 自动跳过
    return fields
