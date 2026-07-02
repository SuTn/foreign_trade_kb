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

from app.config import settings
