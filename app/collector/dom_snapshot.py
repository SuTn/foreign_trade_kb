# app/collector/dom_snapshot.py
"""从 DOMSnapshot.captureSnapshot 结果中解析 [data-id] 消息行的明文正文。"""
from app.config import settings

def parse_dom_snapshot(snapshot: dict, active_chat_name: str | None = None) -> list[dict]:
    """返回 [{message_id, body, sender, ts}]。DOM 是明文正文来源。"""
    # snapshot 结构: {documents: [{nodes: [...]}], strings: [...]}
    # 简化: 实际实现需遍历 nodes 找 data-id 属性的行, 提取文本节点
    # 这里给出基于 strings 表的解析骨架
    messages = []
    strings = snapshot.get("strings", [])
    nodes = snapshot.get("documents", [{}])[0].get("nodes", {})
    # 完整解析依赖 WhatsApp Web DOM 结构, 集中在 dom_selectors 配置
    # 此处返回骨架, 实际由 fixture 测试驱动完善
    return messages
