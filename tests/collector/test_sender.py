# tests/collector/test_sender.py
import asyncio
from app.collector.sender import send_text, open_chat


class FakeLocator:
    def __init__(self, found=True):
        self._found = found
        self.clicked = []

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._found else 0

    def nth(self, idx):
        self._nth = idx
        return self

    async def click(self, timeout=None):
        self.clicked.append(self._nth if hasattr(self, "_nth") else None)


class FakePage:
    def __init__(self, box_found=True, evaluate_idx=0):
        self.typed = []
        self.pressed = []
        self.last_evaluate_js = None
        self._box_found = box_found
        self._evaluate_idx = evaluate_idx
        self.row_locator = FakeLocator(found=True)

    def locator(self, sel):
        if "row" in sel:
            return self.row_locator
        return FakeLocator(found=self._box_found)

    @property
    def keyboard(self):
        return self

    async def type(self, text, delay=0):
        self.typed.append(text)

    async def press(self, key):
        self.pressed.append(key)

    async def wait_for_timeout(self, ms):
        pass

    async def evaluate(self, js):
        self.last_evaluate_js = js
        return self._evaluate_idx


def test_send_text_types_and_enters():
    page = FakePage()
    assert asyncio.run(send_text(page, "hello")) is True
    assert page.typed == ["hello"]
    assert page.pressed == ["Enter"]


def test_send_text_no_box_raises():
    import pytest
    page = FakePage(box_found=False)
    with pytest.raises(RuntimeError):
        asyncio.run(send_text(page, "hello"))


def test_open_chat_finds_index_and_clicks_row():
    page = FakePage(evaluate_idx=3)
    assert asyncio.run(open_chat(page, "苏童")) is True
    assert "苏童" in page.last_evaluate_js  # JS 里含查询串
    assert "chat-list" in page.last_evaluate_js  # 用验证过的行选择器
    assert page.row_locator.clicked == [3]  # 点了第 3 行


def test_open_chat_no_match_returns_false():
    page = FakePage(evaluate_idx=-1)
    assert asyncio.run(open_chat(page, "Nobody")) is False
    assert page.row_locator.clicked == []  # 未点击
