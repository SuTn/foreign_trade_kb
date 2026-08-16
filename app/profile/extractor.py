# app/profile/extractor.py
"""LLM 画像抽取: 从聊天摘要抽取字段, 单行覆盖语义 (遇 manual 跳过)。"""
import json
import re
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

EXTRACT_PROMPT = """从以下客户聊天摘要抽取画像字段, 输出 JSON 对象 {{field: value}}。
字段: company, country, product_interest, inquiry_history, communication_preference, language, deal_stage
摘要: {summary}"""


def _parse_fields(resp: str) -> dict:
    """解析 LLM 输出 JSON; 容错: 先整体解析, 再提取首个 {..} 块, 失败返回空。"""
    data = None
    try:
        data = json.loads(resp)
    except Exception:
        data = None
    if not isinstance(data, dict):
        m = re.search(r"\{.*?\}", resp or "", re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
    return data if isinstance(data, dict) else {}


def extract_profile(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> dict:
    resp = llm.generate("你是外贸客户画像抽取助手", EXTRACT_PROMPT.format(summary=chat_summary),
                        max_tokens=1024)
    fields = _parse_fields(resp)
    for field, value in fields.items():
        store.upsert_profile_field(customer_id, field, str(value), "auto")  # 遇 manual 自动跳过
        store.sync_customer_column(customer_id, field, str(value))  # G: company/country 同步到 customers 列
    return fields
