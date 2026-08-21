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

async def wait_for_login(page, stop_event=None) -> bool:
    """等待 WhatsApp Web 登录完成 (canvas/登录二维码消失)。

    stop_event: 可选 asyncio.Event, 置位后尽快返回 False (供 stop() 在登录阶段也能中断,
    避免 stop() 卡在最长 120s 的登录等待)。
    """
    import asyncio
    try:
        await page.wait_for_selector('canvas[aria-label="Scan me!"]', timeout=3000, state="detached")
    except Exception:
        pass
    # 登录后会出现聊天列表; 分片轮询, 每 2s 检查一次 stop_event, 置位则提前返回
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while True:
        if stop_event is not None and stop_event.is_set():
            return False
        try:
            await page.wait_for_selector("[data-testid='chat-list']", timeout=2000)
            return True
        except Exception:
            pass
        if loop.time() >= deadline:
            return False
