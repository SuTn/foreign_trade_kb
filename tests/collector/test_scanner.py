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
