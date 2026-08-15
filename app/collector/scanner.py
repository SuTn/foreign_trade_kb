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

def _msg_vector_key(chat_id: str, msg_id: str, ts: int) -> str:
    """per-message 向量键; msg_id 缺失时回退 (chatId, day, ts) 避免同日多条消息互相覆盖。"""
    if msg_id:
        return f"{chat_id}:{msg_id}"
    day = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "unknown"
    return f"{chat_id}:{day}:{ts}"


# ---- _merge_idb_dom 归一化辅助 (F: 拆成可测试小函数) ----

def _build_idb_index(data: dict) -> tuple[dict, str | None]:
    """从 IDB messages 建 hex→消息 索引, 并取自身账号 (恒定的 to)。"""
    idb_by_hex = {}
    our_jid = None
    for m in data.get("messages", []):
        hex_part = (m.get("id") or "").rsplit("_", 1)[-1]
        if hex_part and hex_part not in idb_by_hex:
            idb_by_hex[hex_part] = m
        if our_jid is None and m.get("to"):
            our_jid = m["to"]
    return idb_by_hex, our_jid


def _dom_sender_name(dom_msgs: list[dict]) -> str | None:
    """DOM 入站消息的发送人显示名 (供名字回退)。"""
    for dom in dom_msgs:
        if not dom.get("fromMe") and dom.get("from"):
            return dom.get("from")
    return None


def _build_lid_by_phone(lids: dict) -> dict:
    """反向: phone_jid → lid_jid, 供发送者名归一 (成员表可能以任一形态建键)。"""
    lid_by_phone = {}
    for _lid, _phone in lids.items():
        lid_by_phone.setdefault(_phone, _lid)
    return lid_by_phone


def _jid_forms(jid, phone_by_lid, lids, lid_by_phone):
    """LID/手机号 JID 归一候选形态: 原始 + phone_by_lid + lids(lid→phone) + 反向(phone→lid)。"""
    forms = [jid]
    for f in (phone_by_lid.get(jid), lids.get(jid), lid_by_phone.get(jid)):
        if f and f not in forms:
            forms.append(f)
    return forms


def _resolve_chat(rec: dict | None, our_jid: str | None, current_chat_id: str | None) -> str | None:
    """解析会话 JID: 群聊=群 JID; 私聊=非我方一方 (入站 from, 出站 to)。"""
    if not rec:
        return current_chat_id
    from_me = (rec.get("from") == our_jid) if our_jid else False
    to = rec.get("to")
    is_group = bool(to) and str(to).endswith("@g.us")
    if is_group:
        return to
    chat = rec.get("from")
    if chat == our_jid or not chat:
        chat = rec.get("to")
    return chat or current_chat_id


def _resolve_phone_chat(chat: str | None, phone_by_lid: dict, lids: dict) -> str | None:
    """LID → 真实手机号归一 (@lid → contact store 手机号, 回退 lid→phone_jid)。"""
    if not chat:
        return chat
    if phone_by_lid.get(chat):
        return phone_by_lid[chat]
    if chat in lids:
        return lids[chat]
    return chat


def _resolve_chat_name(chat, phone_chat, groups, chats, contacts, dom_sender_name) -> str | None:
    """会话名: 群名 → chats → contacts (含 LID 索引) → DOM 发送人显示名。"""
    name = None
    if chat and groups.get(chat, {}).get("name"):
        name = groups[chat]["name"]
    if not name and chat:
        name = chats.get(chat)
    if not name and chat:
        name = contacts.get(chat)
    if not name and phone_chat != chat:
        name = contacts.get(phone_chat)
    if not name:
        name = dom_sender_name
    return name


def _resolve_sender_name(rec, from_me, sender_jid, contacts, groups, chat, dom,
                         phone_by_lid, lids, lid_by_phone) -> str | None:
    """入站发送者显示名: contacts → 群成员表 → DOM 显示名 → JID 回退。
    发送者 JID 先经 LID/手机号归一, 再对归一与原始形态双查。"""
    if not rec or from_me:
        return None
    sender_name = None
    if sender_jid:
        for f in _jid_forms(sender_jid, phone_by_lid, lids, lid_by_phone):
            sender_name = contacts.get(f)
            if sender_name:
                break
    if not sender_name and chat and groups.get(chat):
        members = groups[chat]["members"]
        for f in _jid_forms(sender_jid, phone_by_lid, lids, lid_by_phone):
            sender_name = members.get(f)
            if sender_name:
                break
    if not sender_name:
        sender_name = dom.get("from")
    if not sender_name:
        sender_name = rec.get("from")
    return sender_name


class Scanner:
    def __init__(self, cdp, store, vector_store, account_id="me", page=None, llm=None, pw=None, context=None):
        self.cdp = cdp
        self.store = store
        self.vector_store = vector_store
        self.account_id = account_id
        self.page = page  # 可选 Playwright page (自动扫描全部会话时用于打开会话)
        self.llm = llm  # 可选 LLM: 自动画像抽取 (None=跳过, 供测试/无 key 环境)
        self._last_dom_hash = None
        self._matched_chats: set[str] = set()
        self._profile_pending: set[tuple[str, str]] = set()  # (customer_id, chat_id) 待抽取画像
        self._vector_pending: list[tuple[str, str, dict]] = []  # (key, text, metadata) 待向量化 (C1: 后台线程非阻塞)
        self._current_chat_id: str | None = None  # 由 slow_tick 推导的当前会话 JID
        self._cdp_failures = 0  # 连续致命 CDP 失败计数 (>=3 触发重建)
        self._pw = pw  # Playwright 实例 (重建时关闭旧实例)
        self._context = context  # 持久上下文 (重建时关闭旧实例)
        self._backfill_table_checked = False  # backfill_requests 表存在性已探测
        self._manual_scan_active = False  # 手动全量扫描进行中 (防御: Web 层 busy 判定不依赖此标志)
        self._scan_runtime = None  # 当前全量扫描进度 (供心跳写 status 时保留, I1)
        from app.storage.runtime_settings import RuntimeSettings
        self._rt = RuntimeSettings(store) if store is not None and hasattr(store, "conn") else None
        if self._rt is not None:
            try:
                self._rt.refresh()
            except Exception:
                self._rt = None  # store 无 settings 表 (测试/旧库) 时降级, 采集器不崩

    async def fast_tick(self):
        """DOM 增量: hash 不变则跳过。心跳每个 tick 都写, 避免空闲时误判死。"""
        snap = await self.cdp.capture_snapshot()
        dom_msgs = parse_dom_snapshot_safe(snap, self._current_chat_id)
        h = hashlib.md5(json.dumps([(m.get("message_id") or m.get("id"), m.get("body")) for m in dom_msgs]).encode()).hexdigest()
        if h == self._last_dom_hash:
            self._write_status_keep_scan({"state": "running"})
            return  # 空闲不刷屏, 但仍更新心跳
        self._last_dom_hash = h
        # 合并 + upsert (DOM tick 也走 IDB 元数据合并, 此处简化为直接 upsert DOM 抓到的)
        for m in dom_msgs:
            self._upsert_one(m)
        self._write_status_keep_scan({"state": "running", "last_sync": time.time()})

    async def slow_tick(self):
        """IDB 全量校准: IDB 提供消息身份/chatId, DOM 提供正文, 按 hex id 合并。"""
        from app.collector.idb_walk import walk_idb
        data = await walk_idb(self.cdp, self.account_id)
        self._persist_contacts(data)
        dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
        merged = self._merge_idb_dom(data, dom_msgs)
        for m in merged:
            self._upsert_one(m)
        self._write_status_keep_scan({"state": "running", "last_sync": time.time()})

    def _write_status_keep_scan(self, status: dict):
        """写 status.json, 但若全量扫描进行中 (self._scan_runtime), 合并保留 scan 进度,
        避免 fast/slow tick 心跳 (无 scan 字段) 覆盖清空进度 (I1)。"""
        if self._scan_runtime and "scan" not in status:
            status = {**status, "scan": self._scan_runtime}
        write_status(settings.status_path, status)

    def _persist_contacts(self, data: dict):
        """把 IDB contact store 落库 contacts 表:
        @lid 记录用 contact store 的真实 phone (若存在), 否则暂存 LID 数字;
        同时落真实手机号 jid 与 lid jid 两条索引, 供 resolve_phone 双向解析。"""
        from app.profile.matcher import phone_from_jid
        now = int(time.time())
        all_contacts = dict(data.get("contacts") or {})
        for lid, info in (data.get("lids") or {}).items():
            if lid not in all_contacts:
                all_contacts[lid] = info.get("name")
        n = 0
        for jid, name in all_contacts.items():
            phone = phone_from_jid(jid)
            real = data.get("phone_by_lid", {}).get(jid)
            if real:
                phone = phone_from_jid(real) or (real if real.isdigit() else None)  # @lid → contact 真实手机号
            self.store.conn.execute(
                "INSERT INTO contacts VALUES(?,?,?,?,?) ON CONFLICT(jid,account_id) DO UPDATE SET "
                "display_name=excluded.display_name, phone=excluded.phone, updated_at=excluded.updated_at",
                (jid, self.account_id, name, phone, now))
            n += 1
        self.store.conn.commit()

    def _merge_idb_dom(self, data: dict, dom_msgs: list[dict]) -> list[dict]:
        """IDB 消息 (id 形如 false_<jid>_<hex>) 与 DOM 消息 (hex id) 合并:
        our_jid = IDB 消息恒定的 to (自身账号); chat 取"不是自己"的一方 (入站=from, 出站=to);
        正文/发送人/时间优先取 DOM, 缺省回退 IDB。
        chat 名: 优先 chats[jid] → contacts[jid] (含 LID 索引) → DOM 发送人显示名。
        返回的 chatId 为真实手机号 JID (LID 经 lid_to_phone 解析), 便于客户匹配。
        归一化逻辑拆为模块级小函数 (F), 便于单测。"""
        idb_by_hex, our_jid = _build_idb_index(data)
        dom_sender_name = _dom_sender_name(dom_msgs)
        lids = data.get("lid_to_phone", {})
        phone_by_lid = data.get("phone_by_lid", {})
        groups = data.get("groups", {})
        lid_by_phone = _build_lid_by_phone(lids)

        merged = []
        for dom in dom_msgs:
            rec = idb_by_hex.get(dom.get("id"))
            from_me = bool(dom.get("fromMe"))
            if rec:
                from_me = (rec.get("from") == our_jid) if our_jid else from_me
            chat = _resolve_chat(rec, our_jid, self._current_chat_id)
            phone_chat = _resolve_phone_chat(chat, phone_by_lid, lids)
            name = _resolve_chat_name(chat, phone_chat, groups, data["chats"],
                                      data["contacts"], dom_sender_name)
            sender_jid = (rec or {}).get("from") if rec else None
            sender_name = _resolve_sender_name(rec, from_me, sender_jid, data["contacts"],
                                               groups, chat, dom, phone_by_lid, lids, lid_by_phone)
            kind = "group" if chat and str(chat).endswith("@g.us") else "single"
            merged.append({
                "id": dom.get("id"), "chatId": phone_chat, "fromMe": from_me,
                "from": dom.get("from") or (rec or {}).get("from"),
                "timestamp": dom.get("timestamp") or (rec or {}).get("t") or 0,
                "type": dom.get("type") or "chat", "body": dom.get("body") or "",
                "body_present": bool(dom.get("body")), "name": name,
                "sender_name": sender_name, "kind": kind,
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
        """入库单条消息, 返回是否真正写入 (缺 chatId/消息 id 则跳过)。
        消息入库时同步落会话元数据 (chats 表), 供会话枚举/显示名/同步状态查询。"""
        from app.storage.interfaces import Message, Chat
        chat_id = m.get("chatId")
        if not chat_id or not m.get("id"):
            return False  # 缺 chatId/消息 id 无法入库 (如未打开会话时的 DOM 增量)
        now = int(time.time())
        # kind 由会话 JID 判定: @g.us → 群聊 (display_name=群名), 其余保持 single
        kind = "group" if str(chat_id).endswith("@g.us") else "single"
        self.store.upsert_chat(Chat(chat_id, self.account_id, chat_id, m.get("name"), kind, now))
        msg = Message(m["id"], self.account_id, chat_id, m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), now,
                      m.get("sender_name"))
        self.store.upsert_message(msg)
        # 客户匹配 (每会话一次, 建画像/建立 chat→customer 映射) + 画像抽取调度
        if chat_id not in self._matched_chats:
            try:
                from app.profile.matcher import match_customer
                match_customer(self.store, self.account_id, chat_id, m.get("name"), chat_id)
                # 自动画像抽取 (每客户一次; LLM 在 run 循环 executor 中跑, 不阻塞本 tick)
                if self.llm is not None:
                    row = self.store.conn.execute(
                        "SELECT customer_id FROM customer_chat_map WHERE account_id=? AND chat_id=?",
                        (self.account_id, chat_id)).fetchone()
                    if row:
                        self._profile_pending.add((row["customer_id"], chat_id))
            except Exception:
                pass  # 匹配失败不阻塞入库, 下次进程重启会重试
            self._matched_chats.add(chat_id)
        # 向量化入队 (C1: 后台线程非阻塞, 不阻塞事件循环; 失败由 _flush_vectors_sync 静默跳过)
        try:
            key = _msg_vector_key(msg.chat_id, msg.id, msg.ts)
            self._vector_pending.append((key, msg.body or "",
                {"chat_id": msg.chat_id, "day": time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"}))
        except Exception:
            pass  # 入队失败不影响入库
        return True

    async def _capture_avatar(self, chat_id: str) -> None:
        """打开会话后抓取头像 (只读 GET → base64 → 落盘 → 更新 avatar_path); 失败静默。"""
        if self.page is None or not chat_id:
            return
        try:
            row = self.store.conn.execute(
                "SELECT customer_id FROM customer_chat_map WHERE account_id=? AND chat_id=?",
                (self.account_id, chat_id)).fetchone()
            if not row:
                return  # 未匹配客户, 跳过
            customer_id = row["customer_id"]
            r = await self.page.evaluate(
                "(function(){var h=document.querySelector('header[data-testid=\"conversation-header\"]');"
                "var imgs=h?h.querySelectorAll('img'):[];"
                "for(var i=0;i<imgs.length;i++){var s=imgs[i].src;if(s&&s.indexOf('data:')!==0)return {src:s};}"
                "return {src:''};})()")
            src = (r or {}).get("src")
            if not src:
                return
            data_url = await self.page.evaluate(
                "fetch(%s).then(function(r){return r.blob()}).then(function(b){return new Promise(function(res){"
                "var f=new FileReader();f.onloadend=function(){res(f.result)};f.readAsDataURL(b);})})" % json.dumps(src))
            if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:"):
                return
            mime = data_url.split(";", 1)[0].split(":", 1)[1]
            ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(mime)
            if not ext:
                return
            raw = __import__("base64").b64decode(data_url.split(",", 1)[1])
            if len(raw) > 2 * 1024 * 1024:
                return  # 超 2MB 丢弃
            path = settings.avatars_dir / f"{customer_id}.{ext}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.store.conn.execute(
                "UPDATE customers SET avatar_path=? WHERE id=?",
                (f"/avatars/{customer_id}.{ext}", customer_id))
            self.store.conn.commit()
        except Exception:
            pass  # 静默跳过, 下次扫描重试

    async def scan_all_chats(self, max_chats: int | None = None, settle: float | None = None,
                             on_progress=None) -> int:
        """自动扫描全部会话: 逐个打开会话读取可见正文入库 (供首次知识构建/周期校准)。
        依赖 Playwright page 原生可信 click; 注意会把未读消息标记为已读。
        on_progress(current, total, ingested): 每处理一个会话回调一次 (D4)。"""
        if self.page is None:
            return 0
        max_chats = max_chats or settings.auto_scan_max_chats
        settle = settle or settings.auto_scan_settle_sec
        from app.collector.idb_walk import walk_idb
        data = await walk_idb(self.cdp, self.account_id)
        self._persist_contacts(data)
        try:
            total = await self.page.eval_on_selector_all(
                "[data-testid='chat-list'] div[role='row']", "els => els.length")
        except Exception:
            return 0
        if on_progress:
            on_progress(0, min(total, max_chats), 0)  # 扫描前先报一次 total 已知
        ingested = 0
        row_sel = "[data-testid='chat-list'] div[role='row']"
        for i in range(min(total, max_chats)):
            if i % 5 == 0:
                self._write_status_keep_scan({"state": "running"})  # 长扫描期间保持心跳 (保留 scan 进度, W1)
            try:
                await self.page.locator(row_sel).nth(i).click(timeout=8000)
            except Exception:
                if on_progress:
                    on_progress(i + 1, min(total, max_chats), ingested)  # 跳过也推进进度
                continue
            await asyncio.sleep(settle)
            dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
            merged = self._merge_idb_dom(data, dom_msgs)
            for m in merged:
                if self._upsert_one(m):
                    ingested += 1
            if merged:
                await self._capture_avatar(self._current_chat_id)
            if on_progress:
                on_progress(i + 1, min(total, max_chats), ingested)
        if ingested:
            self._write_status_keep_scan({"state": "running", "last_sync": time.time()})
        return ingested

    async def run(self):
        last_slow = 0.0
        self.last_scan = -1e9  # 启动即扫描全部会话 (首次知识构建)
        backoff = 1.0
        while True:
            if self._rt is not None:
                try:
                    self._rt.refresh()  # 每轮刷新 (即时生效, 设计 §5.1)
                except Exception:
                    self._rt = None  # settings 表不可用则降级为 .env 默认
            try:
                await self.fast_tick()
                slow_sec = (self._rt.get_typed("slow_tick_sec", settings.slow_tick_sec)
                            if self._rt is not None else settings.slow_tick_sec)
                if time.time() - last_slow >= slow_sec:
                    if not getattr(self, "_vectors_cleared", False):
                        try:
                            self.vector_store.clear_message_vectors()
                        except Exception:
                            pass  # 清理失败不阻塞主循环, 下次重试
                        self._vectors_cleared = True
                    try:
                        await self.slow_tick()
                    except Exception:
                        pass  # IDB 校准失败不阻塞主循环
                    last_slow = time.time()
                auto_scan = (self._rt.get_typed("auto_scan_chats", settings.auto_scan_chats)
                             if self._rt else settings.auto_scan_chats)
                interval = (self._rt.get_typed("auto_scan_interval_sec", settings.auto_scan_interval_sec)
                            if self._rt else settings.auto_scan_interval_sec)
                if (not self._manual_scan_active and auto_scan and self.page is not None
                        and time.time() - self.last_scan >= interval):
                    try:
                        await self.scan_all_chats(
                            max_chats=(self._rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats)
                                       if self._rt else settings.auto_scan_max_chats),
                            settle=(self._rt.get_typed("auto_scan_settle_sec", settings.auto_scan_settle_sec)
                                    if self._rt else settings.auto_scan_settle_sec))
                    except Exception:
                        pass  # 扫描失败不阻塞主循环
                    self.last_scan = time.time()
                await self._drain_scan_requests()
                await self._drain_backfill_requests()
                await self._drain_profile_updates()
                await self._drain_vectors()
                backoff = 1.0  # 成功一轮, 重置退避
            except Exception as e:
                # 记录但不退出; CDP 致命失败连续累积触发重建
                await self._record_cdp_failure(e)
                try:
                    write_status(settings.status_path, {"state": "error", "error": str(e)[:200]})
                except Exception:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)  # 指数退避 1s→30s 上限
            await asyncio.sleep((self._rt.get_typed("fast_tick_sec", settings.fast_tick_sec)
                                 if self._rt else settings.fast_tick_sec)
                                + random.uniform(0, settings.fast_tick_jitter))

    async def _run_once(self):
        """供测试驱动单轮 fast_tick (不含 sleep 与退避): 异常分类/致命计数/阈值重建。"""
        try:
            await self.fast_tick()
        except Exception as e:
            await self._record_cdp_failure(e)

    async def _record_cdp_failure(self, e: Exception):
        """CDP 异常分类: 致命则累积计数 (>=3 触发重建), 瞬时异常归零计数。"""
        if self._is_cdp_fatal(e):
            self._cdp_failures += 1
            if self._cdp_failures >= 3:
                try:
                    await self._reconnect()
                except Exception:
                    pass  # 重连失败继续退避, 下一轮重试
        else:
            self._cdp_failures = 0

    def _is_cdp_fatal(self, e: Exception) -> bool:
        """判断异常是否属于 CDP/浏览器连接失效 (致命, 需重建)。宽匹配, 误判回退可重试。"""
        msg = str(e).lower()
        return any(k in msg for k in ("target closed", "connection", "session", "protocol error",
                                      "page crashed", "context was destroyed", "has been closed", "disconnected"))

    async def _reconnect(self):
        """重建浏览器连接并重置会话状态。失败抛回主循环继续退避。"""
        from app.collector.browser import launch_browser
        for old in (self._pw, self._context):
            try:
                if old is not None:
                    await old.close()
            except Exception:
                pass
        pw, context, page, cdp = await launch_browser()
        self._pw, self._context, self.page, self.cdp = pw, context, page, cdp
        self._current_chat_id = None
        self._last_dom_hash = None
        self._cdp_failures = 0
        self._matched_chats = set()

    async def _drain_profile_updates(self):
        """执行待抽取画像任务 (每客户一次): LLM 在 executor 中跑, 不阻塞事件循环。"""
        if not self._profile_pending:
            return
        loop = asyncio.get_running_loop()
        while self._profile_pending:
            customer_id, chat_id = self._profile_pending.pop()
            try:
                await loop.run_in_executor(
                    None, self._extract_profile_sync, customer_id, chat_id)
            except Exception:
                pass  # LLM 失败不阻塞采集, 下次新消息仍会尝试

    def _flush_vectors_sync(self):
        """同步消费待向量化队列 (在 executor 线程执行, 不阻塞事件循环)。
        交换出当前队列再处理, 避免与主循环 append 并发修改; 单条失败静默跳过。"""
        pending = self._vector_pending
        self._vector_pending = []
        for key, text, metadata in pending:
            try:
                self.vector_store.upsert_message_vector(key, text, metadata)
            except Exception:
                pass  # 单条失败不阻塞其余, 下次重建向量时补齐

    async def _drain_vectors(self):
        """把待向量化消息交给 executor 线程处理 (C1: 嵌入模型推理不阻塞事件循环)。"""
        if not self._vector_pending:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._flush_vectors_sync)
        except Exception:
            pass  # 向量化失败不阻塞采集主循环

    def _extract_profile_sync(self, customer_id: str, chat_id: str):
        """同步执行画像抽取 (供 executor 调用)。SQLite 连接不能跨线程, 故开新连接。"""
        from app.profile.service import refresh_customer_profile
        from app.storage.sqlite_store import SqliteStore
        worker = SqliteStore()
        try:
            refresh_customer_profile(worker, self.llm, customer_id, chat_id)
        finally:
            worker.conn.close()

    async def _drain_backfill_requests(self):
        """处理 Web 提交的按需历史回溯请求。表缺失静默跳过；失败 attempts+1 不标 done。"""
        if not self._backfill_table_checked:
            try:
                self.store.conn.execute("SELECT 1 FROM backfill_requests LIMIT 1").fetchall()
            except Exception:
                self._backfill_table_checked = True
                return
            self._backfill_table_checked = True
        try:
            rows = self.store.conn.execute(
                "SELECT id, chat_id, max_scrolls, attempts FROM backfill_requests WHERE done=0 AND attempts<3"
            ).fetchall()
        except Exception:
            return
        for r in rows:
            try:
                await self.backfill_history(chat_id=r["chat_id"], max_scrolls=r["max_scrolls"] or 10)
                self.store.conn.execute("UPDATE backfill_requests SET done=1 WHERE id=?", (r["id"],))
            except Exception:
                self.store.conn.execute(
                    "UPDATE backfill_requests SET attempts=attempts+1 WHERE id=?", (r["id"],))
        self.store.conn.commit()

    async def _drain_scan_requests(self):
        """处理 Web 提交的全量扫描请求 (与 backfill 同构, D1)。
        串行在主循环执行 scan_all_chats (天然与自动扫描互斥);
        执行前设 last_scan=now 跳过自动周期分支; 失败 attempts+1 (<3 下轮重试)。"""
        req = self.store.next_pending_scan_request()
        if not req:
            return
        self._manual_scan_active = True
        self.last_scan = time.time()
        try:
            self.store.mark_scan_request_running(req["id"])
            total = 0
            if self.page is not None:
                try:
                    total = await self.page.eval_on_selector_all(
                        "[data-testid='chat-list'] div[role='row']", "els => els.length")
                except Exception:
                    total = 0
            def on_progress(current, _total, ingested):
                self._scan_runtime = {"running": True, "current": current,
                                      "total": _total, "ingested": ingested}
                write_status(settings.status_path, {"state": "running", "scan": self._scan_runtime})
            max_chats = (self._rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats)
                         if self._rt is not None else settings.auto_scan_max_chats)
            settle = (self._rt.get_typed("auto_scan_settle_sec", settings.auto_scan_settle_sec)
                      if self._rt is not None else settings.auto_scan_settle_sec)
            ingested = await self.scan_all_chats(max_chats=max_chats, settle=settle,
                                                 on_progress=on_progress)
            if self.page is None:
                raise RuntimeError("采集器页面不可用, 无法全量扫描")  # 防假成功 (M2)
            self._scan_runtime = None  # 完成, 不再保留
            write_status(settings.status_path, {"state": "running", "last_sync": time.time(),
                "scan": {"running": False, "done": True, "ingested": ingested,
                         "finished_at": time.time(), "total": min(total, max_chats)}})
            self.store.mark_scan_request_done(req["id"])
        except Exception:
            self.store.bump_scan_request_attempts(req["id"])
        finally:
            self._manual_scan_active = False
            self._scan_runtime = None

def parse_dom_snapshot_safe(snap, chat_id=None):
    from app.collector.dom_snapshot import parse_dom_snapshot
    try:
        return parse_dom_snapshot(snap, chat_id)
    except Exception:
        return []
