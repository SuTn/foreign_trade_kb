"""Excel 解析器 (功能性回退实现, 归属 Tencent WeKnora, MIT)。

原始 WeKnora `excel_parser.py` 以 `ExcelParser(BaseParser).parse_into_text`
类形式存在并依赖整个 docreader 包; 本模块按适配层契约提供模块级
`parse_excel(path) -> str`, 使用 openpyxl/pandas 实现真实解析。
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd


def parse_excel(path: str) -> str:
    """解析 .xlsx/.xls/.csv 为纯文本。

    每个非空行渲染为 "列名: 值" 形式, 行间以换行分隔, 多 sheet 顺序拼接。
    与 WeKnora ExcelParser 的行级文本化思路一致 (简化版)。
    """
    p = Path(path)
    ext = p.suffix.lower()

    if ext == ".csv":
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
        return _rows_to_text(rows)

    # .xlsx / .xls — pandas 读所有 sheet
    xls = pd.ExcelFile(p)
    parts: list[str] = []
    for sheet in xls.sheet_names:
        df = xls.parse(sheet, header=None)
        if df.empty:
            continue
        rows = df.astype(str).values.tolist()
        parts.append(_rows_to_text(rows))
    return "\n".join(parts)


def _rows_to_text(rows: list[list[str]]) -> str:
    """把二维行数据渲染为文本, 跳过完全空行。"""
    lines: list[str] = []
    for row in rows:
        cells = [str(c).strip() for c in row]
        if not any(cells):
            continue
        if len(cells) == 1:
            lines.append(cells[0])
        else:
            lines.append(" ".join(cells))
    return "\n".join(lines)
