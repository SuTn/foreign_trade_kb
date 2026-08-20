# launcher/paths.py
"""路径处理: 统一解析 PyInstaller 打包后与开发环境下的资源/数据路径。

打包后 (sys.frozen):
  - base        = exe 所在目录 (业务员解压目录)
  - resource    = sys._MEIPASS (PyInstaller 解压的只读资源, 含 app/ 代码、模板、schema.sql)
  - data_dir    = base/data (可写, 业务员数据)
  - browsers    = base/runtime/browsers (内嵌 Chromium)

开发环境:
  - base        = 项目根目录
  - resource    = 项目根目录
  - data_dir    = base/data
"""
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """程序根目录: 打包后为 exe 所在目录, 开发为项目根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # 开发: launcher 在项目根下, 取项目根
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """只读资源目录 (模板/静态/schema.sql): 打包后为 _MEIPASS, 开发为项目根。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", base_dir()))
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """业务员数据目录 (kb.db / chroma / user-data-dir / avatars)。"""
    return base_dir() / "data"


def browsers_dir() -> Path:
    """内嵌 Chromium 目录。"""
    return base_dir() / "runtime" / "browsers"


def setup_environment() -> Path:
    """修正打包后的环境: 设 PLAYWRIGHT_BROWSERS_PATH, chdir 到 base, 返回 base。"""
    base = base_dir()
    os.chdir(base)
    if is_frozen():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir())
    # 确保 data 目录存在
    data_dir().mkdir(parents=True, exist_ok=True)
    return base