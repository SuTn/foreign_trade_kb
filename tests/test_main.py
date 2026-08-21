# tests/test_main.py
import asyncio

import pytest


def test_main_importable():
    import app.__main__
    assert hasattr(app.__main__, "main")


def test_scanner_accepts_pw_and_context():
    """Scanner.__init__ 支持 pw/context 传参 (供 _reconnect 关闭旧浏览器)。"""
    from app.collector.scanner import Scanner
    pw, ctx = object(), object()
    sc = Scanner(None, None, None, pw=pw, context=ctx)
    assert sc._pw is pw
    assert sc._context is ctx


def test_collector_exits_1_on_runtime_error(monkeypatch):
    """采集器运行异常 → sys.exit(1), supervisor 据此重启。"""
    import app.collector.__main__ as cm

    async def boom(*a, **k):
        raise RuntimeError("cdp broken")

    monkeypatch.setattr(cm, "launch_browser", boom)
    monkeypatch.setattr(cm, "write_status", lambda *a, **k: None)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(cm.main())
    assert ei.value.code == 1


def test_collector_exits_0_on_keyboard_interrupt(monkeypatch):
    """用户中断 → sys.exit(0), supervisor 不重启。"""
    import app.collector.__main__ as cm

    async def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cm, "launch_browser", boom)
    monkeypatch.setattr(cm, "write_status", lambda *a, **k: None)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(cm.main())
    assert ei.value.code == 0


def test_collector_passes_pw_context_to_scanner(monkeypatch):
    """__main__ 把 pw/context 传给 Scanner (Task 1 遗留: _reconnect 需关旧浏览器)。"""
    import app.collector.__main__ as cm

    captured = {}

    class FakeScanner:
        def __init__(self, *a, **k):
            captured["pw"] = k.get("pw")
            captured["context"] = k.get("context")

        async def run(self):
            pass

    async def fake_launch():
        return ("PW", "CTX", "PAGE", "CDP")

    async def fake_wait_login(page, stop_event=None):
        return True

    monkeypatch.setattr(cm, "Scanner", FakeScanner)
    monkeypatch.setattr(cm, "launch_browser", fake_launch)
    monkeypatch.setattr(cm, "wait_for_login", fake_wait_login)
    monkeypatch.setattr(cm, "write_status", lambda *a, **k: None)
    monkeypatch.setattr(cm, "SqliteStore", lambda: None)
    monkeypatch.setattr(cm, "ChromaStore", lambda **k: None)
    monkeypatch.setattr(cm, "get_embedding", lambda: type("E", (), {"embed": lambda self, s: None})())
    monkeypatch.setattr(cm, "CloudLLM", lambda: None)
    asyncio.run(cm._run())
    assert captured == {"pw": "PW", "context": "CTX"}


def test_supervise_restarts_on_nonzero_then_breaks_on_zero(monkeypatch):
    """supervisor: 非 0 退出等 3s 重启, rc==0 正常退出不重启。

    P0 改造后: 采集器改为同进程线程 (launcher.collector_runner.CollectorHandle),
    不再用 subprocess。此测试验证新 supervisor 逻辑: 异常退出后重启, stop 后停止。
    """
    from launcher.collector_runner import CollectorHandle
    import launcher.collector_runner as cr

    calls = {"n": 0}

    async def fake_run(stop_event=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        # 第 3 次正常返回 (模拟正常退出)

    monkeypatch.setattr(cr, "_run_collector_coro", fake_run)
    handle = CollectorHandle()
    # 用短 sleep 加速测试
    monkeypatch.setattr(handle, "_stop", type("E", (), {"is_set": lambda self: calls["n"] >= 3,
                                                        "wait": lambda self, s: None})())
    handle._supervise()
    assert calls["n"] == 3


def test_collector_stop_signals_event_loop(monkeypatch):
    """stop() 通过 call_soon_threadsafe 置位 asyncio.Event, 让采集器主循环尽快退出。

    验证: stop() 不再仅依赖 daemon 线程, 而是真正通知事件循环停止。
    """
    from launcher.collector_runner import CollectorHandle
    import launcher.collector_runner as cr

    handle = CollectorHandle()
    # 模拟一个正在运行的事件循环 + 停止信号
    loop = type("L", (), {"call_soon_threadsafe": lambda self, fn: fn()})()
    stop_event = asyncio.Event()
    handle._loop = loop
    handle._stop_event = stop_event
    # 模拟线程已结束 (避免 join 阻塞)
    handle._thread = type("T", (), {"is_alive": lambda self: False,
                                    "join": lambda self, timeout=None: None})()
    handle.stop()
    assert stop_event.is_set()  # 停止信号已置位


def test_supervise_stale_generation_does_not_restart(monkeypatch):
    """重启竞态防护 (#12): 旧线程被新 start() 取代后 (代数不匹配), 即使 _stop 被清除
    也不应复活重启采集器, 避免新旧采集器并发写库。"""
    from launcher.collector_runner import CollectorHandle
    import launcher.collector_runner as cr

    calls = {"n": 0}

    async def fake_run(stop_event=None):
        calls["n"] += 1
        # 正常返回 (模拟采集器主循环退出)

    monkeypatch.setattr(cr, "_run_collector_coro", fake_run)
    handle = CollectorHandle()
    # 模拟: 旧线程代数=1, 但当前代数已被 start() 推进到 2 (被取代)
    handle._generation = 2
    # _stop 未置位 (模拟 start() 已 clear), 但代数不匹配 → 直接退出, 不运行采集器
    handle._supervise(gen=1)
    assert calls["n"] == 0  # 代数不匹配, 旧线程不复活运行采集器


def test_start_waits_for_old_thread_before_new(monkeypatch):
    """start() 在旧线程未退出时先等待其结束, 再启动新线程 (避免并发写库)。"""
    from launcher.collector_runner import CollectorHandle
    import launcher.collector_runner as cr

    joined = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=False, name=""):
            self._target = target
            self._args = args
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            joined.append(timeout)
            self._alive = False  # 模拟线程结束

        def start(self):
            pass

    monkeypatch.setattr(cr.threading, "Thread", FakeThread)
    handle = CollectorHandle()
    # 预置一个"仍在运行"的旧线程
    handle._thread = FakeThread(None, ())
    handle._stop_event = None
    handle._loop = None
    handle.start()
    assert joined  # 等待了旧线程
    assert handle._generation == 1  # 代数已递增


def test_scanner_breaks_on_stop_event(monkeypatch):
    """Scanner.run 主循环检测到 stop_event 置位后退出 (不再无限循环)。"""
    import asyncio
    from app.collector.scanner import Scanner

    stop_event = asyncio.Event()
    sc = Scanner(None, None, None, stop_event=stop_event)
    # 置位停止信号: run() 第一轮检查即 break, 不会调用任何 tick
    stop_event.set()
    # 若 stop_event 未生效, run() 会进入 while True 并调用 fast_tick (此处抛异常暴露)
    async def boom(*a, **k):
        raise AssertionError("stop_event 未生效, 主循环未退出")
    monkeypatch.setattr(Scanner, "fast_tick", boom)
    monkeypatch.setattr(Scanner, "slow_tick", boom)
    # run() 应因 stop_event 置位立即返回
    asyncio.run(sc.run())


def test_main_kills_collector_tree_on_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt → 停止采集器线程 (CollectorHandle.stop)。"""
    import app.__main__ as m
    import launcher.__main__ as lm

    stopped = []

    class FakeHandle:
        def stop(self):
            stopped.append(True)

    # run_web_and_collector 在 launcher.__main__ 命名空间引用 start_collector / _has_model_key
    monkeypatch.setattr(lm, "start_collector", lambda: FakeHandle())
    monkeypatch.setattr(lm, "_has_model_key", lambda: True)
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    m.main()
    assert stopped == [True]
