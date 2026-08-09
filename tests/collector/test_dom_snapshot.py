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
