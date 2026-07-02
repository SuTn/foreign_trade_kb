"""DOCX 解析器 (功能性回退实现, 归属 Tencent WeKnora, MIT)。

原始 WeKnora `docx_parser.py` 实现多进程版面分析、图片抽取等并依赖整个
docreader 包; 本模块按适配层契约提供模块级 `parse_docx(path) -> str`,
使用 python-docx 提取段落与表格文本。
"""
from __future__ import annotations

from docx import Document


def parse_docx(path: str) -> str:
    """解析 .docx 为纯文本, 提取段落与表格内容。"""
    doc = Document(path)
    parts: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" ".join(cells))

    return "\n".join(parts)
