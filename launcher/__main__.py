# launcher/__main__.py
"""一键启动包入口 (exe 入口)。

编排启动流程:
  1. 路径处理 (PyInstaller 打包后资源/数据路径)
  2. 环境自检 (磁盘/Chromium/端口)
  3. 启动采集器 (同进程线程; 未配置模型 Key 时不启动)
  4. 启动 uvicorn Web
  5. 延迟打开浏览器

模型配置在 Web 设置页「模型配置」区块完成 (无 .env 时直接进 Web, 不弹向导)。

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


def _has_model_key() -> bool:
    """检测是否已配置模型 Key (embedding Key 是向量化核心, 无则无法工作)。"""
    try:
        from app.config import settings
        return bool(settings.embedding_api_key)
    except Exception:
        return False


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

    # 无 .env 时直接进 Web, 业务员在页面配置模型 (不再弹 tkinter 向导)
    if not os.path.exists(os.path.join(paths.base_dir(), ".env")):
        log.info("未检测到 .env, 业务员将在 Web 设置页配置模型")

    # 端口检测
    port = env_check.find_free_port(DEFAULT_PORT)
    if port != DEFAULT_PORT:
        log.warning("端口 %d 被占用, 改用 %d", DEFAULT_PORT, port)
    url = f"http://127.0.0.1:{port}"

    # 启动采集器 (同进程线程); 未配置模型 Key 时不启动, 避免打开 WhatsApp
    collector = None
    if _has_model_key():
        collector = start_collector()
        log.info("采集器已启动")
    else:
        log.info("未配置模型 Key, 暂不启动采集器 (配置后 Web 页面自动启动)")

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
        if collector:
            collector.stop()
        try:
            from launcher import tray
            tray.stop_tray()
        except Exception:
            pass


if __name__ == "__main__":
    main()