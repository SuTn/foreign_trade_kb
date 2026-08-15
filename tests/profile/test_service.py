# tests/profile/test_service.py
from app.profile.service import (
    build_chat_summary, build_customer_summary,
    refresh_customer_profile, analyze_customer_full,
)
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
from app.llm.interfaces import LLM
import time

class FakeLLM(LLM):
    def __init__(self, resp): self.resp = resp
    def generate(self, s, u, max_tokens=1024): return self.resp

def _msg(mid, chat, from_me, body, ts):
    return Message(mid, "a1", chat, from_me, None, ts, "chat", body, bool(body), int(time.time()))

def test_build_chat_summary_chronological(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "Hi", 100))
    store.upsert_message(_msg("m2", "c1", True, "Hello", 101))
    s = build_chat_summary(store, "c1")
    assert s.index("客户: Hi") < s.index("我: Hello")

def test_build_customer_summary_aggregates_chats(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "Hi", 100))
    store.upsert_message(_msg("m2", "c2", False, "Yo", 100))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c2", "cust1", 0.9, 0, 0))
    store.conn.commit()
    s = build_customer_summary(store, "cust1")
    assert "Hi" in s and "Yo" in s

def test_refresh_customer_profile_calls_extractor(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "want LED price", 100))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    llm = FakeLLM('{"product_interest": "LED灯"}')
    fields = refresh_customer_profile(store, llm, "cust1")
    assert fields["product_interest"] == "LED灯"
    prof = store.get_profile("cust1")
    assert {p.field: p.value for p in prof}["product_interest"] == "LED灯"
    assert prof[0].source == "auto"  # 来源标记 auto

def test_refresh_customer_profile_with_chat_id(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "want LED price", 100))
    llm = FakeLLM('{"country": "USA"}')
    fields = refresh_customer_profile(store, llm, "cust1", chat_id="c1")
    assert fields["country"] == "USA"

def test_refresh_skips_when_no_messages(tmp_data):
    store = SqliteStore()
    assert refresh_customer_profile(store, FakeLLM("{}"), "cust1") == {}

def test_analyze_customer_full(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "ask LED price", 100))
    store.upsert_profile_field("cust1", "country", "USA", "manual")
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    llm = FakeLLM("兴趣:LED; 活跃:高; 建议:报价")
    r = analyze_customer_full(store, llm, "cust1")
    assert isinstance(r, dict)
    assert "LED" in r["summary"]

def test_build_chat_summary_group_annotates_sender_name(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("g1","a1","g1","海外采购群","group",0))
    store.conn.commit()
    store.upsert_message(Message("m1", "a1", "g1", False, "8615976909619@c.us", 100,
                                 "chat", "Hi", True, int(time.time()), "Sonya"))
    store.upsert_message(Message("m2", "a1", "g1", True, "a1@c.us", 101,
                                 "chat", "Ok", True, int(time.time())))
    s = build_chat_summary(store, "g1")
    assert "Sonya: Hi" in s
    assert "我: Ok" in s


def test_build_chat_summary_single_format_unchanged(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("c1","a1","c1","Alice","single",0))
    store.conn.commit()
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 100,
                                 "chat", "Hi", True, int(time.time())))
    store.upsert_message(Message("m2", "a1", "c1", True, "a1@w", 101,
                                 "chat", "Hello", True, int(time.time())))
    s = build_chat_summary(store, "c1")
    assert "客户: Hi" in s
    assert "我: Hello" in s
