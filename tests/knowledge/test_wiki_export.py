# tests/knowledge/test_wiki_export.py
import json
from pathlib import Path
from app.knowledge.wiki_export import export_vault
from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import WikiPage
import time

def test_export_creates_md_files(tmp_data):
    store = SqliteStore()
    store.upsert_wiki_page(WikiPage("p1", "LED灯", "led灯", "LED照明 [[规格表]]",
                         {"entity_type": "product"}, ["d1"], "product", int(time.time())))
    n = export_vault(store, settings.vault_export_dir)
    assert n == 1
    f = settings.vault_export_dir / "led灯.md"
    assert f.exists()
    content = f.read_text(encoding="utf-8")
    assert "[[规格表]]" in content  # wikilink 保留
    assert "entity_type: product" in content  # frontmatter 保留
    assert (settings.vault_export_dir / ".obsidian" / "app.json").exists()  # vault 配置


def test_export_cross_links_page_titles(tmp_data):
    store = SqliteStore()
    store.upsert_wiki_page(WikiPage("p1", "LED灯", "led灯", "推荐使用 LED灯 做照明",
                         {"entity_type": "product"}, ["d1"], "product", int(time.time())))
    store.upsert_wiki_page(WikiPage("p2", "外贸条款", "waimao-tiaokuan", "询盘含 LED灯 与 外贸条款",
                         {"entity_type": "concept"}, ["d1"], "concept", int(time.time())))
    export_vault(store, settings.vault_export_dir)
    body1 = (settings.vault_export_dir / "led灯.md").read_text(encoding="utf-8")
    body2 = (settings.vault_export_dir / "waimao-tiaokuan.md").read_text(encoding="utf-8")
    assert "推荐使用 LED灯 做照明" in body1  # 不自链
    assert "询盘含 [[led灯]] 与 外贸条款" in body2  # 互链: 其他页标题→[[slug]], 自身不链

from app.config import settings
