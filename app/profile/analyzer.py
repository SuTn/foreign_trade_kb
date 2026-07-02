# app/profile/analyzer.py
"""客户分析: 基于画像 + 聊天摘要, 给出兴趣点/活跃度/跟进建议。"""
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

ANALYZE_PROMPT = """基于客户画像与聊天摘要, 给出客户分析 (兴趣点/活跃度/跟进建议)。
画像: {profile}
聊天摘要: {summary}"""

def analyze_customer(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> str:
    profile = {p.field: p.value for p in store.get_profile(customer_id)}
    return llm.generate("你是外贸客户分析助手",
                        ANALYZE_PROMPT.format(profile=profile, summary=chat_summary))
