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
        var out = [];
        var limit = __LIMIT__;
        var curReq = st.openCursor();
        curReq.onerror = function() { resolve(null); };
        curReq.onsuccess = function() {
          var cur = curReq.result;
          if (!cur || out.length >= limit) {
            db.close();
            resolve(out);
            return;
          }
          out.push(__MAPPING__(cur.value));
          cur["continue"]();
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
    elif store == "group-metadata":
        mapping = (
            "function(g) {"
            " var idv = g.id;"
            " var jid = typeof idv === 'string' ? idv : (idv && (idv._serialized || idv.user));"
            " var members = (g.members || []).map(function(m) {"
            "   var mv = m.jid;"
            "   var mj = typeof mv === 'string' ? mv : (mv && (mv._serialized || mv.user));"
            "   return {jid: mj, name: m.name || m.pushname || null};"
            " });"
            " return {id: jid, name: g.name || g.formattedTitle || null, members: members}; }"
        )
    else:  # contact
        mapping = (
            "function(c) {"
            " var idv = c.id;"
            " var jid = typeof idv === 'string' ? idv : (idv && (idv._serialized || idv.user));"
            " var lidv = c.lid;"
            " var lid = typeof lidv === 'string' ? lidv : (lidv && (lidv._serialized || lidv.user));"
            " var ph = c.phoneNumber || c.phone;"
            " var phone = typeof ph === 'string' ? ph : (ph && (ph._serialized || ph.user));"
            " var jid2 = c.jid;"
            " var alt_jid = typeof jid2 === 'string' ? jid2 : (jid2 && (jid2._serialized || jid2.user));"
            " return {id: jid, lid: lid, phone: phone, alt_jid: alt_jid,"
            "         name: c.name || c.pushname || null, form: c.formattedName || null}; }"
        )
    return (_STORE_JS_TEMPLATE
            .replace("__DB__", settings.idb_database)
            .replace("__STORE__", store)
            .replace("__LIMIT__", str(settings.max_records_per_store))
            .replace("__MAPPING__", mapping))


async def walk_idb(cdp: ReadOnlyCDP, account_id: str) -> dict:
    """读 model-storage 的 message/chat/contact/group-metadata stores (页面 JS 只读)。
    返回 {chats: {jid:name}, messages: [...], contacts: {jid:name},
          lids: {lid_jid: {phone_jid, name}}, lid_to_phone: {lid_jid: phone_jid},
          groups: {g_jid: {"name": str|None, "members": {member_jid: name|None}}}}。
    contacts 同时按手机号 jid 与 lid jid 建索引, 供 LID↔手机号双向查找;
    groups 由 group-metadata store 防御式构建, store 缺失时静默为空。"""
    result = {"chats": {}, "messages": [], "contacts": {}, "lids": {}, "lid_to_phone": {},
              "phone_by_lid": {}, "groups": {}}
    for store in settings.idb_stores:
        rows = await cdp.eval_async_readonly(_read_store_js(store)) or []
        if store == "message":
            result["messages"] = rows
        elif store == "chat":
            for r in rows:
                if r.get("id"):
                    result["chats"][r["id"]] = r.get("name")
        elif store == "group-metadata":
            for r in rows:
                gid = r.get("id")
                if not gid:
                    continue
                members = {}
                for m in r.get("members") or []:
                    mj = m.get("jid")
                    if mj:
                        members[mj] = m.get("name")
                result["groups"][gid] = {"name": r.get("name"), "members": members}
        elif store == "contact":
            for r in rows:
                if not r.get("id"):
                    continue
                name = r.get("name")
                phone = r.get("phone")
                result["contacts"][r["id"]] = name
                lid = r.get("lid")
                if lid:
                    result["contacts"][lid] = name  # LID→name 也可查
                    result["lids"][lid] = {"phone_jid": r["id"], "name": name}
                    result["lid_to_phone"][lid] = r["id"]
                if phone:
                    result["phone_by_lid"][r["id"]] = phone
                    # 纯手机号也可反查名字
                    result["contacts"][phone] = result["contacts"].get(phone) or name
    return result
