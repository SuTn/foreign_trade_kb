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

# ---- ChromaDB SegmentAPI 动态加载模块 (PyInstaller 静态分析找不到) ----
# 项目用 SegmentAPI (Python 实现) 绕过 Rust 绑定在 Windows 的 upsert 崩溃 (见 chroma_store.py)。
# SegmentAPI 通过 importlib.import_module 动态加载实现类, 需显式收集。
hiddenimports += [
    "chromadb.api.segment",
    "chromadb.segment.impl.manager.local",
    "chromadb.segment.impl.vector.local_hnsw",
    "chromadb.segment.impl.vector.local_persistent_hnsw",
    "chromadb.segment.impl.metadata.sqlite",
    "chromadb.db.impl.sqlite",
    "chromadb.execution.executor.local",
    "chromadb.ingest.impl.simple_policy",
    "chromadb.quota.simple_quota_enforcer",
    "chromadb.rate_limit.simple_rate_limit",
    "chromadb.telemetry.product.posthog",
]

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
    upx=False,  # 关闭 UPX 压缩: UPX 壳是杀毒误报常见来源, 业务员需加白名单, 降低误报
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
    upx=False,  # 关闭 UPX 压缩 (降低杀毒误报)
    upx_exclude=[],
    name="外贸客户知识库",
)