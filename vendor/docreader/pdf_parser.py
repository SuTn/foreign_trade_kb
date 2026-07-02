"""PDF 解析器 (功能性回退实现, 归属 Tencent WeKnora, MIT)。

原始 WeKnora `pdf_parser.py` 实现复杂的逐页路由 (原生文本 vs 扫描图像) 并
依赖 docreader.config / docreader.models 等; 本模块按适配层契约提供模块级
`parse_pdf(path) -> str`, 使用 pypdfium2 提取原生文本层。
"""
from __future__ import annotations

import pypdfium2 as pdfium


def parse_pdf(path: str) -> str:
    """解析 .pdf 为纯文本, 逐页提取文本层并拼接。"""
    pdf = pdfium.PdfDocument(path)
    parts: list[str] = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                textpage = page.get_textpage()
                parts.append(textpage.get_text_range())
            finally:
                textpage.close()
    finally:
        pdf.close()
    return "\n".join(parts)
