# app/collector/idb_walk.py
from app.collector.readonly_cdp import ReadOnlyCDP
from app.config import settings

def walk_idb(cdp: ReadOnlyCDP, account_id: str) -> dict:
    """读 model-storage 的 message/chat/contact/group-metadata stores。
    返回 {chats: {jid:name}, messages: [IdbMessage], contacts: {...}}。
    body 在 IDB 中加密, 不取。"""
    result = {"chats": {}, "messages": [], "contacts": {}}
    for store in settings.idb_stores:
        skip = 0
        while True:
            data = cdp.request_indexed_db(settings.idb_database, store, skip_count=skip)
            objs = data.get("result", {}).get("objectStoreData", [])
            if not objs: break
            for obj in objs:
                _ingest(store, obj.get("value", {}), result, account_id)
            skip += len(objs)
            if len(objs) < 500 or skip >= settings.max_records_per_store: break
    return result

def _ingest(store, value, result, account_id):
    if store == "message":
        result["messages"].append({
            "id": value.get("id", {}).get("id") or value.get("id"),
            "chatId": value.get("chatId") or value.get("id", {}).get("remote", {}).get("user"),
            "fromMe": value.get("fromMe", False),
            "from": value.get("from"),
            "timestamp": value.get("t"),
            "type": value.get("type"),
        })
    elif store == "chat":
        jid = value.get("id", {}).get("_serialized") or value.get("id")
        name = value.get("name") or value.get("formattedTitle")
        if jid: result["chats"][jid] = name
    elif store == "contact":
        jid = value.get("id", {}).get("_serialized") or value.get("id")
        name = value.get("name") or value.get("pushname")
        if jid: result["contacts"][jid] = name
