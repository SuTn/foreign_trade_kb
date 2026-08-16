# tests/collector/test_send_drain.py
import asyncio
from app.collector.scanner import Scanner


class FakeRt:
    def get_typed(self, key, default):
        if key == "send_enabled":
            return True
        return default

    def get(self, key, default=None):
        return None


class Store:
    def __init__(self):
        self.calls = []
        self._pending = None

    def next_pending_send_request(self):
        return self._pending

    def mark_send_request_running(self, rid):
        self.calls.append(("running", rid))

    def mark_send_request_done(self, rid):
        self.calls.append(("done", rid))

    def mark_send_request_failed(self, rid, err):
        self.calls.append(("failed", rid, err))

    def bump_send_request_attempts(self, rid, err):
        self.calls.append(("bump", rid, err))


class FakePage:
    def __init__(self):
        self.opened = []
        self.sent = []

    def locator(self, sel):
        class L:
            @property
            def first(self):
                return self
            async def count(self):
                return 1
            async def click(self):
                pass
        return L()

    @property
    def keyboard(self):
        class K:
            async def type(self, t):
                pass
            async def press(self, k):
                pass
        return K()

    async def wait_for_timeout(self, ms):
        pass


def test_drain_send_when_enabled(tmp_data, monkeypatch):
    store = Store()
    store._pending = {"id": 1, "chat_id": "c1", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._rt = FakeRt()
    sc.page = FakePage()
    sc._chat_lookup_query = lambda chat_id: "Alice"
    async def fake_open(page, query):
        sc.page.opened.append(query)
        return True
    async def fake_send(page, text):
        sc.page.sent.append(text)
        return True
    monkeypatch.setattr("app.collector.sender.open_chat", fake_open)
    monkeypatch.setattr("app.collector.sender.send_text", fake_send)
    asyncio.run(sc._drain_send_requests())
    assert ("done", 1) in store.calls
    assert sc.page.opened == ["Alice"]
    assert sc.page.sent == ["hi"]


def test_drain_send_skipped_when_disabled(tmp_data):
    store = Store()
    store._pending = {"id": 1, "chat_id": "c1", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._chat_lookup_query = lambda chat_id: "Alice"
    class DisabledRt:
        def get_typed(self, key, default):
            return False
    sc._rt = DisabledRt()
    sc.page = FakePage()
    asyncio.run(sc._drain_send_requests())
    assert ("failed", 1, "发送功能未开启") in store.calls


def test_drain_send_open_chat_failure_marks_failed_no_send(tmp_data, monkeypatch):
    """C1 修复: open_chat 返回 False 时绝不发送, 标 failed。"""
    store = Store()
    store._pending = {"id": 1, "chat_id": "c1", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._rt = FakeRt()
    sc.page = FakePage()
    sc._chat_lookup_query = lambda chat_id: "Alice"
    async def fail_open(page, query):
        return False
    async def fake_send(page, text):
        sc.page.sent.append(text)
        return True
    monkeypatch.setattr("app.collector.sender.open_chat", fail_open)
    monkeypatch.setattr("app.collector.sender.send_text", fake_send)
    asyncio.run(sc._drain_send_requests())
    assert any(c[0] == "failed" for c in store.calls)
    assert sc.page.sent == []  # 未发送
    assert ("done", 1) not in store.calls


def test_drain_send_no_query_marks_failed(tmp_data):
    """C1 修复: 无显示名/手机号 (如群聊) 时 query 为空 → 标 failed, 不发送。"""
    store = Store()
    store._pending = {"id": 1, "chat_id": "g1@g.us", "text": "hi"}
    sc = Scanner(None, store, None)
    sc._rt = FakeRt()
    sc.page = FakePage()
    sc._chat_lookup_query = lambda chat_id: None
    asyncio.run(sc._drain_send_requests())
    assert any(c[0] == "failed" for c in store.calls)


def test_sync_follow_open_failure_keeps_current(tmp_data, monkeypatch):
    """I3 修复: open_chat 返回 False 时不更新 _current_chat_id。"""
    sc = Scanner(None, Store(), None)
    class FollowRt:
        def get(self, key, default=None):
            return "c9" if key == "collector_follow_chat" else None
        def get_typed(self, key, default):
            return default
    sc._rt = FollowRt()
    sc.page = FakePage()
    sc._chat_lookup_query = lambda chat_id: "Bob"
    sc._current_chat_id = None
    async def fail_open(page, query):
        return False
    monkeypatch.setattr("app.collector.sender.open_chat", fail_open)
    asyncio.run(sc._sync_follow())
    assert sc._current_chat_id is None
