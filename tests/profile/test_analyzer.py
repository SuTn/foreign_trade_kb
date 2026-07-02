from app.profile.analyzer import analyze_customer
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "兴趣:LED; 活跃:高; 建议:报价跟进"

def test_analyze_customer(tmp_data):
    store = SqliteStore()
    store.upsert_profile_field("cust1", "country", "USA", "manual")
    result = analyze_customer(store, FakeLLM(), "cust1", "客户问LED价格")
    assert "LED" in result
