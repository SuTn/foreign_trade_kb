# tests/rag/test_pipeline.py
from app.rag.pipeline import _estimate_tokens


def test_estimate_tokens_cjk_vs_ascii():
    """A6: CJK 字符约 1 token/字, 非 CJK 约 4 字符/token。"""
    # 纯中文: 10 字 ≈ 10 token (用 \u 转义避免 Windows 编码问题)
    assert _estimate_tokens("\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341") == 10
    # 纯英文: 40 字符 ≈ 10 token
    assert _estimate_tokens("a" * 40) == 10  # 40//4
    # 空串
    assert _estimate_tokens("") == 0


def test_estimate_tokens_mixed():
    """A6: 中英混合按各自规则估算。"""
    # 2 中文 + 5 英文 = 2 + 5//4 = 3
    assert _estimate_tokens("\u4e2d\u6587abcde") == 3
