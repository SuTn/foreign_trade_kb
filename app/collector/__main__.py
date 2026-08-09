# app/collector/__main__.py
import asyncio
from app.collector.browser import launch_browser, wait_for_login
from app.collector.scanner import Scanner, write_status
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.bge_embedding import get_embedding
from app.config import settings

async def main():
    write_status(settings.status_path, {"state": "starting"})
    pw, context, page, cdp = await launch_browser()
    logged_in = await wait_for_login(page)
    write_status(settings.status_path, {"state": "logged_in" if logged_in else "awaiting_login"})
    if not logged_in:
        print("请在浏览器扫码登录 WhatsApp")
        await wait_for_login(page)
    store = SqliteStore()
    vector = ChromaStore(embedding_fn=get_embedding().embed)
    scanner = Scanner(cdp, store, vector, page=page)
    await scanner.run()

if __name__ == "__main__":
    asyncio.run(main())
