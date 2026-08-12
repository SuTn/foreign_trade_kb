import pytest
import re
import time
from pathlib import Path

@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    """每个测试用独立 data 目录, 避免污染。"""
    from app import config
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "sqlite_path", tmp_path / "kb.db")
    monkeypatch.setattr(config.settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(config.settings, "status_path", tmp_path / "status.json")
    monkeypatch.setattr(config.settings, "vault_export_dir", tmp_path / "vault")
    monkeypatch.setattr(config.settings, "avatars_dir", tmp_path / "avatars")
    return tmp_path


def reply_task_id(html: str) -> str:
    """从轮询片段 HTML 提取 task_id (uuid4 hex)。"""
    m = re.search(r"/api/reply/status/([0-9a-f]+)", html)
    assert m, f"未找到 task_id: {html[:200]}"
    return m.group(1)


def wait_reply_done(client, task_id, timeout=8.0):
    """轮询 /api/reply/status 直到返回非"处理中"片段 (done/failed)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/reply/status/{task_id}")
        if "正在生成回复" not in r.text:
            return r
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} 未在 {timeout}s 内完成")