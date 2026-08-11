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
