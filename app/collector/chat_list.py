# app/collector/chat_list.py
"""通过 Runtime.evaluate 只读读取左栏会话列表 (name / unread / preview)。

不打开任何会话, 只读取左侧列表 DOM。相比解析平铺的 DOMSnapshot, JS 直读更稳健。
"""
from app.collector.readonly_cdp import ReadOnlyCDP

_CHAT_LIST_JS = """
(function() {
  var list = document.querySelector('[data-testid="chat-list"]');
  if (!list) return [];
  var rows = list.querySelectorAll('div[role="row"]');
  var out = [];
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i];
    var t = r.querySelector('span[title]');
    var name = t ? t.getAttribute('title') : null;
    var unread = 0;
    var badge = r.querySelector('span[aria-label*="unread"], span[aria-label*="未读"]');
    if (badge) {
      var n = parseInt((badge.textContent || '').trim(), 10);
      unread = isNaN(n) ? 1 : n;
    }
    var p = r.querySelector('span[dir="auto"]');
    var preview = p ? (p.textContent || '').trim() : '';
    out.push({name: name, unread: unread, preview: preview});
  }
  return out;
})()
"""


async def read_chat_list(cdp: ReadOnlyCDP) -> list[dict]:
    """返回 [{name, unread, preview}]; 失败返回 []。"""
    try:
        rows = await cdp.eval_async_readonly(_CHAT_LIST_JS)
    except Exception:
        return []
    return rows if isinstance(rows, list) else []
