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
    """supervisor: 非 0 退出等 3s 重启, rc==0 正常退出不重启。"""
    import app.__main__ as m

    codes = iter([1, 1, 0])
    pops = []

    class FakeProc:
        pid = 100

        def wait(self):
            return next(codes)

        def poll(self):
            return None

        def terminate(self):
            pass

    def fake_popen(*a, **k):
        p = FakeProc()
        pops.append(p)
        return p

    monkeypatch.setattr(m.subprocess, "Popen", fake_popen)
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))
    m._supervise()
    assert len(pops) == 3
    assert sleeps == [3, 3]


def test_main_kills_collector_tree_on_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt → 终止采集器进程组 (taskkill /T)。"""
    import app.__main__ as m

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

        def wait(self):
            return 0

        def terminate(self):
            pass

    proc = FakeProc()
    monkeypatch.setattr(m.subprocess, "Popen", lambda *a, **k: proc)
    killed = []
    monkeypatch.setattr(m.subprocess, "run", lambda args, **k: killed.append(args))
    monkeypatch.setattr(m.os, "name", "nt")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    m.main()
    assert killed and killed[0][0] == "taskkill" and "1234" in killed[0]
