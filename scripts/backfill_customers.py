# scripts/backfill_customers.py
"""存量数据回填: LID 会话补真实手机号 + 从聊天记录补显示名。

背景: 早期采集把 @lid JID 的数字直接当手机号建了 customer (phone=LID), 且
display_name 全为空。本脚本:
1. 把 contacts 表 (采集器已落库, 含 LID→真实手机号) 同步到 customers.phone,
   并合并 LID 客户与其真实手机号客户 (保留画像/消息映射)。
2. 从 messages 的发送人显示名 (sender_jid 存的名字) 回填 display_name。

用法: python -m scripts.backfill_customers
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.storage.sqlite_store import SqliteStore
from app.profile.matcher import phone_from_jid

def main():
    store = SqliteStore()
    conn = store.conn
    n_phone = 0
    n_name = 0
    # 1) LID→真实手机号: contacts 表里有 lid jid→phone, 或 customer 当前 phone 命中 contacts 的 phone
    lid_to_phone = {r["jid"]: r["phone"] for r in
                    conn.execute("SELECT jid, phone FROM contacts WHERE phone IS NOT NULL").fetchall()}
    # customers 当前 phone 是 LID 数字且 contacts 有该 LID 的真实 phone
    rows = conn.execute("SELECT id, phone FROM customers WHERE phone IS NOT NULL").fetchall()
    for r in rows:
        cur = r["phone"]
        real = lid_to_phone.get(f"{cur}@lid")  # 客户 phone 存的是 LID 数字
        if not real:
            continue
        # 先查是否有同真实手机号的现成客户, 有则合并
        dup = conn.execute("SELECT id FROM customers WHERE phone=? AND id!=?",
                           (real, r["id"])).fetchone()
        if dup:
            conn.execute("UPDATE customer_chat_map SET customer_id=? WHERE customer_id=?",
                         (dup["id"], r["id"]))
            conn.execute("UPDATE profiles SET customer_id=? WHERE customer_id=?",
                         (dup["id"], r["id"]))
            if not conn.execute("SELECT display_name FROM customers WHERE id=?",
                                (dup["id"],)).fetchone()[0]:
                nm = conn.execute("SELECT display_name FROM customers WHERE id=?", (r["id"],)).fetchone()[0]
                if nm:
                    conn.execute("UPDATE customers SET display_name=? WHERE id=?", (nm, dup["id"]))
            conn.execute("DELETE FROM customers WHERE id=?", (r["id"],))
        else:
            conn.execute("UPDATE customers SET phone=? WHERE id=?", (real, r["id"]))
        n_phone += 1
    conn.commit()
    # 2) 回填显示名: 从 messages 中该客户会话的入站发送人显示名取一个
    rows = conn.execute(
        "SELECT c.id, m.chat_id, c.phone, c.display_name FROM customers c "
        "JOIN customer_chat_map m ON m.customer_id=c.id"
    ).fetchall()
    for r in rows:
        if r["display_name"]:
            continue
        cand = conn.execute(
            "SELECT DISTINCT sender_jid FROM messages WHERE chat_id=? AND from_me=0 "
            "AND sender_jid IS NOT NULL ORDER BY ts DESC LIMIT 5", (r["chat_id"],)).fetchall()
        name = None
        for c in cand:
            v = c["sender_jid"]
            if not v or "@" in v:
                continue  # 跳过 JID 形态 (@c.us/@lid)
            if phone_from_jid(v) or v.replace("+", "").replace(" ", "").isdigit():
                continue  # 跳过手机号形态
            name = v
            break
        if name:
            conn.execute("UPDATE customers SET display_name=? WHERE id=?", (name, r["id"]))
            n_name += 1
    conn.commit()
    print(f"回填完成: LID→手机号 {n_phone} 个, 补显示名 {n_name} 个")

if __name__ == "__main__":
    main()
