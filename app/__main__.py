# app/__main__.py
"""开发启动脚本: 主进程跑 Web, 采集器在同进程线程内运行 (P0 改造)。

原实现用 subprocess.Popen([sys.executable, "-m", "app.collector"]) 启动采集器,
PyInstaller 打包后 sys.executable 指向 exe、-m 模块机制不存在, 故改为同进程线程方案。
开发环境 `python -m app` 与打包 exe 均走此路径。

启动编排复用 launcher (见 launcher/__main__.py 的 run_web_and_collector),
避免与打包入口逻辑重复。
"""
import logging

log = logging.getLogger(__name__)


def main():
    from launcher.__main__ import run_web_and_collector
    run_web_and_collector(port=8000)


if __name__ == "__main__":
    main()