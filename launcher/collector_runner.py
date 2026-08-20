# launcher/collector_runner.py
"""采集器同进程启动器 (P0 改造)。

替代 app/__main__.py 的 subprocess.Popen([sys.executable, "-m", "app.collector"])。
PyInstaller 打包后 sys.executable 指向 exe, -m 模块机制不存在, 故改为:
  在独立线程内跑一个 asyncio 事件循环, 运行采集器协程; 崩溃时自动重启 (保留 supervisor 语义)。

对外接口:
  start_collector() -> CollectorHandle  启动采集器线程 (幂等, 全局单例)
  stop_collector()                      停止采集器
  get_collector() -> CollectorHandle    获取全局采集器句柄
  is_collector_running() -> bool        采集器是否在运行
"""
import asyncio
import logging
import threading
import time

log = logging.getLogger(__name__)

# 全局采集器单例 (Web 路由与 launcher 共享, 同进程)
_handle: "CollectorHandle | None" = None
_handle_lock = threading.Lock()


def _run_collector_coro():
    """在独立事件循环里运行采集器主协程 (等价于 app/collector/__main__.py 的 _run)。"""
    from app.collector.browser import launch_browser, wait_for_login
    from app.collector.scanner import Scanner, write_status
    from app.storage.sqlite_store import SqliteStore
    from app.storage.chroma_store import ChromaStore
    from app.llm.bge_embedding import get_embedding
    from app.llm.cloud_llm import CloudLLM
    from app.config import settings

    async def _run():
        write_status(settings.status_path, {"state": "starting"})
        pw, context, page, cdp = await launch_browser()
        logged_in = await wait_for_login(page)
        write_status(settings.status_path,
                     {"state": "logged_in" if logged_in else "awaiting_login"})
        if not logged_in:
            log.info("请在浏览器扫码登录 WhatsApp")
            await wait_for_login(page)
        store = SqliteStore()
        vector = ChromaStore(embedding_fn=get_embedding().embed)
        scanner = Scanner(cdp, store, vector, page=page, llm=CloudLLM(),
                          pw=pw, context=context)
        await scanner.run()

    asyncio.run(_run())


class CollectorHandle:
    """采集器线程句柄: 启动/停止/重启。"""

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._proc = None  # 兼容: 记录底层浏览器进程 (由 Playwright 管理, 无需手动杀)

    def is_running(self) -> bool:
        """采集器线程是否在运行。"""
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._supervise, daemon=True,
                                        name="collector-supervisor")
        self._thread.start()

    def _supervise(self):
        """守护采集器: 非正常退出等 3s 重启 (保留原 supervisor 语义)。"""
        while not self._stop.is_set():
            try:
                _run_collector_coro()
            except Exception as e:
                log.error("[collector] fatal: %s", e)
            if self._stop.is_set():
                break
            log.info("[collector] exited, restarting in 3s...")
            # 可中断的 sleep
            self._stop.wait(3)

    def stop(self):
        self._stop.set()
        # 无法强制中断 asyncio 线程, 依赖 daemon 线程随进程退出
        if self._thread:
            self._thread.join(timeout=2)


def get_collector() -> CollectorHandle:
    """返回全局采集器单例 (惰性创建)。"""
    global _handle
    with _handle_lock:
        if _handle is None:
            _handle = CollectorHandle()
        return _handle


def start_collector() -> CollectorHandle:
    """启动采集器 (幂等: 已在运行则不重复启动)。"""
    handle = get_collector()
    handle.start()
    return handle


def stop_collector() -> None:
    """停止采集器。"""
    global _handle
    with _handle_lock:
        if _handle:
            _handle.stop()


def is_collector_running() -> bool:
    """采集器是否在运行。"""
    with _handle_lock:
        return bool(_handle and _handle.is_running())