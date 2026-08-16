# app/profile/matcher.py
"""客户匹配: WhatsApp chatId/JID → customer 实体。MVP 手机号+显示名启发式。"""
import re, uuid
from app.storage.interfaces import StructuredStore

def phone_from_jid(jid: str) -> str | None:
    """从手机号 JID 提取数字。@c.us / @s.whatsapp.net 返回数字; @lid/@g.us 返回 None,
    避免把 LID/群号误当成手机号 (这是 @lid 会话混入 customer_chat_map 的根源之一)。"""
    m = re.match(r"^(\d+)@(?!lid$|g\.us$)", jid or "")
    return m.group(1) if m else None

def is_lid_jid(jid: str) -> bool:
    return bool(jid and jid.endswith("@lid"))

def resolve_phone(store: StructuredStore, jid: str) -> str | None:
    """解析 JID 的真实手机号: @c.us 直接取数字; @lid 查 contacts 表映射。
    若传入已是纯数字 (如 _merge_idb_dom 已解析), 原样返回。"""
    if not jid:
        return None
    if "@" not in jid:
        return jid if jid.isdigit() else None
    if not is_lid_jid(jid):
        return phone_from_jid(jid)
    row = store.conn.execute("SELECT phone FROM contacts WHERE jid=?", (jid,)).fetchone()
    if row and row["phone"]:
        # contacts.phone 可能是裸数字或完整 jid, 统一取数字部分
        return phone_from_jid(row["phone"]) or (row["phone"] if row["phone"].isdigit() else None)
    return None

def match_customer(store: StructuredStore, account_id: str, chat_id: str,
                   display_name: str | None, jid: str) -> dict:
    """启发式匹配: 手机号优先, 显示名次之。返回 {customer_id, confidence, confirmed}。
    LID 会话经 contacts 表解析为真实手机号, 避免把 LID 当手机号建重复客户。
    若真实手机号已有客户, 新会话映射到该客户 (不再以 LID 另建)。"""
    phone = resolve_phone(store, jid) or phone_from_jid(jid)
    # 查现有 customer
    row = None
    if phone:
        row = store.conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
    if not row and display_name:
        row = store.conn.execute("SELECT id FROM customers WHERE display_name=?", (display_name,)).fetchone()
    if row:
        cid = row["id"]; conf = 0.9 if phone else 0.6
        # 补全缺失的显示名 (不覆盖已有名字)
        if display_name:
            store.conn.execute(
                "UPDATE customers SET display_name=COALESCE(NULLIF(display_name,''), NULLIF(?, '')) WHERE id=?",
                (display_name, cid))
            store.conn.commit()
    else:
        cid = str(uuid.uuid4())
        store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?,NULL)",
                           (cid, display_name, phone, int(__import__("time").time())))
        store.conn.commit()
        conf = 0.9 if phone else 0.5  # 手机号为强标识, 首次创建亦高置信
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,chat_id) DO UPDATE SET "
        "customer_id=excluded.customer_id, match_confidence=excluded.match_confidence, updated_at=excluded.updated_at",
        (account_id, chat_id, cid, conf, 0, int(__import__("time").time())))
    store.conn.commit()
    return {"customer_id": cid, "confidence": conf, "confirmed": False}
