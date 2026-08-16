# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。支持多候选: 通过 style 提示词让 LLM 产出不同表达。
多轮会话: history 为最近 N 轮 [{"role","content"}] 列表, 作为额外 system 上下文 (D4)。
多语种话术 (multilingual-reply-generation): language/scenario/formality 三维度, 缺省与旧版等价。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。

输出格式 (务必严格遵守):
1. 只输出「要直接发给客户的正文」, 不要输出任何额外内容: 不要场景判断、不要标题、不要「业务场景判断:xxx」这类前缀标签、不要报价表头。
2. 纯文本、自然口语, 像真人发 WhatsApp 消息一样; 严禁 Markdown 格式: 不要 **加粗**、不要 - 列表、不要 # 标题、不要编号、不要星号。
3. 简洁直接、专业得体; 一般一段话或几行短句即可, 不要长篇大论。
4. 确需分点说明时 (如报价), 用普通换行的短句表达 (例如「长续航版510km：21300美元/台」), 不要用任何列表符号。

{style}{language}{scenario}{formality}{terms}"""

REPLY_STYLE_VARIANTS = {
    "default": "",
    "concise": "语气简洁、直接，控制在三句话以内。",
    "warm": "语气热情友好，主动表达对客户需求的重视。",
    "formal": "语气正式严谨，突出专业与条理。",
}

LANGUAGES = {
    "zh": "用简体中文回复。",
    "en": "用英语回复。",
    "ru": "用俄语回复。",
}

SCENARIO_LIST = ["询价", "砍价", "看车", "物流", "付款", "售后"]

SCENARIOS = {
    "auto": "（内部自行判断，切勿在回复中写出）先判断本条消息所属业务场景（询价/砍价/看车/物流/付款/售后），按该场景组织回复；无法判断时按通用场景。",
    "inquiry": "本条消息属于询价场景，突出车型信息与价格。",
    "bargain": "本条消息属于砍价场景，强调产品价值与让步空间。",
    "inspection": "本条消息属于看车场景，突出车况与看车安排。",
    "logistics": "本条消息属于物流场景，说明运输方式与交期。",
    "payment": "本条消息属于付款场景，说明付款方式与交易安全。",
    "after_sale": "本条消息属于售后场景，安抚客户并说明质保与处理流程。",
}

FORMALITY = {
    "casual": "",
    "formal": "使用正式书面语气，措辞严谨。",
}

TERMS = "汽车外贸术语: 车架号VIN, 排量, 手续齐全, 报关单, 关税, 运输时间, 付款方式(定金/尾款), 质保, 出港, 到港。话术中使用标准贸易术语, 术语表达要准确。"

NEXT_STYLE = {"default": "concise", "concise": "warm", "warm": "formal", "formal": "default"}


def _build_system(style_instruction: str, history: list[dict] | None,
                  language: str = "zh", scenario: str = "auto", formality: str = "casual",
                  recent_chat: str | None = None) -> str:
    base = REPLY_SYSTEM.format(
        style=style_instruction,
        language=LANGUAGES.get(language, LANGUAGES["zh"]),
        scenario=SCENARIOS.get(scenario, SCENARIOS["auto"]),
        formality=FORMALITY.get(formality, ""),
        terms=TERMS,
    )
    # 最近聊天记录: 让 AI 结合完整对话上下文, 而非只盯最后一句话
    if recent_chat:
        base = f"{base}\n\n最近聊天记录:\n{recent_chat}"
    if history:
        lines = "\n".join(f"{h['role']}: {h['content']}" for h in history)
        base = f"{base}\n\n本次会话最近对话历史:\n{lines}"
    return base


def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str, style: str = "default",
                   language: str = "zh", scenario: str = "auto", formality: str = "casual",
                   history: list[dict] | None = None,
                   recent_chat: str | None = None) -> dict:
    """返回 {reply, sources, style, language, scenario, formality}。不发送。
    language: zh|en|ru; scenario: auto|inquiry|bargain|inspection|logistics|payment|after_sale;
    formality: casual|formal。缺省与旧版行为一致。
    recent_chat: 最近聊天记录文本 (可选), 供 AI 结合完整对话上下文。"""
    style_instruction = REPLY_STYLE_VARIANTS.get(style, "")
    system = _build_system(style_instruction, history, language, scenario, formality,
                           recent_chat)
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=system)
    return {"reply": result["answer"], "sources": result["sources"], "style": style,
            "language": language, "scenario": scenario, "formality": formality}


def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str, previous_style: str = "default",
                     language: str = "zh", scenario: str = "auto", formality: str = "casual",
                     history: list[dict] | None = None,
                     recent_chat: str | None = None) -> dict:
    """重新生成获得不同候选 (切换表达风格 + LLM 温度自然差异); 保留语种/场景/语气 (D3)。"""
    next_style = NEXT_STYLE.get(previous_style, "default")
    return generate_reply(pipeline, customer_id, chat_id, incoming_message,
                          style=next_style, language=language, scenario=scenario,
                          formality=formality, history=history, recent_chat=recent_chat)
