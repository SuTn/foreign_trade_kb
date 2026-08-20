# build.py
"""一键启动包构建脚本。

用法: python build.py
产出: dist/外贸客户知识库.zip (解压即用)

流程:
  1. 创建干净构建 venv (Python 3.11)
  2. 安装项目依赖 + pyinstaller
  3. playwright install chromium
  4. pyinstaller build.spec
  5. 复制 Chromium 到 dist/.../runtime/browsers
  6. 生成 zip
  7. 清理构建 venv
"""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD_VENV = ROOT / ".build-venv"
DIST_DIR = ROOT / "dist"
APP_NAME = "外贸客户知识库"
# 构建用 Python 3.11 (chromadb 0.4.x 不兼容 3.13)。优先用本机已装的 3.11,
# 否则回退到当前解释器 (需 >=3.11,<3.13, 见 pyproject.toml requires-python)。
# 可覆盖: 设置环境变量 KB_BUILD_PYTHON 指向 3.11 解释器路径。
PYTHON_311 = os.environ.get(
    "KB_BUILD_PYTHON",
    r"C:\Users\S6819489\AppData\Roaming\uv\python\cpython-3.11.11-windows-x86_64-none\python.exe",
)


def _find_python311() -> str:
    """探测可用的 Python 3.11 解释器: 优先 KB_BUILD_PYTHON, 其次常见路径, 最后 sys.executable。"""
    candidates = [PYTHON_311]
    # 常见 uv 管理路径
    uv_dir = Path.home() / "AppData" / "Local" / "uv" / "python"
    if uv_dir.exists():
        for p in sorted(uv_dir.glob("cpython-3.11*/python.exe"), reverse=True):
            candidates.append(str(p))
    # 系统 PATH 里的 python3.11
    candidates.append("python3.11")
    for c in candidates:
        if not c:
            continue
        try:
            r = subprocess.run([c, "-c", "import sys; print(sys.version_info[:2])"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip().startswith("(3, 11"):
                return c
        except Exception:
            continue
    # 回退 sys.executable (需 >=3.11)
    return sys.executable


def _run(cmd, cwd=None):
    print(f"\n>>> {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=cwd or ROOT)
    if r.returncode != 0:
        raise SystemExit(f"命令失败: {' '.join(cmd)} (rc={r.returncode})")


def _create_venv():
    if BUILD_VENV.exists():
        shutil.rmtree(BUILD_VENV)
    # 用 Python 3.11 创建构建 venv (chromadb 0.4.x 不兼容 3.13)
    base_py = _find_python311()
    print(f"使用 Python: {base_py}")
    _run([base_py, "-m", "venv", str(BUILD_VENV)])
    return BUILD_VENV / "Scripts" / "python.exe"


def _install_deps(py):
    _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    _run([str(py), "-m", "pip", "install", "-e", str(ROOT)])
    _run([str(py), "-m", "pip", "install", "pyinstaller", "pystray", "pillow"])


def _install_chromium(py):
    _run([str(py), "-m", "playwright", "install", "chromium"])


def _pyinstaller(py):
    _run([str(py), "-m", "PyInstaller", str(ROOT / "build.spec"), "--noconfirm", "--clean"])


def _copy_chromium():
    """把 Playwright 下载的 Chromium 复制到 dist/runtime/browsers。"""
    src = BUILD_VENV / "Lib" / "site-packages" / "playwright" / "driver" / "package" / ".local-browsers"
    if not src.exists():
        # 尝试 uv 管理的浏览器缓存
        alt = Path.home() / "AppData" / "Local" / "ms-playwright"
        src = alt if alt.exists() else src
    if not src.exists():
        print("[warn] 未找到 Playwright Chromium, 跳过复制")
        return
    dst = DIST_DIR / APP_NAME / "runtime" / "browsers"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    print(f"已复制 Chromium: {src} -> {dst}")


def _make_zip():
    src = DIST_DIR / APP_NAME
    out = DIST_DIR / f"{APP_NAME}-一键启动包.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, src)
                zf.write(full, os.path.join(APP_NAME, rel))
    print(f"\n✅ 打包完成: {out}")
    print(f"   大小: {out.stat().st_size / 1024 / 1024:.1f} MB")


def main():
    print("=== 外贸客户知识库 一键启动包构建 ===")
    py = _create_venv()
    _install_deps(py)
    _install_chromium(py)
    _pyinstaller(py)
    _copy_chromium()
    _make_zip()
    # 清理构建 venv
    shutil.rmtree(BUILD_VENV, ignore_errors=True)
    print("\n构建完成!")


if __name__ == "__main__":
    main()