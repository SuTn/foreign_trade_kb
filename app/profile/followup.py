# app/profile/followup.py
"""结构化跟进建议: 基于画像 + 聊天摘要, LLM 输出可执行的跟进建议 (JSON)。"""
import json
import re
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM
from app.profile.service import build_customer_summary

FOLLOWUP_PROMPT = """你是外贸业务跟进助手。基于客户画像与聊天摘要, 生成一条可执行的跟进建议。
只输出 JSON, 不要任何其他文字。JSON 字段:
- priority: "high" | "medium" | "low"  (跟进优先级)
- next_action: 下一步具体动作 (一句话)
- suggested_message: 建议发送给客户的话术 (可直接复制)
- best_time: 最佳跟进时机 (如"今天下午"、"2天后")
- reason: 判断依据 (结合画像/意向/聊天要点, 一句话)

画像: {profile}
聊天摘要: {summary}"""


def _parse_followup(text: str) -> dict:
    """解析 LLM 输出的 JSON; 容错: 先尝试整体解析, 再提取首个 {...} 块, 失败回退为文本。"""
    if not text:
        return {"priority": "medium", "next_action": "", "suggested_message": "",
                "best_time": "", "reason": ""}
    # 先尝试整体解析 (LLM 只输出 JSON 时)
    data = None
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            data = d
    except (ValueError, json.JSONDecodeError):
        pass
    if data is None:
        # 提取首个 JSON 对象块 (非贪婪, 避免跨多个块过度匹配)
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        raw = m.group(0) if m else text
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                data = d
        except (ValueError, json.JSONDecodeError):
            pass
    if data is None:
        # 回退: 整段作为 reason 展示
        return {"priority": "medium", "next_action": "", "suggested_message": "",
                "best_time": "", "reason": text.strip()}
    return {
        "priority": str(data.get("priority", "medium")),
        "next_action": str(data.get("next_action", "")),
        "suggested_message": str(data.get("suggested_message", data.get("suggested", ""))),
        "best_time": str(data.get("best_time", "")),
        "reason": str(data.get("reason", "")),
    }


def generate_followup(store: StructuredStore, llm: LLM, customer_id: str) -> dict:
    """生成结构化跟进建议。基于画像 + 全部会话摘要。"""
    profile = {p.field: p.value for p in store.get_profile(customer_id)}
    summary = build_customer_summary(store, customer_id)
    if not summary:
        return {"priority": "medium", "next_action": "暂无聊天记录, 建议先主动联系客户",
                "suggested_message": "", "best_time": "尽快", "reason": "该客户暂无关联会话消息"}
    text = llm.generate("你是外贸客户跟进助手",
                        FOLLOWUP_PROMPT.format(profile=profile, summary=summary))
    return _parse_followup(text)
