from app.profile.matcher import match_customer, phone_from_jid, resolve_phone, is_lid_jid
from app.storage.sqlite_store import SqliteStore

def test_phone_from_jid():
    assert phone_from_jid("8613800138000@s.whatsapp.net") == "8613800138000"
    assert phone_from_jid("group@g.us") is None

def test_is_lid_jid():
    assert is_lid_jid("106558658740375@lid")
    assert not is_lid_jid("8613800138000@c.us")

def test_resolve_phone_via_contacts(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO contacts VALUES(?,?,?,?,?)",
                       ("106558658740375@lid", "a1", "Lucas", "447974905044", 0))
    store.conn.commit()
    assert resolve_phone(store, "106558658740375@lid") == "447974905044"
    assert resolve_phone(store, "447974905044@c.us") == "447974905044"
    assert resolve_phone(store, "106558658740375@lid") != "106558658740375"
    # 纯数字 (已解析) 原样返回
    assert resolve_phone(store, "447974905044") == "447974905044"
    # contacts.phone 存完整 jid 时也归一化为数字
    store.conn.execute("INSERT INTO contacts VALUES(?,?,?,?,?)",
                       ("999@lid", "a1", "X", "8615976909619@c.us", 0))
    store.conn.commit()
    assert resolve_phone(store, "999@lid") == "8615976909619"

def test_match_creates_customer(tmp_data):
    store = SqliteStore()
    r = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r["customer_id"]
    assert r["confidence"] == 0.9
    # 重复匹配同一 chat → 同一 customer
    r2 = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r2["customer_id"] == r["customer_id"]

def test_match_lid_resolves_phone(tmp_data):
    """LID 会话应解析为真实手机号匹配, 不把 LID 当 phone 建新客户。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO contacts VALUES(?,?,?,?,?)",
                       ("106558658740375@lid", "a1", "Lucas", "447974905044", 0))
    store.conn.commit()
    r1 = match_customer(store, "a1", "106558658740375@lid", "Lucas", "106558658740375@lid")
    assert r1["confidence"] == 0.9
    # 同一手机号 @c.us 会话再次匹配 → 同一客户
    r2 = match_customer(store, "a1", "447974905044@c.us", "Lucas", "447974905044@c.us")
    assert r2["customer_id"] == r1["customer_id"]
    phone = store.conn.execute("SELECT phone FROM customers WHERE id=?", (r1["customer_id"],)).fetchone()[0]
    assert phone == "447974905044"

def test_match_lid_to_phone_no_dup(tmp_data):
    """真实手机号客户已存在时, LID 会话应复用它而非另建新客户。"""
    store = SqliteStore()
    r_phone = match_customer(store, "a1", "447974905044@c.us", None, "447974905044@c.us")
    store.conn.execute("INSERT INTO contacts VALUES(?,?,?,?,?)",
                       ("106558658740375@lid", "a1", "Lucas", "447974905044", 0))
    store.conn.commit()
    r_lid = match_customer(store, "a1", "106558658740375@lid", "Lucas", "106558658740375@lid")
    assert r_lid["customer_id"] == r_phone["customer_id"]
    assert store.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 1

def test_match_fills_display_name(tmp_data):
    """已存在客户 (display_name 空) 匹配时补全显示名, 不覆盖已有值。"""
    store = SqliteStore()
    r1 = match_customer(store, "a1", "c1", None, "8613800138000@s.whatsapp.net")
    # 第二次带名字 → 补上
    r2 = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert store.conn.execute("SELECT display_name FROM customers WHERE id=?",
                              (r1["customer_id"],)).fetchone()[0] == "Alice"
    # 带新名字 → 不覆盖已存在值
    r3 = match_customer(store, "a1", "c1", "Alicia", "8613800138000@s.whatsapp.net")
    assert store.conn.execute("SELECT display_name FROM customers WHERE id=?",
                              (r1["customer_id"],)).fetchone()[0] == "Alice"
