# app/profile/tiering.py
"""客户意向分层: 复用摘要构建 → LLM 输出 A/B/C/D 等级 + 标签 → 写 profiles + 历史表。"""
import json
import re
from app.storage.interfaces import StructuredStore
from app.llm.interfaces import LLM
from app.profile.service import build_customer_summary

PREDEFINED_TAGS = ["已购", "意向车型", "议价中", "待跟进", "需回访", "沉睡", "垃圾询盘"]

TIER_PROMPT = """你是外贸客户意向分层助手。根据客户聊天摘要, 判定意向等级并生成业务标签。

等级规则:
- A 类 (高意向): 明确确认车型 / 议价 / 索要单证 / 约定看车 / 谈付款
- B 类 (中意向): 详细询价 / 多次沟通 / 询问物流交期
- C 类 (低意向): 一般询价 / 简单咨询
- D 类 (无效/沉睡): 垃圾询盘 / 长期无回复

标签: 从预定义集 [{tags}] 中选择, 可补充自定义标签, 用逗号分隔。

只输出 JSON 对象, 格式: {{"intent_level": "A|B|C|D", "tags": "标签1,标签2"}}
聊天摘要: {summary}"""


def _parse_result(resp: str) -> dict:
    """解析 LLM 输出; 失败返回空 dict (回退未分层)。

    容错: 围栏 JSON (```json ... ```) 提取首个 {..} 子串解析; tags 为列表时 join 为逗号字符串。
    """
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
    level = str(data.get("intent_level", "")).strip().upper()
    if level not in ("A", "B", "C", "D"):
        return {}
    raw_tags = data.get("tags", "")
    if isinstance(raw_tags, list):
        tags = ",".join(str(t).strip() for t in raw_tags if str(t).strip())
    else:
        tags = str(raw_tags).strip()
    tags = re.sub(r"\s*,\s*", ",", tags)
    return {"intent_level": level, "tags": tags}


def tier_customer(store: StructuredStore, llm: LLM, customer_id: str) -> dict:
    """对单个客户分层: 摘要 → LLM → 写 profiles(auto) + 历史(auto)。
    无聊天数据或解析失败回退未分层, 不阻塞其他客户。"""
    summary = build_customer_summary(store, customer_id)
    if not summary:
        return {"intent_level": "", "tags": ""}
    resp = llm.generate("你是外贸客户意向分层助手",
                        TIER_PROMPT.format(tags=",".join(PREDEFINED_TAGS), summary=summary))
    result = _parse_result(resp)
    if not result:
        return {"intent_level": "", "tags": ""}
    store.upsert_profile_field(customer_id, "intent_level", result["intent_level"], "auto")
    store.upsert_profile_field(customer_id, "tags", result["tags"], "auto")
    store.add_tier_history(customer_id, result["intent_level"], result["tags"], "auto")
    return result


def tier_customers(store: StructuredStore, llm: LLM, customer_ids: list[str]) -> dict:
    """批量分层入口。返回 {tiered, untiered}。单个客户失败不阻塞其余;
    但存在硬错误时, 批量处理完毕后重新抛出第一个异常 (供 worker 将任务标记 failed)。"""
    tiered = 0
    untiered = 0
    first_error = None
    for cid in customer_ids:
        try:
            r = tier_customer(store, llm, cid)
        except Exception as e:
            if first_error is None:
                first_error = e
            r = {"intent_level": "", "tags": ""}
        if r["intent_level"]:
            tiered += 1
        else:
            untiered += 1
    if first_error is not None:
        raise first_error
    return {"tiered": tiered, "untiered": untiered}
