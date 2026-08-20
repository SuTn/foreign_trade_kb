# launcher/__main__.py
"""一键启动包入口 (exe 入口)。

编排启动流程:
  1. 路径处理 (PyInstaller 打包后资源/数据路径)
  2. 环境自检 (磁盘/Chromium/端口)
  3. 首次配置向导 (无 .env 时)
  4. 启动采集器 (同进程线程)
  5. 启动 uvicorn Web
  6. 延迟打开浏览器

开发环境: `python -m launcher`
打包后:   exe 直接运行
"""
import logging
import os
import sys
import threading
import time
import webbrowser

from launcher import paths, env_check
from launcher.collector_runner import start_collector

log = logging.getLogger("launcher")

# 默认端口
DEFAULT_PORT = 8000


def _setup_logging():
    # 强制 UTF-8 输出, 避免 Windows 控制台 cp950 编码中文路径报错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _ensure_env() -> bool:
    """确保 .env 存在; 不存在则直接启动 (业务员在 Web 页面配置模型)。

    不再强制弹 tkinter 向导 —— 模型配置已迁移到 Web 设置页「模型配置」区块。
    无 .env 时仍正常启动, 页面会提示去配置。
    """
    env_path = os.path.join(paths.base_dir(), ".env")
    if os.path.exists(env_path):
        return True
    log.info("未检测到 .env, 业务员将在 Web 设置页配置模型")
    return True  # 不拦截, 直接进 Web


def _delayed_open_browser(url: str, delay: float = 3.0):
    """延迟打开浏览器 (等 uvicorn 就绪)。"""
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception as e:
            log.warning("打开浏览器失败: %s", e)
    threading.Thread(target=_open, daemon=True).start()


def main():
    _setup_logging()
    base = paths.setup_environment()
    log.info("程序目录: %s", base)

    # 环境自检 (非致命, 仅警告)
    for w in env_check.run_checks():
        log.warning("自检: %s", w)

    # 确保 .env 存在 (无则直接进 Web, 业务员在页面配置)
    _ensure_env()

    # 端口检测
    port = env_check.find_free_port(DEFAULT_PORT)
    if port != DEFAULT_PORT:
        log.warning("端口 %d 被占用, 改用 %d", DEFAULT_PORT, port)
    url = f"http://127.0.0.1:{port}"

    # 启动采集器 (同进程线程)
    collector = start_collector()
    log.info("采集器已启动")

    # 启动系统托盘
    try:
        from launcher import tray
        tray.start_tray(url)
    except Exception as e:
        log.warning("托盘启动失败: %s", e)

    # 延迟打开浏览器
    _delayed_open_browser(url)

    # 启动 uvicorn (阻塞)
    try:
        import uvicorn
        uvicorn.run("app.web.app:create_app", factory=True,
                    host="127.0.0.1", port=port)
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop()
        try:
            from launcher import tray
            tray.stop_tray()
        except Exception:
            pass


if __name__ == "__main__":
    main()