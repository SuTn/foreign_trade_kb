# app/profile/summarizer.py
"""历史对话智能摘要 (customer-summary): 按客户聚合, LLM 结构化输出核心信息。
增量设计: customer_summaries 表记录 last_ts 游标 (已处理到的最大消息 ts)。
每次生成时, 取 ts > last_ts 的新消息 + 旧摘要一起喂给 LLM 合并, 更新摘要与游标。
首次 (无旧摘要/游标=0) 退化为全量最近 N 条/会话, 兼容旧行为。"""
import json
import re

from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM
from app.profile.service import build_customer_summary, list_customer_chat_ids, _chat_kind

SUMMARIZE_PROMPT = """你是外贸客户对话摘要助手。根据客户聊天摘要, 提炼核心信息, 输出 JSON 对象。

字段:
- overview: 一段话概述客户情况 (身份/需求/沟通进展)
- intent_vehicle: 意向车型 (无则空字符串)
- budget_range: 预算区间 (无则空字符串)
- target_country: 目标国家/市场 (无则空字符串)
- concerns: 核心顾虑 (如车况/物流/付款/质保, 无则空字符串)
- follow_up: 待跟进事项 (无则空字符串)

只输出 JSON 对象, 格式: {{"overview": "...", "intent_vehicle": "...", "budget_range": "...", "target_country": "...", "concerns": "...", "follow_up": "..."}}
聊天摘要: {summary}"""

# 增量合并 prompt: 旧摘要 + 新消息 → 新摘要。要求保留仍有效的旧信息, 更新变化的信息。
INCREMENTAL_PROMPT = """你是外贸客户对话摘要助手。这是对客户摘要的增量更新。

已有摘要 (可能过时, 请判断哪些仍有效):
{old_summary}

自上次摘要以来的新消息:
{new_messages}

请基于新消息更新摘要, 保留仍有效的旧信息, 更新已变化的信息, 补充新信息。输出 JSON 对象:
{{"overview": "...", "intent_vehicle": "...", "budget_range": "...", "target_country": "...", "concerns": "...", "follow_up": "..."}}"""


def _parse_result(resp: str) -> dict:
    """解析 LLM 输出; 失败返回空 dict (回退无摘要)。容错围栏 JSON / 首个 {..} 子串。"""
    data = None
    try:
        data = json.loads(resp)
    except Exception:
        data = None
    if not isinstance(data, dict):
        m = re.search(r"\{.*\}", resp or "", re.DOTALL)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
    if not isinstance(data, dict):
        return {}
    keys = ("overview", "intent_vehicle", "budget_range", "target_country", "concerns", "follow_up")
    out = {}
    for k in keys:
        v = data.get(k)
        out[k] = str(v).strip() if v is not None else ""
    return out


def _format_messages(store: StructuredStore, chat_id: str, msgs) -> str:
    """把消息列表格式化为对话文本 (群聊按发送者标注, 单聊保持 我/客户)。"""
    kind = _chat_kind(store, chat_id)
    lines = []
    for m in msgs:
        body = (m.body or "").strip()
        if not body:
            continue
        if kind == "group":
            who = "我" if m.from_me else (m.sender_name or "未知")
        else:
            who = "我" if m.from_me else "客户"
        lines.append(f"{who}: {body}")
    return "\n".join(lines)


def _build_incremental_input(store: StructuredStore, customer_id: str,
                             last_ts: int) -> tuple[str, int]:
    """构建增量输入: 返回 (prompt 文本, 新的 last_ts)。
    last_ts>0 时取各会话 ts>last_ts 的新消息; 无新消息返回空。
    首次 (last_ts==0) 退化为全量最近 N 条/会话, 并返回当前最大消息 ts 作为新游标。"""
    chat_ids = list_customer_chat_ids(store, customer_id)
    if last_ts > 0:
        parts = []
        new_last = last_ts
        for chat_id in chat_ids:
            msgs = store.list_messages_after(chat_id, last_ts)
            if msgs:
                parts.append(f"[会话 {chat_id}]\n{_format_messages(store, chat_id, msgs)}")
                new_last = max(new_last, max(m.ts for m in msgs))
        return "\n\n".join(parts), new_last
    # 首次: 全量最近 N 条/会话, 游标推进到当前最大消息 ts
    text = build_customer_summary(store, customer_id)
    max_ts = 0
    for chat_id in chat_ids:
        msgs = store.list_messages(chat_id, limit=1)
        if msgs:
            max_ts = max(max_ts, msgs[0].ts)
    return text, max_ts


def summarize_customer(store: StructuredStore, llm: LLM, customer_id: str) -> dict:
    """对单个客户生成/增量更新结构化摘要并写入 customer_summaries 表。
    无聊天数据或解析失败返回空 dict, 不抛异常。"""
    last_ts = store.get_customer_summary_last_ts(customer_id)
    old = store.get_customer_summary(customer_id)
    new_text, new_last = _build_incremental_input(store, customer_id, last_ts)
    if not new_text:
        return {}  # 无新消息 (增量) 或无聊天数据 (首次)
    if last_ts > 0 and old:
        # 增量合并: 旧摘要 + 新消息
        old_summary = _format_old_summary(old)
        resp = llm.generate("你是外贸客户对话摘要助手",
                            INCREMENTAL_PROMPT.format(old_summary=old_summary, new_messages=new_text),
                            max_tokens=2048)
    else:
        resp = llm.generate("你是外贸客户对话摘要助手",
                            SUMMARIZE_PROMPT.format(summary=new_text),
                            max_tokens=2048)
    result = _parse_result(resp)
    if not result:
        return {}
    store.upsert_customer_summary(customer_id, result, last_ts=new_last)
    return result


def _format_old_summary(old: dict) -> str:
    """把旧摘要格式化为文本供增量 prompt 使用。"""
    labels = {"overview": "概述", "intent_vehicle": "意向车型", "budget_range": "预算区间",
              "target_country": "目标国家", "concerns": "核心顾虑", "follow_up": "待跟进事项"}
    lines = []
    for k, label in labels.items():
        v = (old.get(k) or "").strip()
        if v:
            lines.append(f"{label}: {v}")
    return "\n".join(lines) if lines else "(无)"


def get_customer_summary(store: StructuredStore, customer_id: str) -> dict | None:
    """读取已生成的客户摘要; 无则返回 None。"""
    return store.get_customer_summary(customer_id)
