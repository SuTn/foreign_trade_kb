# tests/collector/test_dom_snapshot.py
from app.collector.dom_snapshot import parse_dom_snapshot, _parse_pre_plain_text


def _snapshot():
    """构造符合 DOMSnapshot.captureSnapshot + 真实 WhatsApp 消息行结构的合成快照。

    strings:
      0 data-id  1 data-testid  2 data-pre-plain-text
      3 conv-msg-3EB06C1E7DA73250B3B4  4 3EB06C1E7DA73250B3B4
      5 tail-in  6 selectable-text
      7 [13:57, 2025年10月28日] 宁波腾达公司 Mohammed Mahbouba:   8 Price please
    nodes:
      0 div(parent -1) -> 1
      1 div[data-id=4, data-testid=conv-msg-3EB0...](parent 0) -> 2,3,4
      2 span[data-testid=tail-in](parent 1) -> 5
      3 div[data-pre-plain-text=7](parent 1) -> 6
      4 div[data-testid=selectable-text](parent 1) -> 7
      5 text(value=5 "tail-in") 6 text(value=7 pre) 7 text(value=8 "Price please")
    """
    return {
        "strings": [
            "data-id", "data-testid", "data-pre-plain-text",
            "conv-msg-3EB06C1E7DA73250B3B4", "3EB06C1E7DA73250B3B4",
            "tail-in", "selectable-text",
            "[13:57, 2025年10月28日] 宁波腾达公司 Mohammed Mahbouba: ", "Price please",
        ],
        "documents": [{
            "nodes": {
                "parentIndex": [-1, 0, 1, 1, 1, 2, 3, 4],
                "nodeType": [1, 1, 1, 1, 1, 3, 3, 3],
                "nodeName": [-1, -1, -1, -1, -1, -1, -1, -1],
                "nodeValue": [-1, -1, -1, -1, -1, 5, 7, 8],
                "textValue": [-1, -1, -1, -1, -1, -1, -1, -1],
                "attributes": [[], [0, 4, 1, 3], [1, 5], [2, 7], [1, 6], [], [], []],
            }
        }],
    }


def test_parse_returns_message_row():
    msgs = parse_dom_snapshot(_snapshot(), active_chat_id="8615976909619@c.us")
    assert len(msgs) == 1
    m = msgs[0]
    assert m["id"] == "3EB06C1E7DA73250B3B4"
    assert m["message_id"] == "3EB06C1E7DA73250B3B4"
    assert m["chatId"] == "8615976909619@c.us"
    assert m["fromMe"] is False  # tail-in
    assert m["from"] == "宁波腾达公司 Mohammed Mahbouba"
    assert m["body"] == "Price please"
    assert m["body_present"] is True
    assert m["timestamp"] > 0


def test_parse_from_me_true_tail_out():
    snap = _snapshot()
    # 把 tail-in 换成 tail-out (字符串表 + 属性)
    snap["strings"][5] = "tail-out"
    msgs = parse_dom_snapshot(snap)
    assert msgs[0]["fromMe"] is True


def test_parse_empty_or_malformed_returns_empty():
    assert parse_dom_snapshot({}) == []
    assert parse_dom_snapshot({"strings": [], "documents": [{"nodes": {}}]}) == []
    assert parse_dom_snapshot({"strings": ["x"], "documents": [{"nodes": {"nodeType": []}}]}) == []


def test_parse_ignores_non_conv_msg_rows():
    snap = _snapshot()
    # 增加一个 image-album 行 (有 data-id 但 testid 非 conv-msg), 应被忽略
    snap["strings"] = snap["strings"] + ["album-x"]
    snap["documents"][0]["nodes"]["parentIndex"].append(0)
    snap["documents"][0]["nodes"]["nodeType"].append(1)
    snap["documents"][0]["nodes"]["nodeName"].append(-1)
    snap["documents"][0]["nodes"]["nodeValue"].append(-1)
    snap["documents"][0]["nodes"]["textValue"].append(-1)
    did = snap["strings"].index("data-id")
    tid = snap["strings"].index("data-testid")
    album_idx = snap["strings"].index("album-x")
    snap["documents"][0]["nodes"]["attributes"].append([did, album_idx, tid, album_idx])
    msgs = parse_dom_snapshot(snap)
    assert len(msgs) == 1  # 仍只有 conv-msg 行


def test_parse_pre_plain_text_formats():
    cn = _parse_pre_plain_text("[13:57, 2025年10月28日] 宁波腾达公司 Mohammed Mahbouba: Price please")
    assert cn[1] == "宁波腾达公司 Mohammed Mahbouba"
    assert cn[0] > 0
    slash = _parse_pre_plain_text("[12:05, 9/8/2026] Alice: hello")
    assert slash[1] == "Alice"
    assert slash[0] > 0
    assert _parse_pre_plain_text("") == (0, None)


def test_parse_excludes_quote_container():
    """引用容器 (message-quote) 内文本不进入 body, 只留本人正文。"""
    snap = {
        "strings": [
            "data-id", "data-testid", "data-pre-plain-text",
            "conv-msg-ABC123", "ABC123", "message-quote", "selectable-text",
            "[13:57, 2025年10月28日] Alice: ", "这是回复的旧内容", "这是新消息正文",
        ],
        "documents": [{
            "nodes": {
                "parentIndex": [-1, 0, 1, 1, 1, 2, 3, 5, 4],
                "nodeType": [1, 1, 1, 1, 1, 1, 3, 3, 3],
                "nodeName": [-1] * 9,
                "nodeValue": [-1, -1, -1, -1, -1, -1, 7, 8, 9],
                "textValue": [-1] * 9,
                "attributes": [[], [0, 4, 1, 3], [1, 5], [2, 7], [1, 6], [1, 6], [], [], []],
            }
        }],
    }
    msgs = parse_dom_snapshot(snap)
    assert len(msgs) == 1
    m = msgs[0]
    assert m["body"] == "这是新消息正文"
    assert "这是回复的旧内容" not in m["body"]
    assert m["from"] == "Alice"


def _media_snapshot(with_caption=True):
    """image-album 行: node2=pre 元素, node3=caption (可省略)。"""
    if with_caption:
        return {
            "strings": [
                "data-id", "data-testid", "data-pre-plain-text",
                "image-album-ABC123", "ABC123", "selectable-text",
                "[13:57, 2025年10月28日] Alice: ", "假期照片",
            ],
            "documents": [{
                "nodes": {
                    "parentIndex": [-1, 0, 1, 1, 2, 3],
                    "nodeType": [1, 1, 1, 1, 3, 3],
                    "nodeName": [-1] * 6,
                    "nodeValue": [-1, -1, -1, -1, 6, 7],
                    "textValue": [-1] * 6,
                    "attributes": [[], [0, 4, 1, 3], [2, 6], [1, 5], [], []],
                }
            }],
        }
    return {
        "strings": [
            "data-id", "data-testid", "data-pre-plain-text",
            "image-album-ABC123", "ABC123", "selectable-text",
            "[13:57, 2025年10月28日] Alice: ",
        ],
        "documents": [{
            "nodes": {
                "parentIndex": [-1, 0, 1, 1, 2],
                "nodeType": [1, 1, 1, 1, 3],
                "nodeName": [-1] * 5,
                "nodeValue": [-1, -1, -1, -1, 6],
                "textValue": [-1] * 5,
                "attributes": [[], [0, 4, 1, 3], [2, 6], [], []],
            }
        }],
    }


def test_parse_media_row_with_caption():
    """相册行带说明文字 → body=说明, type=image-album。"""
    msgs = parse_dom_snapshot(_media_snapshot(True))
    assert len(msgs) == 1
    m = msgs[0]
    assert m["type"] == "image-album"
    assert m["body"] == "假期照片"
    assert m["body_present"] is True


def test_parse_media_row_marker_placeholder():
    """相册行无正文 → 媒体标记 [相册] 占位。"""
    msgs = parse_dom_snapshot(_media_snapshot(False))
    m = msgs[0]
    assert m["type"] == "image-album"
    assert m["body"] == "[相册]"
