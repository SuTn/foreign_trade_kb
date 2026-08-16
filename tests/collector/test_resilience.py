# tests/collector/test_resilience.py
import asyncio
from app.collector.scanner import Scanner
from app.config import settings


class ScriptedCDP:
    """按脚本回放 CDP 调用: Exception 抛错, dict 作快照返回。"""
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    async def capture_snapshot(self):
        a = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        if isinstance(a, Exception):
            raise a
        return a


class ReconnectableStore:
    def __init__(self):
        self.msgs = []

    def upsert_message(self, m):
        self.msgs.append(m)

    def upsert_chat(self, c):
        pass


class NoopVector:
    def upsert_message_vector(self, *a, **k):
        pass


class _StopLoop(Exception):
    pass


def test_run_survives_transient_then_reconnects_and_continues(tmp_data, monkeypatch):
    """主循环: 瞬时异常不退出且不计致命; 连续 3 次致命触发重建; 重建后循环继续。"""
    old_tick, old_jit = settings.fast_tick_sec, settings.fast_tick_jitter
    settings.fast_tick_sec = 0.0
    settings.fast_tick_jitter = 0.0
    real_sleep = asyncio.sleep
    sleeps = [0]

    async def counting_sleep(delay):
        sleeps[0] += 1
        if sleeps[0] >= 14:  # 3 瞬时 + 3 致命 + 2 成功 = 8 轮 (错误轮含退避+尾两处 sleep)
            raise _StopLoop()
        await real_sleep(0)

    monkeypatch.setattr("app.collector.scanner.asyncio.sleep", counting_sleep)
    try:
        actions = ([ConnectionError("network timeout")] * 3
                   + [RuntimeError("Target closed: page crashed")] * 3
                   + [{}] * 2)
        cdp = ScriptedCDP(actions)
        sc = Scanner(cdp, ReconnectableStore(), NoopVector())
        reconnects = [0]

        async def fake_reconnect():
            reconnects[0] += 1
            sc._cdp_failures = 0

        sc._reconnect = fake_reconnect
        try:
            asyncio.run(sc.run())
        except _StopLoop:
            pass
        assert reconnects[0] == 1  # 连续 3 次致命恰好触发一次重建
        assert sc._cdp_failures == 0  # 重建后计数归零
        assert sc._last_dom_hash is not None  # 重建后成功快照, 循环继续
    finally:
        settings.fast_tick_sec = old_tick
        settings.fast_tick_jitter = old_jit


def test_run_once_transient_errors_do_not_count_as_fatal(tmp_data):
    """瞬时异常: 归零计数, 不触发重建。"""
    cdp = ScriptedCDP([ConnectionError("network timeout")] * 5)
    sc = Scanner(cdp, ReconnectableStore(), NoopVector())
    reconnects = [0]

    async def fake_reconnect():
        reconnects[0] += 1

    sc._reconnect = fake_reconnect
    for _ in range(5):
        asyncio.run(sc._run_once())
    assert sc._cdp_failures == 0
    assert reconnects[0] == 0


def test_run_once_three_fatal_failures_trigger_reconnect(tmp_data):
    """连续 3 次致命失败: 恰好触发一次重建, 计数归零。"""
    cdp = ScriptedCDP([RuntimeError("Target closed: page crashed")] * 3)
    sc = Scanner(cdp, ReconnectableStore(), NoopVector())
    reconnects = [0]

    async def fake_reconnect():
        reconnects[0] += 1
        sc._cdp_failures = 0

    sc._reconnect = fake_reconnect
    for _ in range(3):
        asyncio.run(sc._run_once())
    assert reconnects[0] == 1
    assert sc._cdp_failures == 0


def test_run_once_transient_between_fatals_resets_count(tmp_data):
    """瞬时异常夹在致命失败之间: 计数归零, 需要重新累积 3 次才重建。"""
    actions = ([RuntimeError("Target closed: page crashed")] * 2
               + [ConnectionError("network timeout")]
               + [RuntimeError("Target closed: page crashed")] * 2)
    cdp = ScriptedCDP(actions)
    sc = Scanner(cdp, ReconnectableStore(), NoopVector())
    reconnects = [0]

    async def fake_reconnect():
        reconnects[0] += 1
        sc._cdp_failures = 0

    sc._reconnect = fake_reconnect
    for _ in range(5):
        asyncio.run(sc._run_once())
    assert sc._cdp_failures == 2  # 2 致命后瞬时节流, 再 2 致命 = 2, 未到 3
    assert reconnects[0] == 0


def test_message_vector_key_is_per_message():
    """同会话同日多条消息使用独立向量键, 互不覆盖; 无 msg_id 回退 (chatId, day, ts) 含时间戳防碰撞。"""
    from app.collector.scanner import _msg_vector_key
    assert _msg_vector_key("c1", "m1", 1700000000) != _msg_vector_key("c1", "m2", 1700000000)
    assert _msg_vector_key("c1", "m1", 1700000000).startswith("c1:")
    assert _msg_vector_key("c1", "m1", 1700000000) == "c1:m1"
    assert _msg_vector_key("c1", "", 1700000000) == _msg_vector_key("c1", None, 1700000000)
    # 无 msg_id 时含时间戳, 同日不同 ts 不碰撞
    assert _msg_vector_key("c1", None, 1700000000) != _msg_vector_key("c1", None, 1700000001)
    assert _msg_vector_key("c1", None, 1700000000) == f"c1:{__import__('time').strftime('%Y-%m-%d', __import__('time').gmtime(1700000000))}:1700000000"
    assert _msg_vector_key("c1", None, 0) == "c1:unknown:0"


def test_upsert_uses_per_message_vector_key(tmp_data):
    """_upsert_one 向量键改为 per-message; metadata 保持 chat_id/day。
    C1: 向量化入队, 由 _flush_vectors_sync 后台消费。"""
    from app.collector.scanner import Scanner
    seen = []

    class RecVector:
        def upsert_message_vector(self, key, text, metadata):
            seen.append((key, text, metadata))

    class RecStore:
        def upsert_chat(self, c):
            pass
        def upsert_message(self, m):
            pass

    sc = Scanner(None, RecStore(), RecVector())
    sc._upsert_one({"chatId": "c1", "id": "m1", "fromMe": False, "body": "hello",
                    "timestamp": 1700000000, "type": "chat", "body_present": True})
    # 向量化已入队, 尚未消费
    assert len(sc._vector_pending) == 1
    assert sc._vector_pending[0][0] == "c1:m1"
    assert sc._vector_pending[0][2] == {"chat_id": "c1", "day": "2023-11-14"}
    # 后台消费后写入向量库
    sc._flush_vectors_sync()
    assert seen and seen[0][0] == "c1:m1"
    assert seen[0][2] == {"chat_id": "c1", "day": "2023-11-14"}
    assert sc._vector_pending == []


def test_drain_vectors_flushes_in_executor(tmp_data):
    """C1: _drain_vectors 把待向量化队列交给 executor 消费, 不阻塞事件循环。"""
    import asyncio
    from app.collector.scanner import Scanner
    seen = []

    class RecVector:
        def upsert_message_vector(self, key, text, metadata):
            seen.append((key, text, metadata))

    class RecStore:
        def upsert_chat(self, c):
            pass
        def upsert_message(self, m):
            pass

    sc = Scanner(None, RecStore(), RecVector())
    sc._vector_pending.append(("c1:m1", "hello", {"chat_id": "c1", "day": "2023-11-14"}))
    sc._vector_pending.append(("c1:m2", "world", {"chat_id": "c1", "day": "2023-11-14"}))
    asyncio.run(sc._drain_vectors())
    assert len(seen) == 2
    assert sc._vector_pending == []


def test_drain_vectors_skips_when_empty(tmp_data):
    """C1: 队列为空时 _drain_vectors 直接返回, 不启动 executor。"""
    import asyncio
    from app.collector.scanner import Scanner
    sc = Scanner(None, None, None)
    asyncio.run(sc._drain_vectors())  # 不应抛异常


def test_clear_message_vectors_only_msg_col(tmp_data):
    from app.storage.chroma_store import ChromaStore
    vs = ChromaStore(embedding_fn=lambda t: [0.0] * 8)
    vs.upsert_message_vector("k1", "msg", {"chat_id": "c1"})
    vs.upsert_chunks([{"id": "ch1", "text": "chunk", "metadata": {"doc_id": "d1"}}])
    vs.clear_message_vectors()
    assert vs.msg_col.count() == 0
    assert vs.chunk_col.count() == 1


def test_run_does_not_clear_message_vectors(tmp_data, monkeypatch):
    """审计修复: 采集器重启不再清空 message_vectors。向量按 chat_id:msg_id 幂等 upsert,
    重启时保留历史向量, 新消息增量追加, 避免重启后语义召回大面积缺失。"""
    from app.collector.scanner import Scanner
    clears = [0]

    class RecChroma:
        def clear_message_vectors(self):
            clears[0] += 1

    class Cdp:
        def __init__(self, n):
            self.n = n
        async def capture_snapshot(self):
            self.n -= 1
            return {}

    real_sleep = asyncio.sleep

    async def counting_sleep(delay):
        raise _StopLoop()

    monkeypatch.setattr("app.collector.scanner.asyncio.sleep", counting_sleep)
    sc = Scanner(Cdp(1), ReconnectableStore(), RecChroma())
    try:
        asyncio.run(sc.run())
    except _StopLoop:
        pass
    assert clears[0] == 0


def test_is_cdp_fatal_matches_disconnect_keywords():
    sc = Scanner(None, None, None)
    fatal = ("Target closed: page crashed", "connection reset", "Session closed.",
             "Protocol error", "Page crashed", "Execution context was destroyed",
             "Browser has been disconnected")
    for msg in fatal:
        assert sc._is_cdp_fatal(Exception(msg)), msg
    non_fatal = ("network timeout", "EOF", "parse error", "http 429")
    for msg in non_fatal:
        assert not sc._is_cdp_fatal(Exception(msg)), msg


def test_is_cdp_fatal_matches_browser_closed_keywords():
    """Playwright 真实浏览器崩溃消息 (Target page, context or browser has been closed) 判致命。"""
    sc = Scanner(None, None, None)
    assert sc._is_cdp_fatal(Exception("Browser has been closed"))
    assert sc._is_cdp_fatal(Exception("Target page, context or browser has been closed"))
    assert sc._is_cdp_fatal(Exception("Browser has been disconnected"))


def test_reconnect_rebuilds_browser_and_resets_state(tmp_data, monkeypatch):
    """重建: 关闭旧浏览器, 重建 pw/context/page/cdp, 重置会话状态。"""
    async def fake_launch():
        return (object(), object(), object(), object())

    monkeypatch.setattr("app.collector.browser.launch_browser", fake_launch)
    sc = Scanner(None, None, None)
    sc._pw = sc._context = object()
    sc._current_chat_id = "c1"
    sc._last_dom_hash = "abc"
    sc._cdp_failures = 2
    sc._matched_chats = {"c1"}
    asyncio.run(sc._reconnect())
    assert sc._current_chat_id is None
    assert sc._last_dom_hash is None
    assert sc._cdp_failures == 0
    assert sc._matched_chats == set()
    assert sc.page is not None and sc.cdp is not None
    assert sc._pw is not None and sc._context is not None


class NoScrollCDP:
    async def scroll_conversation_up(self):
        return False


class BoomScrollCDP:
    async def scroll_conversation_up(self):
        raise RuntimeError("scroll failed")


def _mem_store():
    """内存 SQLite store: conn 含 backfill_requests 表 (含 attempts 列)。"""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE backfill_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, "
        "max_scrolls INTEGER, requested_at INTEGER, done INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0)")
    conn.commit()
    store = ReconnectableStore()
    store.conn = conn
    return store


def _broken_conn_store():
    """conn.execute 一律抛错, 模拟 backfill_requests 表缺失。"""
    store = ReconnectableStore()
    store.conn = type("C", (), {"execute": lambda self, q, p=None: (_ for _ in ()).throw(Exception("no such table"))})()
    return store


def _insert_backfill(store, chat_id="c1", max_scrolls=1, attempts=0):
    store.conn.execute(
        "INSERT INTO backfill_requests(chat_id, max_scrolls, requested_at, attempts) VALUES(?,?,?,?)",
        (chat_id, max_scrolls, 0, attempts))
    store.conn.commit()


def test_drain_backfill_table_missing_no_error(tmp_data):
    """backfill_requests 表缺失时轮询不抛错 (已探测则静默返回)。"""
    scanner = Scanner(ScriptedCDP([{}]), _broken_conn_store(), NoopVector())
    scanner._backfill_table_checked = True  # 跳过探测
    asyncio.run(scanner._drain_backfill_requests())


def test_drain_backfill_probe_missing_table_sets_checked(tmp_data):
    """首次调用探测表存在性: 缺失则置 _backfill_table_checked 并静默返回。"""
    scanner = Scanner(ScriptedCDP([{}]), _broken_conn_store(), NoopVector())
    assert scanner._backfill_table_checked is False
    asyncio.run(scanner._drain_backfill_requests())
    assert scanner._backfill_table_checked is True


def test_drain_backfill_success_marks_done(tmp_data):
    """成功回溯: done 置 1, attempts 不变。"""
    store = _mem_store()
    _insert_backfill(store)
    scanner = Scanner(NoScrollCDP(), store, NoopVector())
    scanner._backfill_table_checked = True
    asyncio.run(scanner._drain_backfill_requests())
    r = store.conn.execute("SELECT done, attempts FROM backfill_requests").fetchone()
    assert r["done"] == 1 and r["attempts"] == 0


def test_drain_backfill_failure_increments_attempts(tmp_data):
    """回溯失败: attempts+1, done 保持 0。"""
    store = _mem_store()
    _insert_backfill(store)
    scanner = Scanner(BoomScrollCDP(), store, NoopVector())
    scanner._backfill_table_checked = True
    asyncio.run(scanner._drain_backfill_requests())
    r = store.conn.execute("SELECT done, attempts FROM backfill_requests").fetchone()
    assert r["done"] == 0 and r["attempts"] == 1


def test_drain_backfill_skips_rows_at_attempts_limit(tmp_data):
    """attempts>=3 的行不再被选取, 保持待处理状态。"""
    store = _mem_store()
    _insert_backfill(store, attempts=3)
    scanner = Scanner(NoScrollCDP(), store, NoopVector())
    scanner._backfill_table_checked = True
    asyncio.run(scanner._drain_backfill_requests())
    r = store.conn.execute("SELECT done, attempts FROM backfill_requests").fetchone()
    assert r["done"] == 0 and r["attempts"] == 3


def test_drain_backfill_no_debug_walk_side_effect(tmp_data):
    """死代码块已删: drain 不写 debug_walk.json, 也不因未定义 data 抛错。"""
    store = _mem_store()
    scanner = Scanner(NoScrollCDP(), store, NoopVector())
    scanner._backfill_table_checked = True
    asyncio.run(scanner._drain_backfill_requests())
    assert not (settings.data_dir / "debug_walk.json").exists()
