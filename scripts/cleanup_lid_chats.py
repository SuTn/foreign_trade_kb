"""一次性清理陈旧的 @lid 会话映射 (whatsapp-bidirectional-chat 之前的 JID 归一化遗留)。

背景: 早期版本把同一会话既存成 @lid 又存成 @c.us 两种 chat_id, 导致
customer_chat_map 里同一客户出现多条会话 (工作台显示多个 tab)、follow 失效。
消息 (messages) 与 chats 表其实都已是归一化的 @c.us 形式, 只有 customer_chat_map
残留 @lid 条目。本脚本用 contacts 表的 @lid→真实手机号 映射, 把 @lid 归并到
对应的 @c.us, 或直接删除无映射的孤儿条目。

用法: python -m scripts.cleanup_lid_chats
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.storage.sqlite_store import SqliteStore


def main():
    store = SqliteStore()
    conn = store.conn

    # 1. @lid → 真实手机号 (contacts 表, phone 是裸数字)
    lid_phone = {}
    for r in conn.execute(
            "SELECT jid, phone FROM contacts WHERE jid LIKE '%@lid' AND phone IS NOT NULL").fetchall():
        lid_phone[r["jid"]] = r["phone"]

    # 2. 归并 customer_chat_map 里的 @lid 条目
    fixed = 0
    removed = 0
    rows = conn.execute(
        "SELECT account_id, chat_id, customer_id FROM customer_chat_map WHERE chat_id LIKE '%@lid'").fetchall()
    for r in rows:
        lid = r["chat_id"]
        phone = lid_phone.get(lid)
        if not phone:
            # 无映射的孤儿条目: 直接删除 (无消息、无 chats 行)
            conn.execute("DELETE FROM customer_chat_map WHERE account_id=? AND chat_id=?",
                         (r["account_id"], lid))
            removed += 1
            continue
        cus = f"{phone}@c.us"
        exists = conn.execute(
            "SELECT customer_id FROM customer_chat_map WHERE account_id=? AND chat_id=?",
            (r["account_id"], cus)).fetchone()
        if exists:
            # 同一客户已有 @c.us 条目 → 删除重复的 @lid
            conn.execute("DELETE FROM customer_chat_map WHERE account_id=? AND chat_id=?",
                         (r["account_id"], lid))
            removed += 1
        else:
            # 仅 @lid 的客户 → 把 chat_id 改成 @c.us
            conn.execute("UPDATE customer_chat_map SET chat_id=? WHERE account_id=? AND chat_id=?",
                         (cus, r["account_id"], lid))
            fixed += 1

    # 3. 兜底: 清理 chats / messages 中可能残留的 @lid (预期为 0)
    conn.execute("DELETE FROM chats WHERE id LIKE '%@lid'")
    conn.execute("DELETE FROM messages WHERE chat_id LIKE '%@lid'")
    conn.commit()

    left = conn.execute(
        "SELECT COUNT(*) FROM customer_chat_map WHERE chat_id LIKE '%@lid'").fetchone()[0]
    print(f"清理完成: 归并 @lid→@c.us {fixed} 条, 删除孤儿 @lid {removed} 条, 剩余 @lid {left} 条")
    store.conn.close()


if __name__ == "__main__":
    main()
