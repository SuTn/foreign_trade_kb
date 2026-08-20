# launcher/tray.py
"""系统托盘: 后台运行, 右键可打开网页/退出。"""
import logging
import threading
import webbrowser

log = logging.getLogger(__name__)

_icon = None
_icon_thread = None


def _create_icon_image():
    """生成托盘图标 (用 PIL 画一个简单图标, 避免依赖外部 .ico 文件)。"""
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # 画一个蓝色圆角方块 + 白色"外"字占位
        d.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(30, 120, 220, 255))
        d.text((20, 18), "外", fill=(255, 255, 255, 255))
        return img
    except Exception as e:
        log.warning("生成托盘图标失败: %s", e)
        return None


def _on_open(icon, item):
    webbrowser.open("http://127.0.0.1:8000")


def _on_quit(icon, item):
    icon.stop()


def start_tray(url: str = "http://127.0.0.1:8000"):
    """在后台线程启动系统托盘。失败时静默降级 (不阻塞主流程)。"""
    global _icon, _icon_thread
    try:
        import pystray
        from pystray import Menu, MenuItem
    except Exception as e:
        log.warning("pystray 不可用, 跳过系统托盘: %s", e)
        return

    def _open():
        webbrowser.open(url)

    def _quit():
        if _icon:
            _icon.stop()

    menu = Menu(
        MenuItem("打开网页", lambda icon, item: _open()),
        MenuItem("退出", lambda icon, item: _quit()),
    )
    try:
        _icon = pystray.Icon("外贸客户知识库", _create_icon_image(),
                             "外贸客户知识库 · 运行中", menu=menu)
        _icon_thread = threading.Thread(target=_icon.run, daemon=True,
                                        name="tray")
        _icon_thread.start()
    except Exception as e:
        log.warning("启动系统托盘失败: %s", e)
        _icon = None


def stop_tray():
    global _icon
    if _icon:
        try:
            _icon.stop()
        except Exception:
            pass
        _icon = None