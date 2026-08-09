# app/profile/service.py
"""画像/分析编排: 从聊天构建摘要 → 触发 LLM 抽取/分析。"""
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM
from app.config import settings
from app.profile.extractor import extract_profile
from app.profile.analyzer import analyze_customer

def list_customer_chat_ids(store: StructuredStore, customer_id: str) -> list[str]:
    try:
        rows = store.conn.execute(
            "SELECT chat_id FROM customer_chat_map WHERE customer_id=?", (customer_id,)).fetchall()
        return [r["chat_id"] for r in rows]
    except Exception:
        return []

def build_chat_summary(store: StructuredStore, chat_id: str, limit: int | None = None) -> str:
    """把某会话近期消息格式化为 我/客户 对话摘要 (时间正序)。"""
    limit = limit or settings.profile_summary_messages
    lines = []
    for m in reversed(store.list_messages(chat_id, limit=limit)):
        body = (m.body or "").strip()
        if body:
            lines.append(f"{'我' if m.from_me else '客户'}: {body}")
    return "\n".join(lines)

def build_customer_summary(store: StructuredStore, customer_id: str,
                           limit: int | None = None) -> str:
    """汇总该客户全部关联会话的近期聊天。"""
    parts = []
    for chat_id in list_customer_chat_ids(store, customer_id):
        s = build_chat_summary(store, chat_id, limit)
        if s:
            parts.append(f"[会话 {chat_id}]\n{s}")
    return "\n\n".join(parts)

def refresh_customer_profile(store: StructuredStore, llm: LLM, customer_id: str,
                             chat_id: str | None = None) -> dict:
    """自动/按需抽取画像。chat_id 缺省时用该客户全部会话摘要。返回抽取的字段。"""
    summary = (build_chat_summary(store, chat_id) if chat_id
               else build_customer_summary(store, customer_id))
    if not summary:
        return {}
    return extract_profile(store, llm, customer_id, summary)

def analyze_customer_full(store: StructuredStore, llm: LLM, customer_id: str) -> str:
    """基于该客户画像 + 全部会话摘要生成客户分析 (兴趣点/活跃度/跟进建议)。"""
    return analyze_customer(store, llm, customer_id, build_customer_summary(store, customer_id))
