# tests/profile/test_tiering.py
import time
from app.profile.tiering import tier_customer, tier_customers, PREDEFINED_TAGS
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
from app.llm.interfaces import LLM


class FakeLLM(LLM):
    def __init__(self, resp): self.resp = resp
    def generate(self, s, u, max_tokens=1024): return self.resp


def _msg(mid, chat, from_me, body, ts):
    return Message(mid, "a1", chat, from_me, None, ts, "chat", body, bool(body), int(time.time()))


def _link(store, chat, cust):
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", chat, cust, 0.9, 0, 0))
    store.conn.commit()


def test_predefined_tags_defined():
    assert "已购" in PREDEFINED_TAGS
    assert "议价中" in PREDEFINED_TAGS


def test_tier_customer_writes_profile_and_history(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "确认车型 LED-100, 谈付款", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购,议价中"}')
    r = tier_customer(store, llm, "cust1")
    assert r["intent_level"] == "A"
    assert r["tags"] == "已购,议价中"
    prof = {p.field: p.value for p in store.get_profile("cust1")}
    assert prof["intent_level"] == "A"
    assert prof["tags"] == "已购,议价中"
    hist = store.get_tier_history("cust1")
    assert len(hist) == 1
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"


def test_tier_customer_does_not_override_manual(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "want LED price", 100))
    _link(store, "c1", "cust1")
    store.upsert_profile_field("cust1", "intent_level", "B", "manual")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    tier_customer(store, llm, "cust1")
    prof = {p.field: p.value for p in store.get_profile("cust1")}
    assert prof["intent_level"] == "B"  # manual 不被 auto 覆盖
    # 历史仍记录本次 auto 分层结果
    hist = store.get_tier_history("cust1")
    assert hist[0]["intent_level"] == "A"
    assert hist[0]["source"] == "auto"


def test_tier_customer_no_data_marks_untiered(tmp_data):
    store = SqliteStore()
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    r = tier_customer(store, llm, "cust1")  # 无消息
    assert r["intent_level"] == ""
    assert r["tags"] == ""
    assert store.get_tier_history("cust1") == []


def test_tier_customer_parse_failure_falls_back(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "hello", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM("不是 JSON")
    r = tier_customer(store, llm, "cust1")
    assert r["intent_level"] == ""
    assert r["tags"] == ""
    assert store.get_tier_history("cust1") == []


def test_tier_customers_batch(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "确认车型, 谈付款", 100))
    store.upsert_message(_msg("m2", "c2", False, "一般询价", 100))
    _link(store, "c1", "cust1")
    _link(store, "c2", "cust2")
    llm = FakeLLM('{"intent_level": "A", "tags": "已购"}')
    r = tier_customers(store, llm, ["cust1", "cust2"])
    assert r["tiered"] == 2
    assert r["untiered"] == 0
    assert len(store.get_tier_history("cust1")) == 1
    assert len(store.get_tier_history("cust2")) == 1
