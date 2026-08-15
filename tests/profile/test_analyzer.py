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
    assert isinstance(result, dict)
    assert "LED" in result["summary"]  # 非 JSON 输出回退到 summary 字段


def test_analyze_customer_parses_json(tmp_data):
    """LLM 输出 JSON 时解析为结构化字段。"""
    from app.profile.analyzer import _parse_analysis
    r = _parse_analysis('{"interests": "LED, 大功率", "activity": "高", "followup": "报价跟进", "summary": "高意向客户"}')
    assert r["interests"] == "LED, 大功率"
    assert r["activity"] == "高"
    assert r["followup"] == "报价跟进"
    assert r["summary"] == "高意向客户"


def test_analyze_customer_parses_json_with_surrounding_text(tmp_data):
    """LLM 输出带前后缀文字时提取 JSON 块。"""
    from app.profile.analyzer import _parse_analysis
    r = _parse_analysis('分析结果如下：{"interests": "LED", "activity": "中", "followup": "跟进", "summary": "一般"} 以上。')
    assert r["interests"] == "LED"
    assert r["summary"] == "一般"
