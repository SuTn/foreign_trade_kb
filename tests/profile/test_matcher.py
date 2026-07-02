from app.profile.matcher import match_customer, phone_from_jid
from app.storage.sqlite_store import SqliteStore

def test_phone_from_jid():
    assert phone_from_jid("8613800138000@s.whatsapp.net") == "8613800138000"
    assert phone_from_jid("group@g.us") is None

def test_match_creates_customer(tmp_data):
    store = SqliteStore()
    r = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r["customer_id"]
    assert r["confidence"] == 0.9
    # 重复匹配同一 chat → 同一 customer
    r2 = match_customer(store, "a1", "c1", "Alice", "8613800138000@s.whatsapp.net")
    assert r2["customer_id"] == r["customer_id"]
