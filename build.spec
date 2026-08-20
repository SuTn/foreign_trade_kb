# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置: 外贸客户知识库一键启动包。

用法: pyinstaller build.spec --noconfirm
产出: dist/外贸客户知识库/ (onedir 文件夹, 再 zip 分发)
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files

# 项目根
ROOT = Path(SPECPATH) if "SPECPATH" in globals() else Path.cwd()

block_cipher = None

datas = []
binaries = []
hiddenimports = []

# ---- 项目资源 (模板/静态/schema) ----
datas += [
    (str(ROOT / "app" / "web" / "templates"), "app/web/templates"),
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
    (str(ROOT / "app" / "storage" / "schema.sql"), "app/storage"),
]

# ---- 完整收集 native 依赖 (ChromaDB 是主要难点) ----
for pkg in ["chromadb", "onnxruntime", "hnswlib", "pypdfium2", "openpyxl",
            "docx", "pandas", "bs4", "pydantic", "pydantic_settings",
            "langchain", "langchain_community", "openai", "anthropic",
            "pystray", "PIL", "playwright"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"[warn] collect_all({pkg}) failed: {e}")

# ---- uvicorn 动态导入 (PyInstaller 静态分析找不到) ----
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

# ---- FastAPI/Starlette 路由动态导入 ----
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("launcher")

# ---- ChromaDB migrations (SQL 数据文件) ----
datas += collect_data_files("chromadb", includes=["migrations/**/*.sql"])

# ---- 排除不需要的包 (瘦身) ----
excludes = [
    "matplotlib", "scipy", "torch", "torchvision", "torchaudio",
    "IPython", "jupyter", "notebook", "pytest", "tkinter.test",
    "test", "tests",
]

a = Analysis(
    [str(ROOT / "launcher" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="外贸客户知识库",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 保留控制台便于看日志 (后续可改 False)
    disable_windowed_traceback=False,
    icon=str(ROOT / "runtime" / "icon.ico") if (ROOT / "runtime" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="外贸客户知识库",
)