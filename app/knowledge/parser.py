"""适配 WeKnora docreader, 统一 parse_document 接口。"""
from pathlib import Path

from app.config import settings  # noqa: F401  (保留配置入口, 供后续扩展使用)


def parse_document(path: str | Path) -> str:
    """按扩展名路由到 docreader 解析器, 返回纯文本。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xls", ".csv"):
        from vendor.docreader.excel_parser import parse_excel
        return parse_excel(str(p))
    elif ext == ".pdf":
        from vendor.docreader.pdf_parser import parse_pdf
        return parse_pdf(str(p))
    elif ext in (".docx", ".doc"):
        from vendor.docreader.docx_parser import parse_docx
        return parse_docx(str(p))
    elif ext in (".txt", ".md"):
        return p.read_text(encoding="utf-8")
    elif ext in (".html", ".htm"):
        from bs4 import BeautifulSoup
        return BeautifulSoup(p.read_text(encoding="utf-8"), "html.parser").get_text()
    else:
        raise ValueError(f"不支持的格式: {ext}")
