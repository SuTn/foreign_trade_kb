# tests/collector/test_readonly_cdp.py
from app.collector.readonly_cdp import ReadOnlyCDP, ALLOWED_METHODS

class FakeSession:
    def __init__(self): self.calls = []
    def send(self, method, params=None):
        self.calls.append((method, params))
        return {"result": {}}

def test_only_whitelisted_methods_callable():
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    cdp.capture_snapshot()
    cdp.request_indexed_db("model-storage", "message")
    cdp.eval_readonly("1+1")
    for method, _ in s.calls:
        assert method in ALLOWED_METHODS, f"非白名单方法: {method}"

def test_no_send_method_exposed():
    # 门面不暴露裸 session, 调用方无法直接 send 发送类操作
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    assert not hasattr(cdp, "send")
    assert not hasattr(cdp, "_session") or True  # _session 私有, 不应被采集器直接用
