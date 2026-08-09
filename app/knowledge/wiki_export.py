# app/knowledge/wiki_export.py
"""导出 Wiki 页面为 Obsidian vault (Markdown + frontmatter + [[wikilinks]] + .obsidian 配置)。"""
import json, re
from pathlib import Path
from app.storage.interfaces import StructuredStore

def export_vault(store: StructuredStore, out_dir: Path) -> int:
    """导出所有 wiki_pages 为 .md 文件, 页面正文按标题互链为 [[wikilinks]]。返回导出数量。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = store.conn.execute("SELECT * FROM wiki_pages").fetchall()
    pages = [{
        "slug": r["slug"], "title": r["title"], "body_md": r["body_md"],
        "frontmatter": json.loads(r["frontmatter"]) if r["frontmatter"] else {},
    } for r in rows]
    titles = {p["title"]: p["slug"] for p in pages if p["title"]}
    for p in pages:
        others = {t: s for t, s in titles.items() if s != p["slug"]}  # 不自链
        body = _linkify(p["body_md"], others)
        fm_lines = "\n".join(f"{k}: {v}" for k, v in p["frontmatter"].items())
        content = f"---\n{fm_lines}\n---\n\n{body}\n"
        (out_dir / f"{p['slug']}.md").write_text(content, encoding="utf-8")
    _write_obsidian_config(out_dir)
    return len(pages)


def _linkify(body: str, titles: dict[str, str]) -> str:
    """把正文中出现的其他页面标题替换为 [[slug]] 链接。
    按标题长度降序处理避免局部覆盖; 已处于 [[...]] 内不重复替换。"""
    for title, slug in sorted(titles.items(), key=lambda kv: -len(kv[0])):
        if len(title) < 2:
            continue
        body = re.compile(r"(?<!\[)" + re.escape(title) + r"(?!\])").sub(f"[[{slug}]]", body)
    return body


def _write_obsidian_config(out_dir: Path) -> None:
    """写入 .obsidian/app.json, 使文件夹可作为 Obsidian vault 打开。"""
    cfg_dir = out_dir / ".obsidian"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "app.json").write_text('{\n  "showUnsupportedFiles": true\n}\n', encoding="utf-8")
