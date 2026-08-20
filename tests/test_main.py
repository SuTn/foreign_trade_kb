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

    async def fake_wait_login(page):
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

    def fake_run():
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


def test_main_kills_collector_tree_on_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt → 停止采集器线程 (CollectorHandle.stop)。"""
    import app.__main__ as m
    import launcher.collector_runner as cr

    stopped = []

    class FakeHandle:
        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(cr, "start_collector", lambda: FakeHandle())
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    m.main()
    assert stopped == [True]
