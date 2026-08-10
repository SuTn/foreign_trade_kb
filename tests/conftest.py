import pytest
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