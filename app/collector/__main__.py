# app/collector/__main__.py
import asyncio, sys
from app.collector.browser import launch_browser, wait_for_login
from app.collector.scanner import Scanner, write_status
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.bge_embedding import get_embedding
from app.llm.cloud_llm import CloudLLM
from app.config import settings

async def _run(stop_event=None):
    write_status(settings.status_path, {"state": "starting"})
    pw, context, page, cdp = await launch_browser()
    if stop_event is not None and stop_event.is_set():
        return  # 启动浏览器期间收到停止信号, 直接退出
    logged_in = await wait_for_login(page, stop_event=stop_event)
    write_status(settings.status_path, {"state": "logged_in" if logged_in else "awaiting_login"})
    if not logged_in:
        if stop_event is not None and stop_event.is_set():
            return
        print("请在浏览器扫码登录 WhatsApp")
        await wait_for_login(page, stop_event=stop_event)
    store = SqliteStore()
    vector = ChromaStore(embedding_fn=get_embedding().embed)
    scanner = Scanner(cdp, store, vector, page=page, llm=CloudLLM(), pw=pw, context=context,
                      stop_event=stop_event)
    await scanner.run()

async def main():
    try:
        await _run()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"[collector] fatal: {e}", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
