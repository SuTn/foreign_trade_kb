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
    def __init__(self, cdp, store, vector_store, account_id="me", page=None):
        self.cdp = cdp
        self.store = store
        self.vector_store = vector_store
        self.account_id = account_id
        self.page = page  # 可选 Playwright page (自动扫描全部会话时用于打开会话)
        self._last_dom_hash = None
        self._matched_chats: set[str] = set()
        self._current_chat_id: str | None = None  # 由 slow_tick 推导的当前会话 JID

    async def fast_tick(self):
        """DOM 增量: hash 不变则跳过。心跳每个 tick 都写, 避免空闲时误判死。"""
        snap = await self.cdp.capture_snapshot()
        dom_msgs = parse_dom_snapshot_safe(snap, self._current_chat_id)
        h = hashlib.md5(json.dumps([(m.get("message_id") or m.get("id"), m.get("body")) for m in dom_msgs]).encode()).hexdigest()
        if h == self._last_dom_hash:
            write_status(settings.status_path, {"state": "running"})
            return  # 空闲不刷屏, 但仍更新心跳
        self._last_dom_hash = h
        # 合并 + upsert (DOM tick 也走 IDB 元数据合并, 此处简化为直接 upsert DOM 抓到的)
        for m in dom_msgs:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    async def slow_tick(self):
        """IDB 全量校准: IDB 提供消息身份/chatId, DOM 提供正文, 按 hex id 合并。"""
        from app.collector.idb_walk import walk_idb
        data = await walk_idb(self.cdp, self.account_id)
        dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
        merged = self._merge_idb_dom(data, dom_msgs)
        for m in merged:
            self._upsert_one(m)
        write_status(settings.status_path, {"state": "running", "last_sync": time.time()})

    def _merge_idb_dom(self, data: dict, dom_msgs: list[dict]) -> list[dict]:
        """IDB 消息 (id 形如 false_<jid>_<hex>) 与 DOM 消息 (hex id) 合并:
        our_jid = IDB 消息恒定的 to (自身账号); chat 取"不是自己"的一方 (入站=from, 出站=to);
        正文/发送人/时间优先取 DOM, 缺省回退 IDB。"""
        idb_by_hex = {}
        our_jid = None
        for m in data.get("messages", []):
            hex_part = (m.get("id") or "").rsplit("_", 1)[-1]
            if hex_part and hex_part not in idb_by_hex:
                idb_by_hex[hex_part] = m
            if our_jid is None and m.get("to"):
                our_jid = m["to"]
        merged = []
        for dom in dom_msgs:
            rec = idb_by_hex.get(dom.get("id"))
            chat, from_me = None, bool(dom.get("fromMe"))
            if rec:
                from_me = (rec.get("from") == our_jid) if our_jid else from_me
                chat = rec.get("from")
                if chat == our_jid or not chat:
                    chat = rec.get("to")
            chat = chat or self._current_chat_id
            name = data["chats"].get(chat) if chat else None
            if not name:
                name = data["contacts"].get((rec or {}).get("from"))
            merged.append({
                "id": dom.get("id"), "chatId": chat, "fromMe": from_me,
                "from": dom.get("from") or (rec or {}).get("from"),
                "timestamp": dom.get("timestamp") or (rec or {}).get("t") or 0,
                "type": "chat", "body": dom.get("body") or "",
                "body_present": bool(dom.get("body")), "name": name,
            })
        if merged:
            self._current_chat_id = merged[0]["chatId"] or self._current_chat_id
        return merged

    async def backfill_history(self, chat_id: str | None = None, max_scrolls: int = 10) -> int:
        """按需历史回溯: 滚动当前会话面板加载更早消息, 每次滚动后抓 DOM 增量入库。
        chat_id 为 None 时作用于当前打开的会话。返回新入库消息数。
        全程只读 (滚动是读取已接收历史, 非发送/输入)。"""
        ingested = 0
        prev_ids: set[str] = set()
        chat_id = chat_id or self._current_chat_id
        for _ in range(max_scrolls):
            scrolled = await self.cdp.scroll_conversation_up()
            if not scrolled:
                break  # 找不到会话面板, 无法回溯
            await asyncio.sleep(1.5)  # 等待 WhatsApp 加载历史
            dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), chat_id)
            new = [m for m in dom_msgs if (m.get("message_id") or m.get("id"))
                   and (m.get("message_id") or m.get("id")) not in prev_ids]
            if not new and prev_ids:
                break  # 滚动后无新消息, 已到顶
            prev_ids.update((m.get("message_id") or m.get("id")) for m in new)
            for m in new:
                if chat_id and m.get("chat_id") and m["chat_id"] != chat_id:
                    continue
                if self._upsert_one(m):
                    ingested += 1
        if ingested:
            write_status(settings.status_path, {"state": "running", "last_sync": time.time()})
        return ingested

    def _upsert_one(self, m) -> bool:
        """入库单条消息, 返回是否真正写入 (缺 chatId/消息 id 则跳过)。"""
        from app.storage.interfaces import Message
        chat_id = m.get("chatId")
        if not chat_id or not m.get("id"):
            return False  # 缺 chatId/消息 id 无法入库 (如未打开会话时的 DOM 增量)
        msg = Message(m["id"], self.account_id, chat_id, m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), int(time.time()))
        self.store.upsert_message(msg)
        # 客户匹配 (每会话一次, 建画像/建立 chat→customer 映射)
        if chat_id not in self._matched_chats:
            try:
                from app.profile.matcher import match_customer
                match_customer(self.store, self.account_id, chat_id, m.get("name"), chat_id)
            except Exception:
                pass  # 匹配失败不阻塞入库, 下次进程重启会重试
            self._matched_chats.add(chat_id)
        # 异步向量化 (chatId, day 分组) — 失败不阻塞
        try:
            day = time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"
            self.vector_store.upsert_message_vector(f"{msg.chat_id}:{day}", msg.body or "", {"chat_id": msg.chat_id, "day": day})
        except Exception:
            pass  # 下次 tick 重试
        return True
        # 异步向量化 (chatId, day 分组) — 失败不阻塞
        try:
            day = time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"
            self.vector_store.upsert_message_vector(f"{msg.chat_id}:{day}", msg.body or "", {"chat_id": msg.chat_id, "day": day})
        except Exception:
            pass  # 下次 tick 重试

    async def scan_all_chats(self, max_chats: int | None = None, settle: float | None = None) -> int:
        """自动扫描全部会话: 逐个打开会话读取可见正文入库 (供首次知识构建/周期校准)。
        依赖 Playwright page 原生可信 click; 注意会把未读消息标记为已读。
        返回新入库消息数。"""
        if self.page is None:
            return 0
        max_chats = max_chats or settings.auto_scan_max_chats
        settle = settle or settings.auto_scan_settle_sec
        from app.collector.idb_walk import walk_idb
        data = await walk_idb(self.cdp, self.account_id)
        try:
            total = await self.page.eval_on_selector_all(
                "[data-testid='chat-list'] div[role='row']", "els => els.length")
        except Exception:
            return 0
        ingested = 0
        row_sel = "[data-testid='chat-list'] div[role='row']"
        for i in range(min(total, max_chats)):
            if i % 5 == 0:
                write_status(settings.status_path, {"state": "running"})  # 长扫描期间保持心跳
            try:
                await self.page.locator(row_sel).nth(i).click(timeout=8000)
            except Exception:
                continue  # 行不可点 (虚拟列表抖动) 则跳过
            await asyncio.sleep(settle)
            dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
            for m in self._merge_idb_dom(data, dom_msgs):
                if self._upsert_one(m):
                    ingested += 1
        if ingested:
            write_status(settings.status_path, {"state": "running", "last_sync": time.time()})
        return ingested

    async def run(self):
        last_slow = 0.0
        last_scan = -1e9  # 启动即扫描全部会话 (首次知识构建)
        while True:
            await self.fast_tick()
            if time.time() - last_slow >= settings.slow_tick_sec:
                try:
                    await self.slow_tick()
                except Exception:
                    pass  # IDB 校准失败不阻塞主循环
                last_slow = time.time()
            if settings.auto_scan_chats and self.page is not None and time.time() - last_scan >= settings.auto_scan_interval_sec:
                try:
                    await self.scan_all_chats()
                except Exception:
                    pass  # 扫描失败不阻塞主循环
                last_scan = time.time()
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

def parse_dom_snapshot_safe(snap, chat_id=None):
    from app.collector.dom_snapshot import parse_dom_snapshot
    try:
        return parse_dom_snapshot(snap, chat_id)
    except Exception:
        return []
