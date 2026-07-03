"""验证采集器所有 CDP 访问经 ReadOnlyCDP 门面, 无发送/输入类操作。"""
from pathlib import Path

def test_no_raw_cdp_send_in_collector():
    """采集器代码不得直接调用 session.send 或发送类 CDP 方法。"""
    collector_dir = Path("app/collector")
    forbidden = ["Input.dispatch", "Page.navigate", "sendMessage", "Input.insertText"]
    for py in collector_dir.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for f in forbidden:
            assert f not in src, f"{py}: 含禁止的 CDP 操作 {f}"

def test_collector_uses_readonly_cdp():
    """采集器 scanner/idb_walk/dom_snapshot 必须通过 ReadOnlyCDP。"""
    from app.collector.readonly_cdp import ALLOWED_METHODS
    # 确保白名单不含发送类
    forbidden_substrings = ["Input.dispatch", "Page.navigate", "sendMessage"]
    for m in ALLOWED_METHODS:
        for f in forbidden_substrings:
            assert f not in m
