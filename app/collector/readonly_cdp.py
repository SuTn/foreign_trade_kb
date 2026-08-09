# app/collector/readonly_cdp.py
"""ReadOnlyCDP 门面: 架构级保证采集器只做只读 CDP 操作。
仅暴露只读方法, 禁止采集器直接持有裸 CDP session。所有方法均为 async
(Playwright CDPSession.send 是协程, 必须 await)。"""
from typing import Any

class ReadOnlyCDP:
    def __init__(self, cdp_session):
        # cdp_session: Playwright CDPSession (page.context.new_cdp_session(page))
        self._session = cdp_session

    async def capture_snapshot(self) -> dict:
        """DOMSnapshot.captureSnapshot — 只读, 抓取渲染态 DOM。"""
        return await self._session.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [], "includeDOMRects": False, "includePaintOrder": False})

    async def request_indexed_db(self, database_name: str, object_store_name: str,
                                 skip_count: int = 0, page_size: int = 500) -> dict:
        """IndexedDB.requestData — 只读, 分页读 IDB store。
        注意: 该 WhatsApp 版本下 requestData 返回 0 行, 实际读取改用 eval_async_readonly (页面 JS)。"""
        return await self._session.send("IndexedDB.requestData", {
            "securityOrigin": "https://web.whatsapp.com",
            "databaseName": database_name,
            "objectStoreName": object_store_name,
            "indexName": None, "skipCount": skip_count, "pageSize": page_size,
            "keyRange": None})

    async def eval_readonly(self, expression: str) -> Any:
        """Runtime.evaluate — 仅限只读查询表达式。
        禁止用于注入有副作用的脚本。"""
        return await self._session.send("Runtime.evaluate", {"expression": expression, "returnByValue": True})

    async def eval_async_readonly(self, expression: str) -> Any:
        """Runtime.evaluate (awaitPromise) — 仅限只读查询表达式 (如 IndexedDB 只读读取)。
        返回 expression 求值结果的 value (awaitPromise 解析后)。"""
        r = await self._session.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        try:
            return r.get("result", {}).get("value")
        except Exception:
            return None

    async def scroll_conversation_up(self) -> bool:
        """滚动当前会话面板至顶部, 触发 WhatsApp 加载更早消息 (历史回溯用)。
        这是读取已接收历史消息的辅助操作, 非发送/输入; 经 Runtime.evaluate (白名单内)。"""
        expr = (
            "(function(){"
            "var p=document.querySelector('[data-testid=\"conversation-panel-messages\"]')"
            "||document.querySelector('[data-testid=\"conversation-panel-wrapper\"]');"
            "if(!p)return false;p.scrollTop=0;return true;})()"
        )
        r = await self._session.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
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
