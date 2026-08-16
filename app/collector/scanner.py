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
    """从 IDB messages 建 hex→消息 索引, 并取自身账号 (our_jid)。

    our_jid 判定优先级 (避免把出站消息的 to=对方 / 群消息的 to=群 误当自己):
    1. fromMe=True 的消息: from=自己 (私聊与群聊都成立);
    2. fromMe=False 且 to 非群: to=自己 (私聊入站);
    3. 无 fromMe 字段 (旧数据/测试): 沿用 to 启发式。
    """
    idb_by_hex = {}
    our_jid = None
    for m in data.get("messages", []):
        hex_part = (m.get("id") or "").rsplit("_", 1)[-1]
        if hex_part and hex_part not in idb_by_hex:
            idb_by_hex[hex_part] = m
        if our_jid is None:
            from_me = m.get("fromMe")
            if from_me is True and m.get("from"):
                our_jid = m["from"]
            elif from_me is False and m.get("to") and not str(m.get("to")).endswith("@g.us"):
                our_jid = m["to"]
            elif from_me is None and m.get("to"):
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


def _aggregate_chat_previews(rows: list[dict], name_to_id: dict[str, str]) -> list[dict]:
    """把左栏会话行按 chat_id 聚合为 chat_previews 行。

    重名/损坏名的会话可能多行解析到同一 chat_id (如两个「苏童」), 若逐行直写,
    后到的 unread=0 行会覆盖掉真实未读。这里按 chat_id 取未读最大值, 保留首个非空
    preview, 避免通知丢失/错位。
    """
    agg: dict[str, dict] = {}
    for r in rows:
        cid = name_to_id.get(r.get("name"))
        if not cid:
            continue
        cur = agg.setdefault(cid, {"unread": 0, "preview": None})
        cur["unread"] = max(cur["unread"], r.get("unread") or 0)
        if r.get("preview") and cur["preview"] is None:
            cur["preview"] = r["preview"]
    return [{"chat_id": cid, "unread_count": v["unread"], "preview": v["preview"]}
            for cid, v in agg.items()]


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
        self._chat_name_cache: dict[str, str] = {}  # chat_id → 权威显示名 (Part C 防串名污染)
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
        try:
            self._reconcile_idb_metadata(data)
        except Exception:
            pass  # 元数据纠偏失败不阻塞采集 (与 IDB 校准同口径)
        self._write_status_keep_scan({"state": "running", "last_sync": time.time()})

    def _reconcile_idb_metadata(self, data: dict) -> int:
        """用 IDB 权威元数据纠正已入库消息的 from_me / ts / chat_id (不依赖当前打开的会话)。

        fast_tick 走 DOM tail 启发式: 连续出站消息只有最后一条带 tail-out, 前面的会误判
        from_me=0; DOM data-pre-plain-text 时间戳只有分钟精度 (秒恒为 0), 同分钟多条消息
        ts 相同导致排序错乱; 会话归因靠 _current_chat_id (会漂移/串会话)。IDB 的
        m.id.fromMe / m.t / msgKey 里的会话 JID 才是权威值。本方法按 hex id 匹配 DB 行:
        from_me 只升不降 (与 upsert_message 同口径), ts 用精确秒覆盖, chat_id 用 msgKey
        的会话 JID 纠正 (搬回正确会话), 并清掉我方消息残留的 sender_name。不动 body。
        """
        if not hasattr(self.store, "conn"):
            return 0
        idb_by_hex, _ = _build_idb_index(data)
        if not idb_by_hex:
            return 0
        phone_by_lid = data.get("phone_by_lid", {})
        lids = data.get("lid_to_phone", {})
        rows = self.store.conn.execute(
            "SELECT id, chat_id, from_me, ts FROM messages WHERE account_id=?",
            (self.account_id,)).fetchall()
        changed = 0
        for r in rows:
            rec = idb_by_hex.get(r["id"])
            if not rec:
                continue
            ts = rec.get("t") or 0
            if ts <= 0:
                continue
            if ts > 10_000_000_000:  # 13 位毫秒时间戳 → 归一为秒
                ts //= 1000
            me = 1 if rec.get("fromMe") else 0
            new_me = max(r["from_me"], me)
            new_chat = self._resolve_authoritative_chat(rec, phone_by_lid, lids)
            if (ts == r["ts"] and new_me == r["from_me"]
                    and (not new_chat or new_chat == r["chat_id"])):
                continue
            moved = bool(new_chat and new_chat != r["chat_id"])
            self.store.conn.execute(
                "UPDATE messages SET from_me=?, ts=?, chat_id=COALESCE(?, chat_id), "
                "sender_name=CASE WHEN ?=1 THEN NULL ELSE sender_name END "
                "WHERE id=? AND account_id=?",
                (new_me, ts, new_chat, new_me, r["id"], self.account_id))
            if moved:
                self._requeue_message_vector(r["id"], new_chat)
            changed += 1
        if changed:
            self.store.conn.commit()
        return changed

    def _resolve_authoritative_chat(self, rec: dict, phone_by_lid: dict, lids: dict):
        """从 IDB 记录取权威会话 JID 并归一为 @c.us (LID→phone)。无 chatJid 返回 None。
        @lid 缺映射时回退查 contacts 表 (phone 列)。"""
        chat = rec.get("chatJid") if rec else None
        if not chat:
            return None
        chat = _resolve_phone_chat(chat, phone_by_lid, lids)
        if chat and str(chat).endswith("@lid"):
            try:
                r2 = self.store.conn.execute(
                    "SELECT phone FROM contacts WHERE jid=?", (chat,)).fetchone()
                if r2 and r2["phone"]:
                    chat = f'{r2["phone"]}@c.us'
            except Exception:
                pass
        return chat

    def _requeue_message_vector(self, msg_id: str, new_chat: str) -> None:
        """消息搬会话后, 把其向量重新入队到新会话 (body 从 DB 取, IDB body 加密)。
        旧会话里的向量成为陈旧数据, 仅影响旧会话 RAG 召回 (轻微, 重建向量时可清)。"""
        try:
            row = self.store.conn.execute(
                "SELECT body, ts FROM messages WHERE id=? AND account_id=?",
                (msg_id, self.account_id)).fetchone()
            if row and row["body"]:
                key = _msg_vector_key(new_chat, msg_id, row["ts"])
                self._vector_pending.append((key, row["body"],
                    {"chat_id": new_chat,
                     "day": time.strftime("%Y-%m-%d", time.gmtime(row["ts"])) if row["ts"] else "unknown"}))
        except Exception:
            pass  # 向量重入队失败不影响归属纠正

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
        fromMe 优先取 IDB 记录的 fromMe (WhatsApp 权威方向位), 缺省回退 DOM tail 信号;
        chat 取"不是自己"的一方 (入站=from, 出站=to)。
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
                # IDB 记录自带 fromMe (WhatsApp 权威方向位), 优先用之;
                # 不要用 from==our_jid 反推 (our_jid 启发式对出站/群消息会错)
                from_me = bool(rec.get("fromMe", from_me))
            chat = _resolve_chat(rec, our_jid, self._current_chat_id)
            if rec and rec.get("chatJid"):
                # 权威: msgKey 里的会话 JID (私聊=对方/群=群), 不依赖「当前打开的是哪个会话」,
                # 避免 follow/重名导致串会话 (消息写进错误客户)
                chat = rec["chatJid"]
            phone_chat = _resolve_phone_chat(chat, phone_by_lid, lids)
            if phone_chat and str(phone_chat).endswith("@lid"):
                # lid_to_phone 缺映射时回退查 contacts 表, 避免 @lid 会话 id 入库
                try:
                    r = self.store.conn.execute(
                        "SELECT phone FROM contacts WHERE jid=?", (phone_chat,)).fetchone()
                    if r and r["phone"]:
                        phone_chat = f'{r["phone"]}@c.us'
                except Exception:
                    pass
            name = _resolve_chat_name(chat, phone_chat, groups, data["chats"],
                                      data["contacts"], dom_sender_name)
            sender_jid = (rec or {}).get("from") if rec else None
            sender_name = _resolve_sender_name(rec, from_me, sender_jid, data["contacts"],
                                               groups, chat, dom, phone_by_lid, lids, lid_by_phone)
            kind = "group" if chat and str(chat).endswith("@g.us") else "single"
            merged.append({
                "id": dom.get("id"), "chatId": phone_chat, "fromMe": from_me,
                "from": dom.get("from") or (rec or {}).get("from"),
                # 时间戳优先 IDB 的 m.t (秒级精确), 回退 DOM data-pre-plain-text (仅分钟精度):
                # 否则同分钟多条消息 ts 相同, 排序错乱
                "timestamp": (rec or {}).get("t") or dom.get("timestamp") or 0,
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
        # Part C: 单聊显示名优先取 customers 画像名, 防 chats 表被 IDB 串名污染
        name = self._authoritative_chat_name(chat_id, m.get("name"))
        self.store.upsert_chat(Chat(chat_id, self.account_id, chat_id, name, kind, now))
        msg = Message(m["id"], self.account_id, chat_id, m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), now,
                      m.get("sender_name"))
        self.store.upsert_message(msg)
        # 客户匹配 (每会话一次, 建画像/建立 chat→customer 映射) + 画像抽取调度
        if chat_id not in self._matched_chats:
            try:
                from app.profile.matcher import match_customer
                match_customer(self.store, self.account_id, chat_id, name, chat_id)
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
        max_chats = min(max_chats, 200)  # 防御: 硬上限 200, 避免设置被改成超大值导致扫描卡死
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
        last_hb = 0.0
        for i in range(min(total, max_chats)):
            # 按时间写心跳 (每 ~10s 一次), 避免长扫描/点击超时期间被判「采集器异常」
            if time.time() - last_hb > 10:
                self._write_status_keep_scan({"state": "running"})
                last_hb = time.time()
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
        # Part C: 启动时清一次脏显示名 (按 customers 画像名回填), 之后靠 _upsert_one 防再次污染
        try:
            if hasattr(self.store, "reconcile_chat_names_from_customers"):
                self.store.reconcile_chat_names_from_customers()
        except Exception:
            pass
        while True:
            if self._rt is not None:
                try:
                    self._rt.refresh()  # 每轮刷新 (即时生效, 设计 §5.1)
                except Exception:
                    self._rt = None  # settings 表不可用则降级为 .env 默认
            try:
                # 优先响应「点谁同步谁」: 先切到 Web 正在查看的会话并立即抓取,
                # 再跑常规 fast_tick, 减少点开会话后的等待。
                await self._sync_follow()
                await self.fast_tick()
                slow_sec = (self._rt.get_typed("slow_tick_sec", settings.slow_tick_sec)
                            if self._rt is not None else settings.slow_tick_sec)
                if time.time() - last_slow >= slow_sec:
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
                await self._drain_send_requests()
                await self._sync_chat_previews()
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

    def _chat_lookup_query(self, chat_id: str) -> str | None:
        """发送/跟随时用于定位会话的查询串 (聊天列表行标题匹配): 显示名优先, 回退手机号。"""
        from app.profile.matcher import phone_from_jid
        try:
            r = self.store.conn.execute(
                "SELECT display_name FROM chats WHERE id=?", (chat_id,)).fetchone()
            if r and r["display_name"]:
                return r["display_name"]
        except Exception:
            pass
        if chat_id and str(chat_id).endswith("@lid"):
            try:
                r2 = self.store.conn.execute(
                    "SELECT phone FROM contacts WHERE jid=?", (chat_id,)).fetchone()
                if r2 and r2["phone"]:
                    return r2["phone"]
            except Exception:
                pass
        return phone_from_jid(chat_id)

    def _authoritative_chat_name(self, chat_id: str, fallback_name: str | None) -> str | None:
        """单聊 (@c.us) 显示名优先取 customers 画像名 (按手机号), 防 chats 表被 IDB 串名污染。
        群聊/无客户映射/查询失败回退 fallback_name; 结果按 chat_id 缓存 (每会话只查一次)。"""
        if chat_id in self._chat_name_cache:
            return self._chat_name_cache[chat_id]
        name = fallback_name
        if chat_id and str(chat_id).endswith("@c.us") and hasattr(self.store, "conn"):
            digits = str(chat_id).rsplit("@", 1)[0]
            if digits.isdigit():
                try:
                    r = self.store.conn.execute(
                        "SELECT display_name FROM customers WHERE phone=? "
                        "AND display_name IS NOT NULL AND display_name != ''",
                        (digits,)).fetchone()
                    if r and r["display_name"]:
                        name = r["display_name"]
                except Exception:
                    pass
        self._chat_name_cache[chat_id] = name
        return name

    def _normalize_send_target(self, chat_id: str) -> str:
        """把发送目标 chat_id 归一为 @c.us 形态 (与 msgKey chatJid 归一一致), 供发送前 JID 校验。"""
        if not chat_id:
            return ""
        if str(chat_id).endswith("@lid") and hasattr(self.store, "conn"):
            try:
                r = self.store.conn.execute(
                    "SELECT phone FROM contacts WHERE jid=?", (chat_id,)).fetchone()
                if r and r["phone"]:
                    return f'{r["phone"]}@c.us'
            except Exception:
                pass
        return chat_id

    async def _current_chat_authoritative_jid(self) -> str | None:
        """返回当前打开会话的权威 JID (来自 msgKey 的 chatJid, 而非名字/current_chat_id 推断)。
        无法确定时返回 None (发送前用它做 fail-closed 校验, 防发错人)。"""
        from app.collector.idb_walk import walk_idb
        try:
            data = await walk_idb(self.cdp, self.account_id)
            idb_by_hex, _ = _build_idb_index(data)
            dom_msgs = parse_dom_snapshot_safe(await self.cdp.capture_snapshot(), self._current_chat_id)
            phone_by_lid = data.get("phone_by_lid", {})
            lids = data.get("lid_to_phone", {})
            for dom in dom_msgs:
                rec = idb_by_hex.get(dom.get("id"))
                if not rec or not rec.get("chatJid"):
                    continue
                jid = self._resolve_authoritative_chat(rec, phone_by_lid, lids)
                if jid:
                    return jid
            return None
        except Exception:
            return None

    async def _drain_send_requests(self):
        """消费发送任务 (纯文字)。send_enabled 关闭时直接 failed (防绕过)。"""
        if self.page is None:
            return
        enabled = (self._rt.get_typed("send_enabled", False)
                   if self._rt is not None else False)
        req = self.store.next_pending_send_request()
        if not req:
            return
        if not enabled:
            self.store.mark_send_request_failed(req["id"], "发送功能未开启 (send_enabled=false)")
            return
        self.store.mark_send_request_running(req["id"])
        try:
            from app.collector.sender import open_chat, send_text
            query = self._chat_lookup_query(req["chat_id"])
            if not query:
                self.store.mark_send_request_failed(req["id"], "无法定位会话 (无显示名/手机号)")
                return
            opened = await open_chat(self.page, query)
            if not opened:
                # 打不开目标会话就绝不发送, 防止发到当前打开的其他会话
                self.store.mark_send_request_failed(req["id"], "无法打开目标会话 (搜索结果为空)")
                return
            # Part B: 发送前核对打开会话的权威 JID (msgKey chatJid), 防止重名/脏名导致发错人
            actual = await self._current_chat_authoritative_jid()
            expected = self._normalize_send_target(req["chat_id"])
            if not actual or actual != expected:
                self.store.mark_send_request_failed(
                    req["id"], f"会话校验失败, 已中止发送 (目标={expected}, 实际={actual})")
                return
            await send_text(self.page, req["text"])
            self.store.mark_send_request_done(req["id"])
        except Exception as e:
            self.store.bump_send_request_attempts(req["id"], str(e)[:300])

    async def _sync_follow(self):
        """读取 Web 设置的 follow_chat, 与当前会话不同则切换过去。"""
        if self.page is None or self._rt is None:
            return
        follow = self._rt.get("collector_follow_chat")
        if not follow or follow == self._current_chat_id:
            return
        query = self._chat_lookup_query(follow)
        if not query:
            msg = f"无法定位会话 {follow}"
            print(f"[follow] {msg}", flush=True)
            self._record_follow(msg)
            return
        from app.collector.sender import open_chat
        try:
            opened = await open_chat(self.page, query)
        except Exception as e:
            msg = f"打开会话异常 {follow}: {e}"
            print(f"[follow] {msg}", flush=True)
            self._record_follow(msg)
            return
        if opened:
            self._current_chat_id = follow  # 仅切换成功才更新, 避免消息归属错位
            msg = f"已切换到 {follow} (query={query})"
            await self._sync_open_chat_now()  # 打开后立即抓取, 不等下一轮 fast_tick
        else:
            msg = f"打开会话失败 {follow} (query={query})"
        print(f"[follow] {msg}", flush=True)
        self._record_follow(msg)

    async def _sync_open_chat_now(self):
        """打开会话后立即抓一次 DOM 入库, 缩短「点进去等半天才出消息」的延迟。

        等 WhatsApp 切换并渲染消息后立刻 capture+upsert; 失败静默, 下一轮 fast_tick 兜底。
        """
        try:
            settle = (self._rt.get_typed("auto_scan_settle_sec", settings.auto_scan_settle_sec)
                      if self._rt is not None else settings.auto_scan_settle_sec)
            await asyncio.sleep(min(settle, 1.0))  # 给 WhatsApp 渲染消息留出时间 (上限 1s)
            self._last_dom_hash = None  # 强制重抓新会话 DOM (越过 hash 去重)
            await self.fast_tick()
        except Exception:
            pass  # 立即抓取失败不阻塞主循环; 下一轮 fast_tick 会再试

    def _record_follow(self, msg: str):
        """把 follow 结果写进 status.json 便于诊断 (不覆盖其他字段)。"""
        try:
            s = read_status(settings.status_path) or {}
            s["follow"] = {"msg": msg, "ts": time.time()}
            write_status(settings.status_path, s)
        except Exception:
            pass

    async def _sync_chat_previews(self):
        """读左栏会话列表 → 映射 chat_id → 写 chat_previews。失败静默。"""
        if self.cdp is None:
            return
        try:
            from app.collector.chat_list import read_chat_list
            rows = await read_chat_list(self.cdp)
        except Exception:
            return
        if not rows:
            return
        try:
            name_to_id = self.store.resolve_chat_ids_by_names(
                [r.get("name") for r in rows if r.get("name")])
        except Exception:
            return
        previews = _aggregate_chat_previews(rows, name_to_id)
        if previews:
            try:
                self.store.upsert_chat_previews(previews)
            except Exception:
                pass

def parse_dom_snapshot_safe(snap, chat_id=None):
    from app.collector.dom_snapshot import parse_dom_snapshot
    try:
        return parse_dom_snapshot(snap, chat_id)
    except Exception:
        return []
