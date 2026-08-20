# app/__main__.py
"""启动脚本: 主进程跑 Web, 采集器在同进程线程内运行 (P0 改造)。

原实现用 subprocess.Popen([sys.executable, "-m", "app.collector"]) 启动采集器,
PyInstaller 打包后 sys.executable 指向 exe、-m 模块机制不存在, 故改为同进程线程方案。
开发环境 `python -m app` 与打包 exe 均走此路径。
"""
import threading
import logging

log = logging.getLogger(__name__)


def main():
    # 采集器在同进程线程内启动 (崩溃自动重启, 见 launcher.collector_runner)
    try:
        from launcher.collector_runner import start_collector
        collector = start_collector()
    except Exception as e:
        log.error("采集器启动失败: %s", e)
        collector = None

    try:
        import uvicorn
        uvicorn.run("app.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
    except KeyboardInterrupt:
        pass
    finally:
        if collector:
            collector.stop()


if __name__ == "__main__":
    main()