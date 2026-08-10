# tests/collector/test_scanner.py
import asyncio, time
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
    def __init__(self): self.msgs = []; self.chats = []
    def upsert_message(self, m): self.msgs.append(m)
    def upsert_chat(self, c): self.chats.append(c)

class FakeVector:
    def upsert_message_vector(self, *a, **k): pass

class FakeCDP:
    def __init__(self, snaps): self.snaps = snaps; self.i = 0
    async def capture_snapshot(self):
        s = self.snaps[min(self.i, len(self.snaps)-1)]; self.i += 1; return s

def test_fast_tick_skips_unchanged(tmp_data, monkeypatch):
    # 两次相同 snapshot → 第二次不产出
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
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
    async def capture_snapshot(self):
        s = self._waves[min(self.i, len(self._waves) - 1)]; self.i += 1; return s
    async def scroll_conversation_up(self):
        self.scroll_count += 1
        return True

def test_backfill_history_ingests_new_messages(tmp_data, monkeypatch):
    """3.7: 按需历史回溯 — 滚动加载更早消息并入库, 到顶后停止。"""
    import asyncio
    # BackfillCDP 的 capture_snapshot 直接返回消息列表, 让解析函数原样透传
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: s)
    cdp = BackfillCDP()
    store = FakeStore()
    sc = Scanner(cdp, store, FakeVector())
    n = asyncio.run(sc.backfill_history(chat_id="c1", max_scrolls=5))
    assert n == 2  # m1 + m2 (m1 第二波已存在, 不重复)
    assert cdp.scroll_count == 3  # 第 3 次滚动无新消息 → 到顶停止

def test_backfill_history_stops_when_no_panel(tmp_data, monkeypatch):
    """会话面板不存在时滚动返回 False, 立即停止, 入库 0。"""
    import asyncio
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: s)
    class NoPanelCDP(BackfillCDP):
        async def scroll_conversation_up(self): return False
    cdp = NoPanelCDP()
    sc = Scanner(cdp, FakeStore(), FakeVector())
    n = asyncio.run(sc.backfill_history(max_scrolls=5))
    assert n == 0


def test_fast_tick_writes_heartbeat_when_idle(tmp_data, monkeypatch):
    """空闲 (DOM 不变) 时也应写心跳, 避免 alive 误判死。"""
    import asyncio
    writes = []
    monkeypatch.setattr("app.collector.scanner.write_status", lambda path, st: writes.append(dict(st)))
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    asyncio.run(sc.fast_tick())   # 首次: hash 变化
    asyncio.run(sc.fast_tick())   # 空闲: 应仍写心跳
    assert len(writes) == 2


def test_upsert_matches_customer_once_per_chat(tmp_data, monkeypatch):
    """同一会话只匹配一次客户, 不重复建。"""
    calls = []
    monkeypatch.setattr(
        "app.profile.matcher.match_customer",
        lambda *a, **k: calls.append((a[3], a[4])) or {},
    )
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 1, "type": "chat", "name": "Alice"})
    sc._upsert_one({"id": "m2", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 2, "type": "chat", "name": "Alice"})
    sc._upsert_one({"id": "m3", "chatId": "c2", "fromMe": False, "from": "y",
                    "timestamp": 3, "type": "chat"})
    assert len(calls) == 2  # c1 一次, c2 一次
    assert calls[0] == ("Alice", "c1")
    assert calls[1] == (None, "c2")


def test_upsert_writes_chat_record(tmp_data):
    """消息入库时应同步写入会话元数据 (chats 表)。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 1, "type": "chat", "name": "Alice"})
    row = store.conn.execute("SELECT * FROM chats WHERE id='c1'").fetchone()
    assert row is not None
    assert row["account_id"] == "me"
    assert row["jid"] == "c1"
    assert row["display_name"] == "Alice"
    assert row["kind"] == "single"
    assert row["last_synced_at"] > 0


def test_upsert_chat_keeps_existing_name_when_unknown(tmp_data):
    """显示名缺失 (纯 DOM 增量) 时不得覆盖已有人工/已知名字。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 1, "type": "chat", "name": "Alice"})
    sc._upsert_one({"id": "m2", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 2, "type": "chat", "name": None})
    row = store.conn.execute("SELECT display_name FROM chats WHERE id='c1'").fetchone()
    assert row["display_name"] == "Alice"


def test_upsert_writes_chat_via_fake_store():
    """FakeStore 路径也应收到会话元数据。"""
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 1, "type": "chat", "name": "Alice"})
    assert len(sc.store.chats) == 1
    assert sc.store.chats[0].id == "c1"


async def test_slow_tick_ingests_idb_and_creates_customer(tmp_data, monkeypatch):
    """slow_tick 挂接: DOM+IDB 按 hex id 合并 → 消息入库 + 自动建客户画像。"""
    from app.storage.sqlite_store import SqliteStore
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe",
                        lambda s, chat_id=None: [{
                            "id": "3EB06C1E7DA73250B3B4", "fromMe": False, "from": None,
                            "timestamp": 0, "body": "Price please", "body_present": True}])
    async def fake_walk_idb(cdp, acct):
        return {
            "chats": {"8615976909619@c.us": "Sonya"},
            "contacts": {},
            "messages": [{"id": "false_8615976909619@c.us_3EB06C1E7DA73250B3B4",
                          "t": 1710000000, "from": "8615976909619@c.us",
                          "to": "8618963126542@c.us", "type": "chat", "fromMe": False}],
        }
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    store = SqliteStore()
    sc = Scanner(FakeCdp(), store, FakeVector())
    await sc.slow_tick()
    assert len(store.conn.execute("SELECT id FROM customers").fetchall()) == 1
    msg = store.conn.execute("SELECT chat_id, body FROM messages").fetchone()
    assert msg["chat_id"] == "8615976909619@c.us"
    assert msg["body"] == "Price please"


class _FakeRow:
    def __init__(self, page, i): self.page = page; self.i = i
    async def click(self, timeout=None): self.page.clicks.append(self.i)

class _FakeLocator:
    def __init__(self, page): self.page = page
    def nth(self, i): return _FakeRow(self.page, i)

class FakePage:
    def __init__(self, n_rows): self.n_rows = n_rows; self.clicks = []
    async def eval_on_selector_all(self, sel, expr): return self.n_rows
    def locator(self, sel): return _FakeLocator(self)


async def test_scan_all_chats_opens_each_chat(tmp_data, monkeypatch):
    """自动扫描: 逐会话打开读取, 全部入库。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    counter = [0]
    def fake_parse(s, chat_id=None):
        counter[0] += 1
        i = counter[0]
        return [{"id": f"HEX{i}", "fromMe": False, "from": None,
                 "timestamp": 0, "body": f"hello{i}", "body_present": True}]
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", fake_parse)
    async def fake_walk_idb(cdp, acct):
        return {
            "chats": {}, "contacts": {},
            "messages": [
                {"id": "false_8615976909619@c.us_HEX1", "t": 1001,
                 "from": "8615976909619@c.us", "to": "8618963126542@c.us", "type": "chat", "fromMe": False},
                {"id": "false_8616111222333@c.us_HEX2", "t": 1002,
                 "from": "8616111222333@c.us", "to": "8618963126542@c.us", "type": "chat", "fromMe": False},
                {"id": "false_8617333444555@c.us_HEX3", "t": 1003,
                 "from": "8617333444555@c.us", "to": "8618963126542@c.us", "type": "chat", "fromMe": False},
            ],
        }
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    page = FakePage(n_rows=3)
    sc = Scanner(FakeCdp(), store, FakeVector(), page=page)
    n = await sc.scan_all_chats(max_chats=3, settle=0)
    assert page.clicks == [0, 1, 2]  # 每个会话都被打开
    assert n == 3
    rows = store.conn.execute("SELECT chat_id FROM messages ORDER BY ts").fetchall()
    assert len(rows) == 3
    assert {r["chat_id"] for r in rows} == {"8615976909619@c.us", "8616111222333@c.us", "8617333444555@c.us"}


def test_scan_all_chats_no_page_returns_zero(tmp_data):
    sc = Scanner(FakeCDP([{}]), FakeStore(), FakeVector())
    assert asyncio.run(sc.scan_all_chats()) == 0


async def test_scan_all_chats_skips_avatar_when_no_messages(tmp_data, monkeypatch):
    """空 DOM 波次 (点击的会话无消息) 不得用上一会话 id 重复抓头像覆盖错误客户。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, "/avatars/cust1.png"))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("me", "8615976909619@c.us", "cust1", 0.9, 0, 0))
    store.conn.commit()

    waves = [
        [{"id": "HEX1", "fromMe": False, "from": None, "timestamp": 0,
          "body": "hello", "body_present": True}],  # 会话 1 有消息 → 应抓头像
        [],                                          # 会话 2 无 DOM 消息 → 不应再抓
    ]
    class WaveCdp:
        def __init__(self): self.i = 0
        async def capture_snapshot(self):
            s = waves[min(self.i, len(waves) - 1)]; self.i += 1; return s

    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe",
                        lambda s, chat_id=None: s)
    async def fake_walk_idb(cdp, acct):
        return {
            "chats": {"8615976909619@c.us": "Alice"},
            "contacts": {},
            "messages": [{"id": "false_8615976909619@c.us_HEX1", "t": 1001,
                          "from": "8615976909619@c.us", "to": "8618963126542@c.us",
                          "type": "chat", "fromMe": False}],
        }
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)

    class AvatarPage:
        def __init__(self, n_rows):
            self.n_rows = n_rows; self.clicks = []; self.calls = 0
        async def eval_on_selector_all(self, sel, expr): return self.n_rows
        def locator(self, sel): return _FakeLocator(self)
        async def evaluate(self, expr):
            self.calls += 1
            if self.calls == 1:
                return {"src": "blob:https://web.whatsapp.com/x"}
            png = b"\x89PNG\r\n\x1a\navatar2"
            return "data:image/png;base64," + __import__("base64").b64encode(png).decode()

    page = AvatarPage(n_rows=2)
    sc = Scanner(WaveCdp(), store, FakeVector(), page=page)
    n = await sc.scan_all_chats(max_chats=2, settle=0)
    assert n == 1  # 仅会话 1 的消息入库
    assert page.calls == 2  # 头像只抓一次 (第 2 波空 → 不再抓), 否则会重复抓=4 次
    row = store.conn.execute("SELECT avatar_path FROM customers WHERE id='cust1'").fetchone()
    assert row["avatar_path"] == "/avatars/cust1.png"


class FakeLLM:
    def generate(self, s, u, max_tokens=1024):
        return '{"country": "USA"}'


def test_upsert_schedules_profile_extraction(tmp_data):
    """匹配成功后把 (customer_id, chat_id) 加入画像抽取待处理队列。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector(), llm=FakeLLM())
    sc._upsert_one({"id": "m1", "chatId": "c1", "fromMe": False, "from": "x",
                    "timestamp": 1, "type": "chat", "name": "Alice"})
    assert len(sc._profile_pending) == 1
    cid, chat_id = next(iter(sc._profile_pending))
    assert chat_id == "c1"
    assert store.conn.execute("SELECT customer_id FROM customer_chat_map").fetchone()[0] == cid


def test_drain_profile_updates_runs_extractor(tmp_data, monkeypatch):
    """画像抽取在 executor 中执行并写入 profile 表。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("me", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "me", "c1", False, None, 1, "chat", "client from USA", True, 0))
    sc = Scanner(FakeCDP([{}]), store, FakeVector(), llm=FakeLLM())
    sc._profile_pending.add(("cust1", "c1"))
    asyncio.run(sc._drain_profile_updates())
    prof = {p.field: p.value for p in store.get_profile("cust1")}
    assert prof["country"] == "USA"


def test_capture_avatar_writes_file_and_path(tmp_data, monkeypatch):
    """打开会话后抓取头像: 文件落盘 + customers.avatar_path 更新。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("me", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    png = b"\x89PNG\r\n\x1a\nfakedata"
    data_url = "data:image/png;base64," + __import__("base64").b64encode(png).decode()
    class FakePage:
        def __init__(self): self.calls = 0
        async def evaluate(self, expr):
            self.calls += 1
            if self.calls == 1:   # 第一次调用: 读 header img src
                return {"src": "blob:https://web.whatsapp.com/xyz"}
            return data_url       # 第二次: fetch → dataURL
    sc = Scanner(FakeCDP([{}]), store, FakeVector(), page=FakePage())
    import asyncio
    asyncio.run(sc._capture_avatar("c1"))
    path = settings.avatars_dir / "cust1.png"
    assert path.read_bytes() == png
    row = store.conn.execute("SELECT avatar_path FROM customers WHERE id='cust1'").fetchone()
    assert row["avatar_path"] == "/avatars/cust1.png"


def test_capture_avatar_skips_when_no_customer(tmp_data):
    """无客户映射的会话不抓取, 不报错。"""
    from app.storage.sqlite_store import SqliteStore
    class FakePage:
        async def evaluate(self, expr): return {"src": "blob:x"}
    sc = Scanner(FakeCDP([{}]), SqliteStore(), FakeVector(), page=FakePage())
    import asyncio
    asyncio.run(sc._capture_avatar("no_map"))  # 不应抛异常
    av = settings.avatars_dir
    assert not av.exists() or not list(av.glob("*"))  # 未写任何头像文件


def test_drain_profile_updates_failure_does_not_block(tmp_data, monkeypatch):
    """LLM 失败时静默跳过, 不抛异常阻塞采集。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute(
        "INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
        ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute(
        "INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
        ("me", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    class BoomLLM:
        def generate(self, s, u, max_tokens=1024):
            raise RuntimeError("LLM 挂了")
    sc = Scanner(FakeCDP([{}]), store, FakeVector(), llm=BoomLLM())
    sc._profile_pending.add(("cust1", "c1"))
    asyncio.run(sc._drain_profile_updates())  # 不应抛异常
    assert sc._profile_pending == set()  # 失败项已消费, 不无限重试
