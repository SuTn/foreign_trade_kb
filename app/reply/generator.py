# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。
要求: 专业、得体、可直接复制发送。给出 1 个主回复。"""

def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str) -> dict:
    """返回 {reply, sources}。不发送。"""
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=REPLY_SYSTEM)
    return {"reply": result["answer"], "sources": result["sources"]}

def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str) -> dict:
    """重新生成获得不同候选 (LLM 温度自然产生差异)。"""
    return generate_reply(pipeline, customer_id, chat_id, incoming_message)
