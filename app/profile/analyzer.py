# app/profile/analyzer.py
"""客户分析: 基于画像 + 聊天摘要, 给出兴趣点/活跃度/跟进建议 (结构化 JSON)。"""
import json
import re
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM

ANALYZE_PROMPT = """你是外贸客户分析助手。基于客户画像与聊天摘要, 给出客户分析。
只输出 JSON, 不要任何其他文字。JSON 字段:
- interests: 客户兴趣点 (如"LED 灯, 大功率", 逗号分隔)
- activity: 活跃度 (高/中/低)
- followup: 跟进建议 (一句话)
- summary: 一句话总结该客户

画像: {profile}
聊天摘要: {summary}"""


def _parse_analysis(text: str) -> dict:
    """解析 LLM 输出的 JSON; 容错: 先整体解析, 再提取首个 {...} 块, 失败回退为文本。"""
    if not text:
        return {"interests": "", "activity": "", "followup": "", "summary": ""}
    data = None
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            data = d
    except (ValueError, json.JSONDecodeError):
        pass
    if data is None:
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(0))
                if isinstance(d, dict):
                    data = d
            except (ValueError, json.JSONDecodeError):
                pass
    if data is None:
        # 回退: 整段作为 summary 展示
        return {"interests": "", "activity": "", "followup": "", "summary": text.strip()}
    return {
        "interests": str(data.get("interests", data.get("interest", ""))),
        "activity": str(data.get("activity", "")),
        "followup": str(data.get("followup", data.get("followup_suggestion", ""))),
        "summary": str(data.get("summary", "")),
    }


def analyze_customer(store: StructuredStore, llm: LLM, customer_id: str, chat_summary: str) -> dict:
    profile = {p.field: p.value for p in store.get_profile(customer_id)}
    text = llm.generate("你是外贸客户分析助手",
                        ANALYZE_PROMPT.format(profile=profile, summary=chat_summary),
                        max_tokens=1024)
    return _parse_analysis(text)
