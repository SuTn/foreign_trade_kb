# launcher/env_check.py
"""环境自检: 磁盘空间 / Chromium 就位 / 端口占用。"""
import logging
import shutil
import socket

from launcher import paths

log = logging.getLogger(__name__)

# 最小可用磁盘空间 (MB)
MIN_FREE_MB = 500


def check_disk_space() -> list[str]:
    """检查 base 所在磁盘剩余空间, 返回警告列表。"""
    warnings = []
    try:
        base = paths.base_dir()
        free = shutil.disk_usage(base).free / (1024 * 1024)
        if free < MIN_FREE_MB:
            warnings.append(f"磁盘剩余空间不足 ({free:.0f}MB < {MIN_FREE_MB}MB), 可能影响数据写入")
    except Exception as e:
        log.warning("磁盘空间检查失败: %s", e)
    return warnings


def check_chromium() -> list[str]:
    """检查内嵌 Chromium 是否就位 (打包后)。返回警告列表。"""
    if not paths.is_frozen():
        return []  # 开发环境用系统 playwright, 不检查
    problems = []
    browsers = paths.browsers_dir()
    if not browsers.exists():
        problems.append(f"未找到内嵌 Chromium: {browsers}")
    return problems


def check_port(port: int = 8000) -> bool:
    """检查端口是否被占用。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True  # 可绑定 = 空闲
        except OSError:
            return False


def find_free_port(start: int = 8000, max_try: int = 20) -> int:
    """从 start 起找第一个空闲端口。"""
    for p in range(start, start + max_try):
        if check_port(p):
            return p
    return start  # 都占用则用 start (会失败, 由上层处理)


def run_checks() -> list[str]:
    """执行全部自检, 返回警告列表 (非致命)。"""
    warnings = []
    warnings += check_disk_space()
    warnings += check_chromium()
    return warnings