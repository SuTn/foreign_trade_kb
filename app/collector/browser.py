# app/collector/browser.py
"""Playwright 启动独立 Chrome + user-data-dir 持久登录, 返回 ReadOnlyCDP。"""
from playwright.async_api import async_playwright
from app.collector.readonly_cdp import ReadOnlyCDP
from app.config import settings

async def launch_browser():
    pw = await async_playwright().start()
    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(settings.user_data_dir),
        headless=False,  # WhatsApp Web 需可见渲染
        args=["--disable-blink-features=AutomationControlled"])
    page = await context.new_page()
    await page.goto(settings.whatsapp_url)
    cdp = ReadOnlyCDP(await context.new_cdp_session(page))
    return pw, context, page, cdp

async def wait_for_login(page) -> bool:
    """等待 WhatsApp Web 登录完成 (canvas/登录二维码消失)。"""
    try:
        await page.wait_for_selector('canvas[aria-label="Scan me!"]', timeout=3000, state="detached")
    except Exception:
        pass
    # 登录后会出现聊天列表
    try:
        await page.wait_for_selector("[data-testid='chat-list']", timeout=120000)
        return True
    except Exception:
        return False
