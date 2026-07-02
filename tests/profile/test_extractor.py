from app.profile.extractor import extract_profile
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return '{"country": "USA", "product_interest": "LED灯"}'

def test_extract_profile_skips_manual(tmp_data):
    store = SqliteStore()
    store.upsert_profile_field("cust1", "country", "China", "manual")  # 人工值
    extract_profile(store, FakeLLM(), "cust1", "客户来自美国想买LED")
    p = {f.field: f.value for f in store.get_profile("cust1")}
    assert p["country"] == "China"  # manual 不被覆盖
    assert p["product_interest"] == "LED灯"  # auto 新增
