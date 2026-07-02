# app/profile/matcher.py
"""客户匹配: WhatsApp chatId/JID → customer 实体。MVP 手机号+显示名启发式。"""
import re, uuid
from app.storage.interfaces import StructuredStore

def phone_from_jid(jid: str) -> str | None:
    m = re.match(r"^(\d+)@", jid or "")
    return m.group(1) if m else None

def match_customer(store: StructuredStore, account_id: str, chat_id: str,
                   display_name: str | None, jid: str) -> dict:
    """启发式匹配: 手机号优先, 显示名次之。返回 {customer_id, confidence, confirmed}。"""
    phone = phone_from_jid(jid)
    # 查现有 customer
    row = None
    if phone:
        row = store.conn.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
    if not row and display_name:
        row = store.conn.execute("SELECT id FROM customers WHERE display_name=?", (display_name,)).fetchone()
    if row:
        cid = row["id"]; conf = 0.9 if phone else 0.6
    else:
        cid = str(uuid.uuid4())
        store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?)",
                           (cid, display_name, phone, int(__import__("time").time())))
        store.conn.commit()
        conf = 0.9 if phone else 0.5  # 手机号为强标识, 首次创建亦高置信
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?) ON CONFLICT(account_id,chat_id) DO UPDATE SET "
        "customer_id=excluded.customer_id, match_confidence=excluded.match_confidence, updated_at=excluded.updated_at",
        (account_id, chat_id, cid, conf, 0, int(__import__("time").time())))
    store.conn.commit()
    return {"customer_id": cid, "confidence": conf, "confirmed": False}
