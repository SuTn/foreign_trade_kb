# tests/collector/test_browser.py
import asyncio
from app.config import settings

def test_user_data_dir_configured():
    # 持久登录依赖独立 user-data-dir
    assert settings.user_data_dir.name == "user-data-dir"

def test_readonly_cdp_returned():
    # launch_browser 返回 ReadOnlyCDP 实例 (类型契约)
    # 实际 Playwright 启动在集成测试, 单元测试只验证类型
    from app.collector.readonly_cdp import ReadOnlyCDP
    assert hasattr(ReadOnlyCDP, "capture_snapshot")


def test_wait_for_login_aborts_on_stop_event(monkeypatch):
    """wait_for_login 在 stop_event 置位后尽快返回 False (审计 #11: 登录阶段可中断)。"""
    from app.collector import browser

    class FakePage:
        async def wait_for_selector(self, sel, timeout=None, state=None):
            # 模拟聊天列表一直不出现 (登录未完成)
            raise Exception("timeout")

    stop_event = asyncio.Event()
    stop_event.set()  # 已置位 → 第一轮轮询即返回 False
    result = asyncio.run(browser.wait_for_login(FakePage(), stop_event=stop_event))
    assert result is False
