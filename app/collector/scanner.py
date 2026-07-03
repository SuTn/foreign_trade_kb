# app/collector/scanner.py
import asyncio, json, random, time, hashlib
from pathlib import Path
from app.config import settings

def write_status(path: Path, status: dict):
    status["last_heartbeat"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status), encoding="utf-8")

def read_status(path: Path) -> dict | None:
    if not path.exists(): return None
    return json.loads(path.read_text(encoding="utf-8"))

def is_alive(path: Path, timeout: float | None = None) -> bool:
    timeout = timeout or settings.heartbeat_timeout_sec
    s = read_status(path)
    if not s: return False
    return (time.time() - s.get("last_heartbeat", 0)) < timeout

class Scanner:
    def __init__(self, cdp, store, vector_store, account_id="me"):
        self.cdp = cdp
        self.store = store
        self.vector_store = vector_store
        self.account_id = account_id
        self._last_dom_hash = None

    async def fast_tick(self):
        """DOM 增量: hash 不变则跳过。"""
        snap = self.cdp.capture_snapshot()
        dom_msgs = parse_dom_snapshot_safe(snap)
        h = hashlib.md5(json.dumps([(m["message_id"], m["body"]) for m in dom_msgs]).encode()).hexdigest()
        if h == self._last_dom_hash:
            return  # 空闲不刷屏
        self._last_dom_hash = h
        # 合并 + upsert (DOM tick 也走 IDB 元数据合并, 此处简化为直接 upsert DOM 抓到的)
        for m in dom_msgs:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    async def slow_tick(self):
        """IDB 全量校准。"""
        from app.collector.idb_walk import walk_idb
        from app.collector.merger import merge_messages
        data = walk_idb(self.cdp, self.account_id)
        dom_msgs = parse_dom_snapshot_safe(self.cdp.capture_snapshot())
        merged = merge_messages(data["messages"], dom_msgs)
        for m in merged:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    async def backfill_history(self, chat_id: str | None = None, max_scrolls: int = 10) -> int:
        """按需历史回溯: 滚动当前会话面板加载更早消息, 每次滚动后抓 DOM 增量入库。
        chat_id 为 None 时作用于当前打开的会话。返回新入库消息数。
        全程只读 (滚动是读取已接收历史, 非发送/输入)。"""
        ingested = 0
        prev_ids: set[str] = set()
        for _ in range(max_scrolls):
            scrolled = self.cdp.scroll_conversation_up()
            if not scrolled:
                break  # 找不到会话面板, 无法回溯
            await asyncio.sleep(1.5)  # 等待 WhatsApp 加载历史
            dom_msgs = parse_dom_snapshot_safe(self.cdp.capture_snapshot())
            new = [m for m in dom_msgs if (m.get("message_id") or m.get("id"))
                   and (m.get("message_id") or m.get("id")) not in prev_ids]
            if not new and prev_ids:
                break  # 滚动后无新消息, 已到顶
            prev_ids.update((m.get("message_id") or m.get("id")) for m in new)
            for m in new:
                if chat_id and m.get("chat_id") and m["chat_id"] != chat_id:
                    continue
                self._upsert_one(m)
                ingested += 1
        if ingested:
            write_status(settings.status_path, {"state": "running", "last_sync": time.time()})
        return ingested

    def _upsert_one(self, m):
        from app.storage.interfaces import Message
        msg = Message(m["id"], self.account_id, m["chatId"], m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), int(time.time()))
        self.store.upsert_message(msg)
        # 异步向量化 (chatId, day 分组) — 失败不阻塞
        try:
            day = time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"
            self.vector_store.upsert_message_vector(f"{msg.chat_id}:{day}", msg.body or "", {"chat_id": msg.chat_id, "day": day})
        except Exception:
            pass  # 下次 tick 重试

    async def run(self):
        while True:
            await self.fast_tick()
            await self._drain_backfill_requests()
            await asyncio.sleep(settings.fast_tick_sec + random.uniform(0, settings.fast_tick_jitter))

    async def _drain_backfill_requests(self):
        """处理 Web 提交的按需历史回溯请求 (backfill_requests 表)。"""
        try:
            rows = self.store.conn.execute(
                "SELECT id, chat_id, max_scrolls FROM backfill_requests WHERE done=0"
            ).fetchall()
        except Exception:
            return  # 表不存在 (旧库) 则跳过
        for r in rows:
            try:
                await self.backfill_history(
                    chat_id=r["chat_id"], max_scrolls=r["max_scrolls"] or 10
                )
            except Exception:
                pass
            self.store.conn.execute(
                "UPDATE backfill_requests SET done=1 WHERE id=?", (r["id"],)
            )
            self.store.conn.commit()

def parse_dom_snapshot_safe(snap):
    from app.collector.dom_snapshot import parse_dom_snapshot
    try:
        return parse_dom_snapshot(snap)
    except Exception:
        return []
