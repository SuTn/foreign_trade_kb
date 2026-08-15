# tests/profile/test_summarizer.py
import time
from app.profile.summarizer import (summarize_customer, get_customer_summary,
                                    _parse_result, _build_incremental_input)
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


def test_summarize_customer_writes_structured_summary(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "想买 LED-100, 预算 3 万, 发到俄罗斯", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM('{"overview": "客户想买 LED-100", "intent_vehicle": "LED-100", '
                  '"budget_range": "3万", "target_country": "俄罗斯", '
                  '"concerns": "物流时效", "follow_up": "确认库存"}')
    r = summarize_customer(store, llm, "cust1")
    assert r["intent_vehicle"] == "LED-100"
    assert r["target_country"] == "俄罗斯"
    s = get_customer_summary(store, "cust1")
    assert s["intent_vehicle"] == "LED-100"
    assert s["budget_range"] == "3万"
    assert s["concerns"] == "物流时效"
    assert s["follow_up"] == "确认库存"
    assert s["updated_at"] > 0
    # 首次生成后游标推进到最大消息 ts
    assert s["last_ts"] == 100


def test_summarize_customer_no_data_returns_empty(tmp_data):
    store = SqliteStore()
    llm = FakeLLM('{"overview": "x"}')
    r = summarize_customer(store, llm, "cust1")  # 无消息
    assert r == {}
    assert get_customer_summary(store, "cust1") is None


def test_summarize_customer_parse_failure_falls_back(tmp_data):
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "hello", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM("不是 JSON")
    r = summarize_customer(store, llm, "cust1")
    assert r == {}
    assert get_customer_summary(store, "cust1") is None


def test_summarize_customer_incremental_uses_new_messages(tmp_data):
    """增量: 首次生成后, 再次触发只取新消息 + 旧摘要合并, 游标推进。"""
    store = SqliteStore()
    store.upsert_message(_msg("m1", "c1", False, "想买 LED-100", 100))
    _link(store, "c1", "cust1")
    # 首次全量
    llm1 = FakeLLM('{"overview": "v1", "intent_vehicle": "LED-100"}')
    summarize_customer(store, llm1, "cust1")
    assert get_customer_summary(store, "cust1")["last_ts"] == 100
    # 新增消息
    store.upsert_message(_msg("2", "c1", False, "预算提到 5 万", 200))
    # 第二次增量: 应走 INCREMENTAL_PROMPT (旧摘要 + 新消息)
    seen = {}
    class RecLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["prompt"] = u
            return '{"overview": "v2", "intent_vehicle": "LED-100", "budget_range": "5万"}'
    summarize_customer(store, RecLLM(), "cust1")
    s = get_customer_summary(store, "cust1")
    assert s["overview"] == "v2"
    assert s["budget_range"] == "5万"
    assert s["last_ts"] == 200  # 游标推进到新消息最大 ts
    # 增量 prompt 应包含旧摘要与新消息
    assert "已有摘要" in seen["prompt"]
    assert "预算提到 5 万" in seen["prompt"]


def test_summarize_customer_incremental_no_new_messages_noop(tmp_data):
    """增量：无新消息时返回空, 不覆盖旧摘要。"""
    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "想买 LED-100", 100))
    _link(store, "c1", "cust1")
    llm = FakeLLM('{"overview": "v1", "intent_vehicle": "LED-100"}')
    summarize_customer(store, llm, "cust1")
    # 无新消息, 再次触发
    r = summarize_customer(store, llm, "cust1")
    assert r == {}
    s = get_customer_summary(store, "cust1")
    assert s["overview"] == "v1"  # 旧摘要保留


def test_build_incremental_input_first_full_then_new(tmp_data):
    """_build_incremental_input: 首次全量, 之后只取新消息。"""
    store = SqliteStore()
    store.upsert_message(_msg("1", "c1", False, "old", 100))
    store.upsert_message(_msg("2", "c1", False, "new", 200))
    _link(store, "c1", "cust1")
    text, last = _build_incremental_input(store, "cust1", 0)
    assert "old" in text and "new" in text
    assert last == 200
    # 增量: 只取 ts>100 的新消息
    text2, last2 = _build_incremental_input(store, "cust1", 100)
    assert "new" in text2 and "old" not in text2
    assert last2 == 200


def test_parse_result_fenced_json():
    assert _parse_result('```json\n{"overview": "o", "intent_vehicle": "LED-100"}\n```') == \
        {"overview": "o", "intent_vehicle": "LED-100", "budget_range": "",
         "target_country": "", "concerns": "", "follow_up": ""}


def test_parse_result_missing_fields_default_empty():
    assert _parse_result('{"overview": "o"}') == \
        {"overview": "o", "intent_vehicle": "", "budget_range": "",
         "target_country": "", "concerns": "", "follow_up": ""}


def test_parse_result_invalid_returns_empty():
    assert _parse_result("garbage") == {}