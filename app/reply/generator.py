# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。支持多候选: 通过 style 提示词让 LLM 产出不同表达。
多轮会话: history 为最近 N 轮 [{"role","content"}] 列表, 作为额外 system 上下文 (D4)。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。
要求: 专业、得体、可直接复制发送。给出一条主回复。{style}"""

REPLY_STYLE_VARIANTS = {
    "default": "",
    "concise": "语气简洁、直接，控制在三句话以内。",
    "warm": "语气热情友好，主动表达对客户需求的重视。",
    "formal": "语气正式严谨，突出专业与条理。",
}

NEXT_STYLE = {"default": "concise", "concise": "warm", "warm": "formal", "formal": "default"}


def _build_system(style_instruction: str, history: list[dict] | None) -> str:
    base = REPLY_SYSTEM.format(style=style_instruction)
    if history:
        lines = "\n".join(f"{h['role']}: {h['content']}" for h in history)
        base = f"{base}\n\n本次会话最近对话历史:\n{lines}"
    return base


def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str, style: str = "default",
                   history: list[dict] | None = None) -> dict:
    """返回 {reply, sources}。不发送。style 决定候选表达风格, history 提供会话上下文。"""
    style_instruction = REPLY_STYLE_VARIANTS.get(style, "")
    system = _build_system(style_instruction, history)
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=system)
    return {"reply": result["answer"], "sources": result["sources"], "style": style}


def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str, previous_style: str = "default",
                     history: list[dict] | None = None) -> dict:
    """重新生成获得不同候选 (切换表达风格 + LLM 温度自然差异)。"""
    next_style = NEXT_STYLE.get(previous_style, "default")
    return generate_reply(pipeline, customer_id, chat_id, incoming_message,
                          style=next_style, history=history)
