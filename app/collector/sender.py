# app/collector/sender.py
"""发送文字 + 打开会话 (搜索框切换) 的页面操作。

只做页面 DOM 操作, 不持有 store。选择器集中在此, WhatsApp Web 改版时单点修补。
"""
from playwright.async_api import Page

MESSAGE_BOX_SELECTORS = [
    'footer div[contenteditable="true"][data-tab="10"]',
    'footer div[contenteditable="true"]',
    'div[contenteditable="true"][data-tab="10"]',
]
SEARCH_BOX_SELECTORS = [
    'div[contenteditable="true"][data-tab="3"]',
    'div[contenteditable="true"][data-testid="chat-list-search"]',
]
CHAT_LIST_ROW_SELECTOR = "[data-testid='chat-list'] div[role='row']"


async def _first(page: Page, selectors: list[str]):
    """按顺序返回第一个存在的元素; 找不到抛 RuntimeError。"""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    raise RuntimeError(f"未找到元素: {selectors}")


async def send_text(page: Page, text: str) -> bool:
    """在当前打开的会话输入框写入文字并回车发送。返回是否成功。"""
    box = await _first(page, MESSAGE_BOX_SELECTORS)
    await box.click()
    await page.keyboard.type(text)
    await page.keyboard.press("Enter")
    return True


async def open_chat(page: Page, query: str) -> bool:
    """通过搜索框定位并打开会话 (query 为显示名或手机号)。返回是否成功。"""
    search = await _first(page, SEARCH_BOX_SELECTORS)
    await search.click()
    # 清空可能残留的旧查询, 避免新 query 被追加导致匹配失败
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await page.keyboard.type(query)
    await page.wait_for_timeout(800)  # 等搜索结果出现
    row = page.locator(CHAT_LIST_ROW_SELECTOR).first
    if await row.count() == 0:
        return False
    await row.click()
    return True
