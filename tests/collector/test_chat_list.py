# tests/collector/test_chat_list.py
import asyncio
from app.collector.chat_list import read_chat_list


class FakeCdp:
    def __init__(self, result):
        self._result = result
        self.expr = None

    async def eval_async_readonly(self, expression):
        self.expr = expression
        return self._result


def test_read_chat_list_returns_rows():
    rows = [{"name": "Alice", "unread": 2, "preview": "need price"}]
    cdp = FakeCdp(rows)
    assert asyncio.run(read_chat_list(cdp)) == rows
    assert "chat-list" in cdp.expr  # JS 表达式里含 chat-list 容器


def test_read_chat_list_null_returns_empty():
    cdp = FakeCdp(None)
    assert asyncio.run(read_chat_list(cdp)) == []
