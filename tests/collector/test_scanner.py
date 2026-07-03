# tests/collector/test_scanner.py
import time
from app.collector.scanner import write_status, read_status, is_alive, Scanner
from app.config import settings

def test_status_heartbeat(tmp_data):
    write_status(settings.status_path, {"state": "running"})
    s = read_status(settings.status_path)
    assert "last_heartbeat" in s
    assert is_alive(settings.status_path) is True

def test_status_dead_after_timeout(tmp_data):
    write_status(settings.status_path, {"state": "running"})
    # 模拟超时
    import app.collector.scanner as sc
    old = settings.heartbeat_timeout_sec
    s = read_status(settings.status_path)
    s["last_heartbeat"] = time.time() - 100
    settings.status_path.write_text(__import__("json").dumps(s))
    assert is_alive(settings.status_path, timeout=1) is False

class FakeStore:
    def __init__(self): self.msgs = []
    def upsert_message(self, m): self.msgs.append(m)

class FakeVector:
    def upsert_message_vector(self, *a, **k): pass

class FakeCDP:
    def __init__(self, snaps): self.snaps = snaps; self.i = 0
    def capture_snapshot(self):
        s = self.snaps[min(self.i, len(self.snaps)-1)]; self.i += 1; return s

def test_fast_tick_skips_unchanged(tmp_data, monkeypatch):
    # 两次相同 snapshot → 第二次不产出
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s: [])
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    import asyncio
    asyncio.run(sc.fast_tick())  # 空 dom, hash 一致


class BackfillCDP:
    """模拟滚动回溯: 每次滚动后产出新消息, 第 3 次起无新消息 (到顶)。
    消息用合并后结构 (id/chatId), 经 parse_dom_snapshot_safe 透传。"""
    def __init__(self):
        self.scroll_count = 0
        self._waves = [
            [{"id": "m1", "body": "old1", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 1, "type": "chat"}],
            [{"id": "m1", "body": "old1", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 1, "type": "chat"},
             {"id": "m2", "body": "old2", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 2, "type": "chat"}],
            [{"id": "m1", "body": "old1", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 1, "type": "chat"},
             {"id": "m2", "body": "old2", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 2, "type": "chat"}],
        ]
        self.i = 0
    def capture_snapshot(self):
        s = self._waves[min(self.i, len(self._waves) - 1)]; self.i += 1; return s
    def scroll_conversation_up(self):
        self.scroll_count += 1
        return True

def test_backfill_history_ingests_new_messages(tmp_data, monkeypatch):
    """3.7: 按需历史回溯 — 滚动加载更早消息并入库, 到顶后停止。"""
    import asyncio
    # BackfillCDP 的 capture_snapshot 直接返回消息列表, 让解析函数原样透传
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s: s)
    cdp = BackfillCDP()
    store = FakeStore()
    sc = Scanner(cdp, store, FakeVector())
    n = asyncio.run(sc.backfill_history(chat_id="c1", max_scrolls=5))
    assert n == 2  # m1 + m2 (m1 第二波已存在, 不重复)
    assert cdp.scroll_count == 3  # 第 3 次滚动无新消息 → 到顶停止

def test_backfill_history_stops_when_no_panel(tmp_data, monkeypatch):
    """会话面板不存在时滚动返回 False, 立即停止, 入库 0。"""
    import asyncio
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s: s)
    class NoPanelCDP(BackfillCDP):
        def scroll_conversation_up(self): return False
    cdp = NoPanelCDP()
    sc = Scanner(cdp, FakeStore(), FakeVector())
    n = asyncio.run(sc.backfill_history(max_scrolls=5))
    assert n == 0
