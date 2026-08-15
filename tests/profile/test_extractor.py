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


def test_extract_profile_syncs_company_country_to_customers(tmp_data):
    """G: 抽取的 company/country 同步到 customers 固定列, 使 search_customers 可命中。"""
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,NULL,NULL,?,NULL)",
                       ("cust1", "Alice", "10086", 0))
    store.conn.commit()
    extract_profile(store, FakeLLM(), "cust1", "客户来自美国想买LED")
    row = store.conn.execute("SELECT company, country FROM customers WHERE id='cust1'").fetchone()
    assert row["country"] == "USA"  # 同步到固定列
    # search_customers 能按 country 命中
    hits = store.search_customers("USA")
    assert any(c["id"] == "cust1" for c in hits)
