# app/collector/idb_walk.py
"""通过 Runtime.evaluate (页面 JS) 只读遍历 model-storage 的 message/chat/contact stores。

CDP 的 IndexedDB.requestData 在该 WhatsApp 版本返回 0 行, 改用页面上下文直接
indexedDB.open + getAll (readonly) 读取。body 在 IDB 中加密, 不取 (由 DOM 提供正文)。
返回 {chats: {jid:name}, messages: [...], contacts: {...}}。
"""
from app.collector.readonly_cdp import ReadOnlyCDP
from app.config import settings

_STORE_JS_TEMPLATE = """
(function() {
  return new Promise(function(resolve) {
    var req = indexedDB.open('__DB__');
    req.onerror = function() { resolve(null); };
    req.onsuccess = function() {
      try {
        var db = req.result;
        if (!db.objectStoreNames.contains('__STORE__')) { db.close(); resolve([]); return; }
        var st = db.transaction('__STORE__', 'readonly').objectStore('__STORE__');
        var g = st.getAll();
        g.onerror = function() { resolve(null); };
        g.onsuccess = function() {
          var out = (g.result || []).map(__MAPPING__);
          db.close();
          resolve(out);
        };
      } catch (e) { resolve(null); }
    };
  });
})()
"""


def _read_store_js(store: str) -> str:
    """构造只读读取单 store 的 IIFE (返回 Promise, awaitPromise 解析)。
    仅挑精简字段, 避免把加密的 msgRowOpaqueData 等大对象序列化回传。"""
    if store == "message":
        mapping = (
            "function(m) {"
            " var idv = m.id;"
            " var idstr = typeof idv === 'string' ? idv : (idv && (idv.id || ''));"
            " var to = m.to && m.to._serialized ? m.to._serialized : (typeof m.to === 'string' ? m.to : null);"
            " var from = typeof m.from === 'string' ? m.from : (m.from && (m.from._serialized || m.from.user));"
            " return {id: idstr, t: m.t, from: from, to: to, type: m.type, fromMe: m.fromMe === true}; }"
        )
    elif store == "chat":
        mapping = (
            "function(c) {"
            " var idv = c.id;"
            " var jid = typeof idv === 'string' ? idv : (idv && (idv._serialized || idv.user));"
            " return {id: jid, name: c.name || c.formattedTitle || null}; }"
        )
    else:  # contact
        mapping = (
            "function(c) {"
            " var idv = c.id;"
            " var jid = typeof idv === 'string' ? idv : (idv && (idv._serialized || idv.user));"
            " return {id: jid, name: c.name || c.pushname || null}; }"
        )
    return (_STORE_JS_TEMPLATE
            .replace("__DB__", settings.idb_database)
            .replace("__STORE__", store)
            .replace("__MAPPING__", mapping))


async def walk_idb(cdp: ReadOnlyCDP, account_id: str) -> dict:
    """读 model-storage 的 message/chat/contact stores (页面 JS 只读)。
    返回 {chats: {jid:name}, messages: [...], contacts: {...}}。"""
    result = {"chats": {}, "messages": [], "contacts": {}}
    for store in settings.idb_stores:
        if store == "group-metadata":
            continue  # 群元数据暂不读取 (无明文正文可用)
        rows = await cdp.eval_async_readonly(_read_store_js(store)) or []
        if store == "message":
            result["messages"] = rows
        elif store == "chat":
            for r in rows:
                if r.get("id"):
                    result["chats"][r["id"]] = r.get("name")
        elif store == "contact":
            for r in rows:
                if r.get("id"):
                    result["contacts"][r["id"]] = r.get("name")
    return result
