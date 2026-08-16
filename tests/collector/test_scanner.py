# tests/collector/test_scanner.py
import asyncio, time
from app.collector.scanner import (write_status, read_status, is_alive, Scanner,
                                   _aggregate_chat_previews)
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


def test_aggregate_chat_previews_max_unread_and_keeps_preview():
    """重名会话多行解析到同一 chat_id 时, 取未读最大值, 避免 unread=0 行覆盖真实未读。"""
    name_to_id = {"苏童": "8615071290277@c.us"}
    rows = [
        {"name": "苏童", "unread": 1, "preview": "我要一台"},
        {"name": "苏童", "unread": 0, "preview": None},
    ]
    out = _aggregate_chat_previews(rows, name_to_id)
    assert out == [{"chat_id": "8615071290277@c.us", "unread_count": 1, "preview": "我要一台"}]


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


def _group_data(**overrides):
    data = {
        "chats": {}, "contacts": {},
        "groups": {"120363123456789@g.us": {"name": "海外采购群",
                                            "members": {"8615976909619@c.us": "Sonya"}}},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [{"id": "false_120363123456789@g.us_ABC123", "t": 1710000000,
                      "from": "8615976909619@c.us", "to": "120363123456789@g.us",
                      "type": "chat", "fromMe": False}],
    }
    data.update(overrides)
    return data


def test_merge_idb_dom_group_uses_group_jid_and_sender_name():
    """群聊: chatId=群 JID, kind=group, sender_name 来自群成员表, name=群名。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    merged = sc._merge_idb_dom(_group_data(), dom)
    assert merged[0]["chatId"] == "120363123456789@g.us"
    assert merged[0]["kind"] == "group"
    assert merged[0]["sender_name"] == "Sonya"
    assert merged[0]["name"] == "海外采购群"


def test_merge_group_member_missing_falls_back_to_jid():
    """群成员名缺失 (contacts/成员表/DOM 均无) → sender_name 回退为原始 JID。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    data = _group_data(groups={"120363123456789@g.us": {"name": None, "members": {}}},
                       contacts={})
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["sender_name"] == "8615976909619@c.us"


def test_upsert_group_writes_kind_group_and_sender_name(tmp_data):
    """群聊消息入库: chats.kind=group + display_name=群名, messages.sender_name 随行。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "120363123456789@g.us", "fromMe": False,
                    "from": "8615976909619@c.us", "timestamp": 1, "type": "chat",
                    "name": "海外采购群", "sender_name": "Sonya"})
    row = store.conn.execute("SELECT * FROM chats WHERE id='120363123456789@g.us'").fetchone()
    assert row["kind"] == "group"
    assert row["display_name"] == "海外采购群"
    msg = store.list_messages("120363123456789@g.us")[0]
    assert msg.sender_name == "Sonya"


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


def test_merge_group_sender_lid_normalized_to_phone():
    """群成员表按手机号 JID 建键, 消息 from 为 @lid → 归一后解析成员名。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    data = _group_data(
        messages=[{"id": "false_120363123456789@g.us_ABC123", "t": 1710000000,
                   "from": "123456789@lid", "to": "120363123456789@g.us",
                   "type": "chat", "fromMe": False}],
        lid_to_phone={"123456789@lid": "8615976909619@c.us"},
        groups={"120363123456789@g.us": {"name": "海外采购群",
                                         "members": {"8615976909619@c.us": "Sonya"}}},
    )
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["sender_name"] == "Sonya"


def test_merge_group_sender_phone_normalized_to_lid():
    """群成员表按 LID 建键, 消息 from 为手机号 JID → 反向归一后解析成员名。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    data = _group_data(
        messages=[{"id": "false_120363123456789@g.us_ABC123", "t": 1710000000,
                   "from": "8615976909619@c.us", "to": "120363123456789@g.us",
                   "type": "chat", "fromMe": False}],
        lid_to_phone={"123456789@lid": "8615976909619@c.us"},
        groups={"120363123456789@g.us": {"name": "海外采购群",
                                         "members": {"123456789@lid": "Sonya"}}},
    )
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["sender_name"] == "Sonya"


def test_merge_idb_from_me_is_authoritative_over_dom_tail():
    """fromMe 冲突时 IDB 发送者==自身账号为权威 (覆盖 DOM tail-in 信号)。"""
    sc = Scanner(None, None, None)
    data = {
        "chats": {}, "contacts": {}, "groups": {},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [
            # 首条入站消息确立 our_jid (=to)
            {"id": "false_8615976909619@c.us_ABC000", "t": 1700000000,
             "from": "8615976909619@c.us", "to": "8618963126542@c.us",
             "type": "chat", "fromMe": False},
            # 出站消息: from == our_jid
            {"id": "false_8615976909619@c.us_ABC123", "t": 1710000000,
             "from": "8618963126542@c.us", "to": "8615976909619@c.us",
             "type": "chat", "fromMe": True},
        ],
    }
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hi", "body_present": True}]  # DOM tail-in 说 fromMe=False
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["fromMe"] is True  # IDB 权威覆盖


# ---- collector-settings-center: 手动扫描 (tasks 2.x) ----
async def test_scan_all_chats_reports_progress(tmp_data, monkeypatch):
    """2.1: scan_all_chats 每处理一个会话回调 on_progress(current, total, ingested)。"""
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
        return {"chats": {}, "contacts": {},
                "messages": [{"id": f"false_{i}11@c.us_HEX{i}", "t": i,
                              "from": f"{i}11@c.us", "to": "99@c.us", "type": "chat", "fromMe": False}
                             for i in (1, 2, 3)]}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    page = FakePage(n_rows=3)
    sc = Scanner(FakeCdp(), store, FakeVector(), page=page)
    progress = []
    await sc.scan_all_chats(max_chats=3, settle=0,
                            on_progress=lambda c, t, i: progress.append((c, t, i)))
    assert progress[0][0] == 0 and progress[0][1] == 3 and progress[0][2] == 0  # 扫描前 total 已知
    assert progress[-1] == (3, 3, 3)  # current/total/累计 ingested


async def test_scan_all_chats_respects_max_chats_cap(tmp_data, monkeypatch):
    """W3: 会话总数 > auto_scan_max_chats 时仅扫前上限个, 进度 total 如实为 min 值。"""
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
        return {"chats": {}, "contacts": {},
                "messages": [{"id": f"false_{i}11@c.us_HEX{i}", "t": i,
                              "from": f"{i}11@c.us", "to": "99@c.us", "type": "chat", "fromMe": False}
                             for i in (1, 2, 3)]}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    page = FakePage(n_rows=5)  # 5 个会话
    sc = Scanner(FakeCdp(), store, FakeVector(), page=page)
    progress = []
    await sc.scan_all_chats(max_chats=3, settle=0,
                            on_progress=lambda c, t, i: progress.append((c, t, i)))
    assert page.clicks == [0, 1, 2]  # 仅扫前 3 个
    assert progress[-1] == (3, 3, 3)  # total=min(5,3)=3
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3


class ScanPage:
    def __init__(self, n_rows=2):
        self.n_rows = n_rows; self.clicks = []
    async def eval_on_selector_all(self, sel, expr): return self.n_rows
    def locator(self, sel): return _FakeLocator(self)


async def test_drain_scan_requests_consumes_and_writes_status(tmp_data, monkeypatch):
    """2.2: 消费 pending 请求 → 执行扫描 → 进度/结果写 status.json → 标 done。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    req_id = store.create_scan_request()
    def fake_parse(s, chat_id=None):
        return [{"id": "HEX1", "fromMe": False, "from": None, "timestamp": 0,
                 "body": "hello", "body_present": True}]
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", fake_parse)
    async def fake_walk_idb(cdp, acct):
        return {"chats": {"111@c.us": "Alice"}, "contacts": {},
                "messages": [{"id": "false_111@c.us_HEX1", "t": 1,
                              "from": "111@c.us", "to": "99@c.us", "type": "chat", "fromMe": False}]}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    sc = Scanner(FakeCdp(), store, FakeVector(), page=ScanPage())
    await sc._drain_scan_requests()
    row = store.conn.execute("SELECT * FROM scan_requests WHERE id=?", (req_id,)).fetchone()
    assert row["done"] == 1 and row["status"] == "done"
    s = read_status(settings.status_path)
    assert s["scan"]["running"] is False and s["scan"]["done"] is True
    assert s["scan"]["ingested"] >= 0 and "finished_at" in s["scan"]
    assert store.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= 1


async def test_drain_scan_requests_sets_last_scan_skips_auto(tmp_data, monkeypatch):
    """2.3: 消费期间设置 last_scan=now → 自动周期扫描分支本轮跳过。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.create_scan_request()
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
    async def fake_walk_idb(cdp, acct): return {"chats": {}, "contacts": {}, "messages": []}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    sc = Scanner(FakeCdp(), store, FakeVector(), page=ScanPage())
    await sc._drain_scan_requests()
    assert time.time() - sc.last_scan < 5  # 已刷新


async def test_drain_scan_requests_failure_bumps_attempts(tmp_data, monkeypatch):
    """2.2: 扫描中途异常 → attempts+1 不标 done, <3 下轮可重试。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    req_id = store.create_scan_request()
    class BoomPage:
        async def eval_on_selector_all(self, sel, expr): raise RuntimeError("CDP 挂了")
        def locator(self, sel): raise RuntimeError("CDP 挂了")
    sc = Scanner(None, store, FakeVector(), page=BoomPage())
    await sc._drain_scan_requests()   # 不应抛异常
    row = store.conn.execute("SELECT * FROM scan_requests WHERE id=?", (req_id,)).fetchone()
    assert row["done"] == 0 and row["attempts"] == 1 and row["status"] == "failed"
    assert sc._manual_scan_active is False  # finally 已复位


async def test_drain_scan_requests_page_none_no_false_success(tmp_data, monkeypatch):
    """M2: 采集器无 page (不可扫描) 时不得标记 done 假成功, 应 bump attempts。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    req_id = store.create_scan_request()
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
    async def fake_walk_idb(cdp, acct): return {"chats": {}, "contacts": {}, "messages": []}
    monkeypatch.setattr("app.collector.idb_walk.walk_idb", fake_walk_idb)
    class FakeCdp:
        async def capture_snapshot(self): return {}
    sc = Scanner(FakeCdp(), store, FakeVector())  # page=None
    await sc._drain_scan_requests()
    row = store.conn.execute("SELECT * FROM scan_requests WHERE id=?", (req_id,)).fetchone()
    assert row["done"] == 0 and row["attempts"] == 1 and row["status"] == "failed"


async def test_scanner_rt_uses_runtime_settings_fast_tick(tmp_data):
    """2.4: Scanner._rt 经 RuntimeSettings 读取, DB 值覆盖 .env。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('fast_tick_sec','0.001',0)")
    store.conn.commit()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    assert sc._rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == 0.001


def test_scanner_rt_parse_failure_falls_back(tmp_data):
    """2.4: 脏配置 (非数值) → get_typed 回退 .env 默认, 采集器不崩。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO settings(key,value,updated_at) VALUES('slow_tick_sec','NaN',0)")
    store.conn.commit()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    assert sc._rt.get_typed("slow_tick_sec", settings.slow_tick_sec) == settings.slow_tick_sec


def test_fast_tick_keeps_scan_progress_when_scanning(tmp_data, monkeypatch):
    """I1: 扫描进行中 fast_tick 心跳不得清空 status.json 的 scan 进度字段。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._scan_runtime = {"running": True, "current": 3, "total": 10, "ingested": 5}
    sc._manual_scan_active = True
    monkeypatch.setattr("app.collector.scanner.parse_dom_snapshot_safe", lambda s, chat_id=None: [])
    import asyncio
    asyncio.run(sc.fast_tick())
    s = read_status(settings.status_path)
    assert s["scan"] == {"running": True, "current": 3, "total": 10, "ingested": 5}


# ---- F: _merge_idb_dom 归一化辅助函数单测 ----

def test_build_idb_index_extracts_hex_and_our_jid():
    from app.collector.scanner import _build_idb_index
    data = {"messages": [
        {"id": "false_me_abc123", "to": "me@c.us", "from": "cust@c.us"},
        {"id": "false_me_def456", "to": "me@c.us", "from": "cust@c.us"},
    ]}
    idx, our = _build_idb_index(data)
    assert our == "me@c.us"
    assert set(idx.keys()) == {"abc123", "def456"}


def test_build_idb_index_our_jid_prefers_fromme_sender():
    """our_jid 判定: fromMe=True 的 from=自己优先 (出站消息的 to=对方, 不能当作自己)。"""
    from app.collector.scanner import _build_idb_index
    data = {"messages": [
        {"id": "false_me_OUT1", "to": "cust@c.us", "from": "me@c.us", "fromMe": True},
        {"id": "false_cust_IN1", "to": "me@c.us", "from": "cust@c.us", "fromMe": False},
    ]}
    idx, our = _build_idb_index(data)
    assert our == "me@c.us"  # 不是 cust@c.us


def test_build_idb_index_our_jid_skips_group_to():
    """our_jid 判定: 群消息的 to=群 JID 不能当作自己。"""
    from app.collector.scanner import _build_idb_index
    data = {"messages": [
        {"id": "false_grp_G1", "to": "grp@g.us", "from": "cust@c.us", "fromMe": False},
        {"id": "false_me_OUT1", "to": "cust@c.us", "from": "me@c.us", "fromMe": True},
    ]}
    idx, our = _build_idb_index(data)
    assert our == "me@c.us"


def test_merge_sent_message_attributed_to_me():
    """网页发送的消息 fromMe 归属: 即便 IDB 首条为出站 (旧启发式会把 our_jid 误判为对方),
    也要用 IDB 记录的 fromMe 归属为我。"""
    sc = Scanner(None, None, None)
    data = {
        "chats": {}, "contacts": {}, "groups": {},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [
            {"id": "false_me_OUT1", "t": 1000, "from": "me@c.us", "to": "cust@c.us",
             "type": "chat", "fromMe": True},
            {"id": "false_cust_IN1", "t": 900, "from": "cust@c.us", "to": "me@c.us",
             "type": "chat", "fromMe": False},
        ],
    }
    dom = [{"id": "OUT1", "fromMe": True, "from": None, "timestamp": 1000,
            "body": "我发的", "body_present": True}]
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["fromMe"] is True
    assert merged[0]["chatId"] == "cust@c.us"


def test_resolve_chat_private_and_group():
    from app.collector.scanner import _resolve_chat
    # 私聊入站: from 非我方 → chat=from
    assert _resolve_chat({"from": "cust@c.us", "to": "me@c.us"}, "me@c.us", None) == "cust@c.us"
    # 私聊出站: from 是我方 → chat=to
    assert _resolve_chat({"from": "me@c.us", "to": "cust@c.us"}, "me@c.us", None) == "cust@c.us"
    # 群聊: chat=群 JID
    assert _resolve_chat({"from": "cust@c.us", "to": "grp@g.us"}, "me@c.us", None) == "grp@g.us"
    # 无 rec: 回退当前会话
    assert _resolve_chat(None, "me@c.us", "cur@c.us") == "cur@c.us"


def test_resolve_phone_chat_lid_to_phone():
    from app.collector.scanner import _resolve_phone_chat
    assert _resolve_phone_chat("123@lid", {"123@lid": "8613800000000"}, {}) == "8613800000000"
    assert _resolve_phone_chat("123@lid", {}, {"123@lid": "8613800000000"}) == "8613800000000"
    assert _resolve_phone_chat("8613800000000@c.us", {}, {}) == "8613800000000@c.us"


def test_resolve_chat_name_priority():
    from app.collector.scanner import _resolve_chat_name
    groups = {"g@g.us": {"name": "群名"}}
    chats = {"c@c.us": "会话名"}
    contacts = {"c@c.us": "联系人名"}
    # 群名优先
    assert _resolve_chat_name("g@g.us", "g@g.us", groups, chats, contacts, "回退") == "群名"
    # chats 次之
    assert _resolve_chat_name("c@c.us", "c@c.us", {}, chats, contacts, "回退") == "会话名"
    # contacts 再次
    assert _resolve_chat_name("c@c.us", "c@c.us", {}, {}, contacts, "回退") == "联系人名"
    # 全缺 → DOM 回退
    assert _resolve_chat_name("x@c.us", "x@c.us", {}, {}, {}, "回退") == "回退"


def test_resolve_sender_name_contacts_then_dom():
    from app.collector.scanner import _resolve_sender_name
    rec = {"from": "cust@c.us"}
    contacts = {"cust@c.us": "Alice"}
    # contacts 命中
    assert _resolve_sender_name(rec, False, "cust@c.us", contacts, {}, None, {},
                                {}, {}, {}) == "Alice"
    # contacts 未命中 → DOM 回退
    assert _resolve_sender_name(rec, False, "cust@c.us", {}, {}, None, {"from": "Bob"},
                                {}, {}, {}) == "Bob"
    # 出站消息 (from_me) → None
    assert _resolve_sender_name(rec, True, "cust@c.us", contacts, {}, None, {}, {}, {}, {}) is None


def test_merge_idb_dom_prefers_idb_t_over_dom_minute_ts():
    """时间戳: IDB 秒级 m.t 优先于 DOM 分钟级 timestamp, 避免同分钟多条消息排序错乱。"""
    sc = Scanner(None, None, None)
    data = {
        "chats": {}, "contacts": {}, "groups": {},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [
            {"id": "false_me_HEX1", "t": 1786883467, "from": "cust@c.us",
             "to": "me@c.us", "type": "chat", "fromMe": False},
        ],
    }
    dom = [{"id": "HEX1", "fromMe": False, "from": None, "timestamp": 1786883460,
            "body": "hi", "body_present": True}]
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["timestamp"] == 1786883467  # IDB 秒级优先


def test_reconcile_idb_metadata_upgrades_from_me_and_ts(tmp_data):
    """IDB 权威纠偏: from_me 0→1 (网页发送被 DOM 误判) 且 ts 用秒级精确值覆盖分钟截断值。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("HEX1", "me", "c1", False, None, 1786883460,
                                 "chat", "我发的", True, 0, "Sonya"))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    data = {"messages": [
        {"id": "false_me_HEX1", "t": 1786883467, "from": "me@c.us",
         "to": "cust@c.us", "type": "chat", "fromMe": True},
    ]}
    changed = sc._reconcile_idb_metadata(data)
    assert changed == 1
    row = store.conn.execute(
        "SELECT from_me, ts, sender_name FROM messages WHERE id='HEX1'").fetchone()
    assert row["from_me"] == 1
    assert row["ts"] == 1786883467
    assert row["sender_name"] is None  # 我方消息清掉残留发送人名


def test_reconcile_idb_metadata_skips_unmatched(tmp_data):
    """IDB 无对应 hex 的消息保持不变 (不做臆测纠偏)。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("UNKNOWN", "me", "c1", False, None, 1786883460,
                                 "chat", "x", True, 0, "Sonya"))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    changed = sc._reconcile_idb_metadata({"messages": [
        {"id": "false_me_HEX9", "t": 1786883467, "from": "me@c.us",
         "to": "cust@c.us", "type": "chat", "fromMe": True},
    ]})
    assert changed == 0
    row = store.conn.execute(
        "SELECT from_me, ts, sender_name FROM messages WHERE id='UNKNOWN'").fetchone()
    assert row["from_me"] == 0
    assert row["ts"] == 1786883460
    assert row["sender_name"] == "Sonya"


def test_reconcile_idb_metadata_normalizes_millis_ts(tmp_data):
    """IDB m.t 若为毫秒 (13 位) 归一为秒, 防止与秒级 DOM ts 混排。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("HEX1", "me", "c1", False, None, 0,
                                 "chat", "x", True, 0))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._reconcile_idb_metadata({"messages": [
        {"id": "false_me_HEX1", "t": 1786883467000, "from": "me@c.us",
         "to": "cust@c.us", "type": "chat", "fromMe": False},
    ]})
    row = store.conn.execute("SELECT ts FROM messages WHERE id='HEX1'").fetchone()
    assert row["ts"] == 1786883467


def test_reconcile_idb_metadata_moves_message_to_authoritative_chat(tmp_data):
    """chat_id 纠偏: 消息被 fast_tick 误写进错误会话, IDB msgKey 的 chatJid 把它搬回正确会话。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("HEX1", "me", "447974905044@c.us", False, None,
                                 1786886970, "chat", "长续航版", True, 0, "苏童"))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    changed = sc._reconcile_idb_metadata({"messages": [
        {"id": "false_8615071290277@c.us_HEX1", "t": 1786886973, "from": None,
         "to": None, "type": "chat", "fromMe": False, "chatJid": "8615071290277@c.us"},
    ]})
    assert changed == 1
    row = store.conn.execute(
        "SELECT chat_id, ts FROM messages WHERE id='HEX1'").fetchone()
    assert row["chat_id"] == "8615071290277@c.us"
    assert row["ts"] == 1786886973


def test_reconcile_idb_metadata_lid_chatjid_normalized(tmp_data):
    """chatJid 为 @lid 时经 lid_to_phone 归一为 @c.us 再搬会话。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("HEX1", "me", "447974905044@c.us", False, None,
                                 1786886970, "chat", "x", True, 0, None))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._reconcile_idb_metadata({
        "messages": [{"id": "false_31916774395984@lid_HEX1", "t": 1786886973,
                      "from": None, "to": None, "type": "chat", "fromMe": False,
                      "chatJid": "31916774395984@lid"}],
        "lid_to_phone": {"31916774395984@lid": "8615071290277@c.us"},
        "phone_by_lid": {},
    })
    row = store.conn.execute("SELECT chat_id FROM messages WHERE id='HEX1'").fetchone()
    assert row["chat_id"] == "8615071290277@c.us"


def test_reconcile_idb_metadata_no_chatjid_keeps_chat(tmp_data):
    """无 chatJid 时不搬会话 (只纠正 from_me/ts), 避免臆测搬错。"""
    from app.storage.sqlite_store import SqliteStore
    from app.storage.interfaces import Message
    store = SqliteStore()
    store.upsert_message(Message("HEX1", "me", "c1", False, None, 1786886970,
                                 "chat", "x", True, 0, "苏童"))
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._reconcile_idb_metadata({"messages": [
        {"id": "false_me_HEX1", "t": 1786886973, "from": None, "to": None,
         "type": "chat", "fromMe": True},  # 无 chatJid
    ]})
    row = store.conn.execute(
        "SELECT chat_id, from_me FROM messages WHERE id='HEX1'").fetchone()
    assert row["chat_id"] == "c1"
    assert row["from_me"] == 1


def test_merge_idb_dom_prefers_chatjid_over_resolve_chat():
    """合并时 chatId 优先取 msgKey 的 chatJid (权威), 而非 from/to 启发式 + current_chat_id。"""
    sc = Scanner(None, None, None)
    data = {
        "chats": {}, "contacts": {}, "groups": {},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [{"id": "false_8615071290277@c.us_ABC123", "t": 1710000000,
                      "from": "8615071290277@c.us", "to": "me@c.us",
                      "type": "chat", "fromMe": False, "chatJid": "8615071290277@c.us"}],
    }
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hi", "body_present": True}]
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["chatId"] == "8615071290277@c.us"
