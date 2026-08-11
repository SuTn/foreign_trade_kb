# app/collector/dom_snapshot.py
"""从 DOMSnapshot.captureSnapshot 结果解析消息行 (真实 WhatsApp Web DOM)。

CDP 结构: {documents: [{nodes: {parentIndex, nodeType, nodeValue, textValue, attributes}}, ...], strings: [全局字符串表]}
消息行特征 (2026 版 WhatsApp Web):
  - 行元素: data-id=<hex msg id> 且 data-testid="conv-msg-<id>"
  - fromMe: 子树内 span[data-testid="tail-in"/"tail-out"]
  - 正文: 子树内 span[data-testid="selectable-text"] 的文本
  - 时间/发送人: 子树内带 data-pre-plain-text 的元素, 形如 "[13:57, 2025年10月28日] 名字: "
"""
import re
import datetime as _dt

from app.config import settings

# 媒体行 testid 前缀 → 无正文时的占位标记
MEDIA_MARKERS = {
    "image-album-": "[相册]", "image-": "[图片]", "video-": "[视频]",
    "ptt-": "[语音]", "document-": "[文档]", "audio-": "[音频]", "location-": "[位置]",
}


def parse_dom_snapshot(snapshot: dict, active_chat_id: str | None = None) -> list[dict]:
    """返回 [{id, message_id, chatId, fromMe, from, timestamp, type, body, body_present}]。
    chatId 由 active_chat_id 提供 (慢同步里由 IDB 消息推导); 无法解析返回空列表。"""
    strings = snapshot.get("strings") or []
    docs = snapshot.get("documents") or []
    if not strings or not docs:
        return []
    nodes = docs[0].get("nodes", {}) or {}
    parent = nodes.get("parentIndex") or []
    ntype = nodes.get("nodeType") or []
    node_value = nodes.get("nodeValue") or []
    text_value = nodes.get("textValue") or []
    attributes = nodes.get("attributes") or []
    n = len(ntype)
    if n == 0:
        return []

    def s(idx):
        return strings[idx] if 0 <= idx < len(strings) else ""

    def attr_dict(i):
        d = {}
        a = attributes[i] if i < len(attributes) else []
        for k in range(0, len(a) - 1, 2):
            d[s(a[k])] = s(a[k + 1])
        return d

    children = [[] for _ in range(n)]
    testid = {}
    pre_values = {}  # node -> data-pre-plain-text 值
    row_idx = []
    media_prefixes = tuple(settings.dom_media_row_prefixes)
    for i, p in enumerate(parent):
        if 0 <= p < n:
            children[p].append(i)
    for i in range(n):
        if ntype[i] != 1:
            continue
        ad = attr_dict(i)
        tid = ad.get("data-testid", "")
        testid[i] = tid
        if (tid.startswith("conv-msg-") or tid.startswith(media_prefixes)) and ad.get("data-id"):
            row_idx.append((i, ad))
        if "data-pre-plain-text" in ad:
            pre_values[i] = ad["data-pre-plain-text"]

    messages = []
    for i, ad in row_idx:
        msg = _parse_row(i, ad, children, ntype, node_value, text_value,
                         testid, pre_values, strings, active_chat_id)
        if msg:
            messages.append(msg)
    return messages


def _parse_row(i, ad, children, ntype, node_value, text_value, testid, pre_values, strings, chat_id) -> dict | None:
    """提取单条消息行字段。跳过引用容器 (message-quote/quoted-*); 媒体行占位。"""
    data_id = ad.get("data-id", "")
    if not data_id:
        return None
    row_tid = ad.get("data-testid", "")
    media_prefix = next((p for p in settings.dom_media_row_prefixes
                         if row_tid.startswith(p)), None)
    from_me, pre = False, ""
    body_parts = []
    stack = list(reversed(children[i])) if i < len(children) else []
    while stack:
        cur = stack.pop()
        if cur >= len(children):
            continue
        tid = testid.get(cur, "")
        if tid.startswith("message-quote") or tid.startswith("quoted-"):
            continue  # 引用容器整块跳过, 不含本人正文 (testid 漂移时自然回退到收集全部)
        if tid == "tail-out":
            from_me = True
        elif tid == "tail-in":
            from_me = False
        if tid == "selectable-text":
            body_parts.append(_collect_text(cur, children, ntype, node_value, text_value, strings))
            continue  # selectable-text 内部不再深入, 避免重复
        if cur in pre_values:
            pre = pre_values[cur]
        stack.extend(reversed(children[cur]))

    ts, sender = _parse_pre_plain_text(pre)
    body = "".join(body_parts)
    if media_prefix:
        msg_type = media_prefix.rstrip("-")
        if not body:
            body = MEDIA_MARKERS[media_prefix]  # 无正文: 媒体标记占位
    else:
        msg_type = "chat"
    return {
        "id": data_id, "message_id": data_id, "chatId": chat_id,
        "fromMe": bool(from_me), "from": sender, "timestamp": ts,
        "type": msg_type, "body": body, "body_present": bool(body),
    }


def _collect_text(root, children, ntype, node_value, text_value, strings) -> str:
    parts = []
    stack = list(reversed(children[root])) if root < len(children) else []
    while stack:
        cur = stack.pop()
        if cur >= len(children):
            continue
        if ntype[cur] == 3:
            v = text_value[cur] if cur < len(text_value) and text_value[cur] >= 0 else (
                node_value[cur] if cur < len(node_value) else -1)
            if 0 <= v < len(strings):
                parts.append(strings[v])
        stack.extend(reversed(children[cur]))
    return "".join(parts)


def _parse_pre_plain_text(pre: str) -> tuple[int, str | None]:
    """解析 data-pre-plain-text, 如 "[13:57, 2025年10月28日] 名字: 正文...".
    支持中文 (2025年10月28日) 与斜杠 (9/8/2026, 日/月/年) 两种日期。
    返回 (epoch, sender)。"""
    if not pre:
        return 0, None
    m = re.match(r"^\[([^\]]+)\]\s*([^:]*?):", pre)
    if not m:
        return 0, None
    seg = m.group(1).strip()
    sender = m.group(2).strip() or None
    ts = 0
    try:
        hhmm, date = seg.split(",", 1)
        hh, mm = hhmm.strip().split(":")
        d = date.strip()
        cn = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$", d)
        if cn:
            y, mo, da = (int(x) for x in cn.groups())
        else:
            dm = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", d)
            if dm:
                da, mo, y = (int(x) for x in dm.groups())
            else:
                return 0, sender
        ts = int(_dt.datetime(y, mo, da, int(hh), int(mm)).timestamp())
    except Exception:
        ts = 0
    return ts, sender
