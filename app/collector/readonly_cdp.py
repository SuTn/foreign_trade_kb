# app/collector/readonly_cdp.py
"""ReadOnlyCDP 门面: 架构级保证采集器只做只读 CDP 操作。
仅暴露三个只读方法, 禁止采集器直接持有裸 CDP session。"""
from typing import Any

class ReadOnlyCDP:
    def __init__(self, cdp_session):
        # cdp_session: Playwright CDPSession (page.context.new_cdp_session(page))
        self._session = cdp_session

    def capture_snapshot(self) -> dict:
        """DOMSnapshot.captureSnapshot — 只读, 抓取渲染态 DOM。"""
        return self._session.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [], "includeDOMRects": False, "includePaintOrder": False})

    def request_indexed_db(self, database_name: str, object_store_name: str,
                           skip_count: int = 0, page_size: int = 500) -> dict:
        """IndexedDB.requestData — 只读, 分页读 IDB store。"""
        # 调用方需先 resolve databaseId/objectStoreId (通过 IndexedDB.requestDatabase)
        return self._session.send("IndexedDB.requestData", {
            "securityOrigin": "https://web.whatsapp.com",
            "databaseName": database_name,
            "objectStoreName": object_store_name,
            "indexName": "", "skipCount": skip_count, "pageSize": page_size,
            "keyRange": {}})

    def eval_readonly(self, expression: str) -> Any:
        """Runtime.evaluate — 仅限只读查询表达式。
        禁止用于注入有副作用的脚本。"""
        return self._session.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})

    def scroll_conversation_up(self) -> bool:
        """滚动当前会话面板至顶部, 触发 WhatsApp 加载更早消息 (历史回溯用)。
        这是读取已接收历史消息的辅助操作, 非发送/输入; 经 Runtime.evaluate (白名单内)。"""
        expr = (
            "(function(){"
            "var p=document.querySelector('[data-testid=\"conversation-panel-messages\"]')"
            "||document.querySelector('[data-testid=\"conversation-panel-wrapper\"]');"
            "if(!p)return false;p.scrollTop=0;return true;})()"
        )
        r = self._session.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        try:
            return bool(r.get("result", {}).get("value"))
        except Exception:
            return False

# 白名单: 采集器允许调用的 CDP 方法 (测试断言用)
ALLOWED_METHODS = frozenset({
    "DOMSnapshot.captureSnapshot",
    "IndexedDB.requestDatabase",
    "IndexedDB.requestDatabaseNames",
    "IndexedDB.requestData",
    "Runtime.evaluate",  # 仅经 eval_readonly, 表达式须只读
})
