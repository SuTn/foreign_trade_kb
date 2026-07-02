# app/collector/merger.py
def merge_messages(idb_messages: list[dict], dom_messages: list[dict]) -> list[dict]:
    """IDB 元数据 + DOM 明文正文按 message id 合并。
    IDB 提供元数据, DOM 提供 body; DOM 缺失则 body=None。"""
    dom_by_id = {m["message_id"]: m for m in dom_messages}
    merged = []
    for m in idb_messages:
        mid = m.get("id")
        dom = dom_by_id.get(mid)
        merged.append({
            "id": mid, "chatId": m.get("chatId"), "fromMe": m.get("fromMe"),
            "from": m.get("from"), "timestamp": m.get("timestamp"), "type": m.get("type"),
            "body": dom["body"] if dom else None,
            "body_present": bool(dom and dom.get("body")),
        })
    return merged
