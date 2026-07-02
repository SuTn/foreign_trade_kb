# app/knowledge/wiki_export.py
"""导出 Wiki 页面为 Obsidian vault (Markdown + frontmatter + [[wikilinks]])。"""
import json
from pathlib import Path
from app.storage.interfaces import StructuredStore

def export_vault(store: StructuredStore, out_dir: Path) -> int:
    """导出所有 wiki_pages 为 .md 文件, 返回导出数量。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = store.conn.execute("SELECT * FROM wiki_pages").fetchall()
    for r in rows:
        fm = json.loads(r["frontmatter"]) if r["frontmatter"] else {}
        fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
        content = f"---\n{fm_lines}\n---\n\n{r['body_md']}\n"
        (out_dir / f"{r['slug']}.md").write_text(content, encoding="utf-8")
    return len(rows)
