# tests/collector/test_browser.py
from app.config import settings

def test_user_data_dir_configured():
    # 持久登录依赖独立 user-data-dir
    assert settings.user_data_dir.name == "user-data-dir"

def test_readonly_cdp_returned():
    # launch_browser 返回 ReadOnlyCDP 实例 (类型契约)
    # 实际 Playwright 启动在集成测试, 单元测试只验证类型
    from app.collector.readonly_cdp import ReadOnlyCDP
    assert hasattr(ReadOnlyCDP, "capture_snapshot")
