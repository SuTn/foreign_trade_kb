# tests/collector/test_readonly_cdp.py
from app.collector.readonly_cdp import ReadOnlyCDP, ALLOWED_METHODS

class FakeSession:
    def __init__(self): self.calls = []
    async def send(self, method, params=None):
        self.calls.append((method, params))
        return {"result": {}}

async def test_only_whitelisted_methods_callable():
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    await cdp.capture_snapshot()
    await cdp.request_indexed_db("model-storage", "message")
    await cdp.eval_readonly("1+1")
    await cdp.eval_async_readonly("Promise.resolve(1)")
    await cdp.scroll_conversation_up()
    for method, _ in s.calls:
        assert method in ALLOWED_METHODS, f"非白名单方法: {method}"

def test_no_send_method_exposed():
    # 门面不暴露裸 session, 调用方无法直接 send 发送类操作
    s = FakeSession()
    cdp = ReadOnlyCDP(s)
    assert not hasattr(cdp, "send")
