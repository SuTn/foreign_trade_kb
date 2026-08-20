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

log = logging.getLogger(__name__)

# 全局采集器单例 (Web 路由与 launcher 共享, 同进程)
_handle: "CollectorHandle | None" = None
_handle_lock = threading.Lock()


async def _run_collector_coro(stop_event: asyncio.Event | None = None):
    """运行采集器主协程 (在调用方的事件循环上执行)。

    复用 app/collector/__main__.py 的 _run (单一实现来源), 避免两处重复。
    stop_event: 可选 asyncio.Event, 置位后采集器主循环尽快退出 (供 stop() 真正停止线程)。
    """
    from app.collector.__main__ import _run
    await _run(stop_event=stop_event)


class CollectorHandle:
    """采集器线程句柄: 启动/停止/重启。

    stop() 通过 asyncio.Event 通知采集器主循环尽快退出 (而非仅依赖 daemon 线程随进程退出)。
    """

    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self._stop_event = None  # 当前线程事件循环里的 asyncio.Event (stop 时置位)
        self._loop = None          # 当前线程的事件循环 (stop 时 call_soon_threadsafe)
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
            # 每个采集器运行周期创建独立事件循环 + 停止信号
            loop = asyncio.new_event_loop()
            self._loop = loop
            stop_event = asyncio.Event()
            self._stop_event = stop_event
            try:
                asyncio.set_event_loop(loop)
                loop.run_until_complete(_run_collector_coro(stop_event))
            except Exception as e:
                log.error("[collector] fatal: %s", e)
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                self._loop = None
                self._stop_event = None
            if self._stop.is_set():
                break
            log.info("[collector] exited, restarting in 3s...")
            # 可中断的 sleep
            self._stop.wait(3)

    def stop(self):
        self._stop.set()
        # 通知当前事件循环尽快退出 (若采集器主循环在跑)
        if self._loop is not None and self._stop_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)


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