# app/collector/sender.py
"""发送文字 + 打开会话 (点击聊天列表行) 的页面操作。

只做页面 DOM 操作, 不持有 store。选择器集中在此, WhatsApp Web 改版时单点修补。
"""
import json
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
    """点击聊天列表里标题匹配的会话行打开会话 (query 为显示名)。

    用与 scan_all_chats 相同的行选择器 (已验证可用), 通过 JS 匹配标题并点击,
    避免搜索框选择器在 WhatsApp 改版后失效。
    """
    js = (
        "(function(){"
        "var target=" + json.dumps(query) + ";"
        "var rows=document.querySelectorAll('[data-testid=\"chat-list\"] div[role=\"row\"]');"
        "for(var i=0;i<rows.length;i++){"
        "var t=rows[i].querySelector('span[title]');"
        "var name=t?(t.getAttribute('title')||'').trim():'';"
        "if(name && (name===target || name.indexOf(target)!==-1 || target.indexOf(name)!==-1)){"
        "rows[i].click();return true;"
        "}"
        "}"
        "return false;"
        "})()"
    )
    try:
        return bool(await page.evaluate(js))
    except Exception:
        return False
