---
change: collector-message-integrity
design-doc: docs/superpowers/specs/2026-08-11-collector-message-integrity-design.md
base-ref: b5113428e1f06972729d92c2587ae34e7bb26ff8
---

# collector-message-integrity 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让采集器完整记录群聊消息的发送者身份 —— 数据层新增 `sender_name` 列、IDB 读取 `group-metadata`、群聊识别与发送者名解析入库、DOM 引用/媒体净化、画像摘要与 Web 聊天页按发送者标注，且单聊行为零回归。

**Architecture:** 采集器双进程只读架构（`ReadOnlyCDP` + 页面 JS 读 IndexedDB + DOMSnapshot 解析）。本变更沿既有数据流展开：`idb_walk` 放开 `group-metadata` store 产出 `groups` 映射 → `scanner._merge_idb_dom` 在合并层做群聊识别（`@g.us` → `kind=group`）与发送者名优先级解析（contacts → 群成员表 → DOM 显示名 → JID 回退）→ `sqlite_store` 幂等迁移写入 `sender_name` → `profile.build_chat_summary` 与 `chat_messages.html` 按 `chats.kind` 分支展示。`dom_snapshot` 保持纯 DOM 信号输出，引用/媒体净化与 fromMe 仲裁（IDB 权威）均在各自职责层完成。

**Tech Stack:** Python 3 / FastAPI + Jinja2 + HTMX / SQLite（WAL + FTS5，`try/except OperationalError` 幂等迁移）/ ChromaDB / Playwright CDP。测试：pytest（`tests/`，现 102 例）。

## Global Constraints

- **采集器只读**：`app/collector/` 下所有代码不得出现 `Input.dispatch`、`Page.navigate`、`sendMessage`、`Input.insertText`（`tests/integration/test_readonly_constraint.py` 校验）。新增 JS 必须用 `readonly` 事务。
- **本地优先**：所有改动只在本地 SQLite（WAL）+ Chroma，无网络写操作。
- **单聊零回归**：`kind != "group"` 的所有路径（存储、合并、摘要、Web）输出与现状完全一致。
- **防御式降级**：`group-metadata` store 缺失/异常、引用块 testid 漂移、媒体 testid 未知 → 静默回退，不得阻塞入库或抛异常。
- **构建/验证命令**（`.comet.yaml`）：
  - build：`.venv/Scripts/python.exe -m compileall -q app`
  - verify：`.venv/Scripts/python.exe -m pytest -q`
- **字段命名**：新增列/字段统一为 `sender_name`；群聊类型字符串统一为 `group`（单聊沿用 `single`）。
- 任务按顺序执行；每任务独立测试 + 提交，全部完成后在 tasks.md 勾选。

---

### Task 1: 数据层 —— `messages.sender_name` 列 + `Message` 模型扩展

**Files:**
- Modify: `app/storage/schema.sql:6-9`（messages 表新增列）
- Modify: `app/storage/interfaces.py:10-14`（Message dataclass）
- Modify: `app/storage/sqlite_store.py:19-26,37-47,142-144`（幂等迁移 + upsert + row 解析）
- Modify: `tests/web/test_routes.py:12-15,305`（`INSERT INTO messages` 由 10 列改为 11 列）
- Test: `tests/storage/test_sqlite_store.py`

**Interfaces:**
- Consumes: 现有 `Message` dataclass 全部 10 个字段（位置参数兼容性必须保留）。
- Produces: `Message.sender_name: str | None = None`（**追加为最后一个字段，带默认值**，保证既有位置参数构造点不受影响）；`SqliteStore._row_to_msg`/`upsert_message` 读写新列；`schema.sql` messages 表含 `sender_name TEXT`。

- [x] **Step 1: 更新 schema**

`app/storage/schema.sql` 的 messages 建表语句改为：

```sql
CREATE TABLE IF NOT EXISTS messages(
  id TEXT, account_id TEXT, chat_id TEXT, from_me INTEGER, sender_jid TEXT,
  ts INTEGER, type TEXT, body TEXT, body_present INTEGER, ingested_at INTEGER,
  sender_name TEXT,
  PRIMARY KEY(id, account_id));
```

- [x] **Step 2: 扩展 `Message` dataclass**

`app/storage/interfaces.py` 第 10-14 行改为（字段顺序不变，`sender_name` 追加在最后并带默认值）：

```python
@dataclass
class Message:
    id: str; account_id: str; chat_id: str; from_me: bool
    sender_jid: str | None; ts: int; type: str | None
    body: str | None; body_present: bool; ingested_at: int
    sender_name: str | None = None
```

- [x] **Step 3: 幂等迁移 + 读写新列**

`app/storage/sqlite_store.py`：

1. `_init_schema` 末尾（avatar_path 迁移块之后）追加：

```python
        try:
            self.conn.execute("ALTER TABLE messages ADD COLUMN sender_name TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在 (新库 schema.sql 已含) — 幂等
```

2. `upsert_message` 的 SQL 与参数改为 11 列（含 ON CONFLICT 更新 `sender_name`）：

```python
        self.conn.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id,account_id) DO UPDATE SET "
            "from_me=excluded.from_me, sender_jid=excluded.sender_jid, ts=excluded.ts, type=excluded.type, "
            "body=COALESCE(excluded.body, body), body_present=excluded.body_present, sender_name=excluded.sender_name",
            (msg.id, msg.account_id, msg.chat_id, int(msg.from_me), msg.sender_jid,
             msg.ts, msg.type, msg.body, int(msg.body_present), msg.ingested_at, msg.sender_name))
```

3. `_row_to_msg` 末参数追加 `r["sender_name"]`：

```python
    def _row_to_msg(self, r):
        return Message(r["id"], r["account_id"], r["chat_id"], bool(r["from_me"]), r["sender_jid"],
                       r["ts"], r["type"], r["body"], bool(r["body_present"]), r["ingested_at"],
                       r["sender_name"])
```

- [x] **Step 4: 修正既有测试的 10 列 INSERT（schema 列数变化导致的回归）**

`tests/web/test_routes.py` 第 12-15 行 `test_stats_endpoint` 的 executemany：

```python
    store.conn.executemany(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [("m1","me","ch1",0,"x",1000,"chat","hi",1,0,None),
         ("m2","me","ch1",0,"x",2000,"chat","yo",1,0,None),
         ("m3","me","ch2",0,"y",1500,"chat","a",1,0,None)])
```

第 305 行 `test_home_shows_stats`：

```python
    store.conn.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)",("m1","me","ch1",0,"x",1000,"chat","hi",1,0,None))
```

- [x] **Step 5: 写失败测试**

追加到 `tests/storage/test_sqlite_store.py`：

```python
def test_old_schema_gets_sender_name_column(tmp_data):
    """旧库 (messages 无 sender_name) 打开后自动迁移出该列, 且幂等。"""
    p = tmp_data / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE messages(id TEXT, account_id TEXT, chat_id TEXT, from_me INTEGER, "
              "sender_jid TEXT, ts INTEGER, type TEXT, body TEXT, body_present INTEGER, "
              "ingested_at INTEGER, PRIMARY KEY(id, account_id))")
    c.commit(); c.close()
    for _ in range(2):  # 同一旧库重复打开 → 迁移幂等
        store2 = SqliteStore(p)
        cols = [r[1] for r in store2.conn.execute("PRAGMA table_info(messages)").fetchall()]
        assert "sender_name" in cols
        store2.conn.close()

def test_upsert_message_roundtrips_sender_name(tmp_data):
    s = SqliteStore()
    m = Message("m1", "a1", "c1", False, "x@w", 1000, "chat", "hello", True,
                int(time.time()), "Sonya")
    s.upsert_message(m)
    rows = s.list_messages("c1")
    assert rows[0].sender_name == "Sonya"
```

- [x] **Step 6: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/storage/test_sqlite_store.py`
Expected: `test_upsert_message_roundtrips_sender_name` FAIL（`TypeError: Message.__init__() got an unexpected keyword argument 'sender_name'`）—— 因为 Task 1 代码尚未全部就位时先看失败。确认后继续实现即可（本任务代码已在前序步骤给出）。

- [x] **Step 7: 运行并确认通过**

Run: `.venv/Scripts/python.exe -m pytest -q tests/storage/test_sqlite_store.py tests/web/test_routes.py`
Expected: 全部 PASS（含修正后的 `test_stats_endpoint`/`test_home_shows_stats`）。

- [x] **Step 8: 提交**

```bash
git add app/storage/schema.sql app/storage/interfaces.py app/storage/sqlite_store.py tests/web/test_routes.py tests/storage/test_sqlite_store.py
git commit -m "feat: messages 表新增 sender_name 列 (幂等迁移) + Message 模型扩展"
```

---

### Task 2: IDB 群聊元数据读取（group-metadata → groups 映射）

**Files:**
- Modify: `app/collector/idb_walk.py:38-67,74-107`
- Test: `tests/collector/test_idb_walk.py`（FakeCDP 识别键增加 `group-metadata`；新增 2 个测试）

**Interfaces:**
- Consumes: `ReadOnlyCDP.eval_async_readonly(js)`（现有门面）、`settings.idb_stores`（默认已含 `"group-metadata"`）。
- Produces: `walk_idb(cdp, account_id) -> dict` 返回值新增键 `groups: {g_jid: {"name": str|None, "members": {member_jid: name|None}}}`；`_read_store_js("group-metadata")` 返回只读 IIFE。后续 Task 3 依赖 `data["groups"]`。

- [ ] **Step 1: `_read_store_js` 增加 group-metadata 映射**

`app/collector/idb_walk.py` 第 54 行的 `else:  # contact` 之前插入 `elif` 分支（防御式提取：群 JID 兼容对象/字符串、成员数组逐个取 jid+name、缺失字段为 null）：

```python
    elif store == "group-metadata":
        mapping = (
            "function(g) {"
            " var idv = g.id;"
            " var jid = typeof idv === 'string' ? idv : (idv && (idv._serialized || idv.user));"
            " var members = (g.members || []).map(function(m) {"
            "   var mv = m.jid;"
            "   var mj = typeof mv === 'string' ? mv : (mv && (mv._serialized || mv.user));"
            "   return {jid: mj, name: m.name || m.pushname || null};"
            " });"
            " return {id: jid, name: g.name || g.formattedTitle || null, members: members}; }"
        )
```

- [ ] **Step 2: `walk_idb` 放开 group-metadata 并构建 groups**

`app/collector/idb_walk.py`：

1. 删除第 82-83 行的 `if store == "group-metadata": continue` 跳过逻辑。
2. 第 79-80 行返回字典增加 `"groups": {}`。
3. `elif store == "contact":` 之前插入分支：

```python
        elif store == "group-metadata":
            for r in rows:
                gid = r.get("id")
                if not gid:
                    continue
                members = {}
                for m in r.get("members") or []:
                    mj = m.get("jid")
                    if mj:
                        members[mj] = m.get("name")
                result["groups"][gid] = {"name": r.get("name"), "members": members}
```

4. 同步更新函数 docstring，说明返回值含 `groups: {g_jid: {name, members}}`。

- [ ] **Step 3: 更新既有 FakeCDP 识别键**

`tests/collector/test_idb_walk.py` 第 13 行的元组改为：

```python
        for key in ("message", "chat", "contact", "group-metadata"):
```

（既有 `test_walk_idb_builds_chats_contacts_messages` 不受影响：group-metadata 无数据时返回空列表 → `groups` 为空。）

- [ ] **Step 4: 写失败测试**

追加到 `tests/collector/test_idb_walk.py`：

```python
async def test_walk_idb_reads_group_metadata(monkeypatch):
    monkeypatch.setattr(idb_walk.settings, "idb_stores",
                        ["message", "chat", "contact", "group-metadata"])
    cdp = FakeCDP({
        "group-metadata": [
            {"id": "120363123456789@g.us", "name": "海外采购群",
             "members": [{"jid": "8615976909619@c.us", "name": "Sonya"},
                         {"jid": "8616111222333@c.us", "name": None}]},
        ],
    })
    data = await idb_walk.walk_idb(cdp, "me")
    g = data["groups"]["120363123456789@g.us"]
    assert g["name"] == "海外采购群"
    assert g["members"] == {"8615976909619@c.us": "Sonya", "8616111222333@c.us": None}


async def test_walk_idb_group_metadata_missing_silently_empty(monkeypatch):
    """store 缺失 (null) → groups 空字典, 不抛异常。"""
    monkeypatch.setattr(idb_walk.settings, "idb_stores",
                        ["message", "chat", "contact", "group-metadata"])
    cdp = FakeCDP({"group-metadata": None})
    data = await idb_walk.walk_idb(cdp, "me")
    assert data["groups"] == {}
```

- [ ] **Step 5: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_idb_walk.py -v`
Expected: 两个新测试 FAIL（`AttributeError`/断言失败：walk 结果无 `groups` 键或为空），既有测试通过。

- [ ] **Step 6: 运行并确认通过**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_idb_walk.py`
Expected: 全部 PASS。

- [ ] **Step 7: 提交**

```bash
git add app/collector/idb_walk.py tests/collector/test_idb_walk.py
git commit -m "feat: idb_walk 读取 group-metadata → groups 映射 (防御式, 静默降级)"
```

---

### Task 3: 采集器群聊识别 + 发送者解析入库

**Files:**
- Modify: `app/collector/scanner.py:82-136`（`_merge_idb_dom`：群聊会话=群 JID、发送者名优先级解析、groups 群名、输出 `sender_name`/`kind`）
- Modify: `app/collector/scanner.py:165-178`（`_upsert_one`：kind 按 `@g.us` 计算、`Message` 携带 `sender_name`）
- Test: `tests/collector/test_scanner.py`

**Interfaces:**
- Consumes: Task 2 产出 `data["groups"]`；`Message`（Task 1）11 字段位置构造。
- Produces: `_merge_idb_dom(data, dom_msgs) -> list[dict]` 每条记录新增 `sender_name: str|None`、`kind: "group"|"single"`；群聊 `chatId` = 群 JID（`@g.us`）。`_upsert_one(m)` 对 `@g.us` 会话写入 `chats.kind="group"`、群名作 `display_name`，`Message.sender_name` 随行入库。Task 5/6 依赖 `chats.kind` 与 `messages.sender_name`。

- [ ] **Step 1: `_merge_idb_dom` 群聊识别 + 发送者解析**

`app/collector/scanner.py` 第 104-133 行循环体整体替换为：

```python
        groups = data.get("groups", {})
        merged = []
        for dom in dom_msgs:
            rec = idb_by_hex.get(dom.get("id"))
            chat, from_me = None, bool(dom.get("fromMe"))
            if rec:
                from_me = (rec.get("from") == our_jid) if our_jid else from_me
                to = rec.get("to")
                is_group = bool(to) and str(to).endswith("@g.us")
                if is_group:
                    chat = to  # 群聊: 会话 = 群 JID, 发送者为 from
                else:
                    chat = rec.get("from")
                    if chat == our_jid or not chat:
                        chat = rec.get("to")
            chat = chat or self._current_chat_id
            phone_chat = chat
            if chat and phone_by_lid.get(chat):
                phone_chat = phone_by_lid[chat]  # @lid → contact store 的真实手机号 (无@)
            elif chat and chat in lids:
                phone_chat = lids[chat]  # 回退: lid→phone_jid 映射
            # 会话名: group-metadata 群名 → chats → contacts (含 LID 索引) → DOM 发送人显示名
            name = None
            if chat and groups.get(chat, {}).get("name"):
                name = groups[chat]["name"]
            if not name and chat:
                name = data["chats"].get(chat)
            if not name and chat:
                name = data["contacts"].get(chat)
            if not name and phone_chat != chat:
                name = data["contacts"].get(phone_chat)
            if not name:
                name = dom_sender_name
            # 入站发送者显示名: contacts → 群成员表 → DOM 显示名 → JID 回退
            sender_name = None
            if rec and not from_me:
                sender_jid = rec.get("from")
                if sender_jid:
                    sender_name = data["contacts"].get(sender_jid)
                if not sender_name and chat and groups.get(chat):
                    sender_name = groups[chat]["members"].get(sender_jid)
            if not sender_name and not from_me:
                sender_name = dom.get("from")
            if not sender_name and rec and not from_me:
                sender_name = rec.get("from")
            kind = "group" if chat and str(chat).endswith("@g.us") else "single"
            merged.append({
                "id": dom.get("id"), "chatId": phone_chat, "fromMe": from_me,
                "from": dom.get("from") or (rec or {}).get("from"),
                "timestamp": dom.get("timestamp") or (rec or {}).get("t") or 0,
                "type": dom.get("type") or "chat", "body": dom.get("body") or "",
                "body_present": bool(dom.get("body")), "name": name,
                "sender_name": sender_name, "kind": kind,
            })
```

- [ ] **Step 2: `_upsert_one` kind 按 @g.us 计算 + Message 携带 sender_name**

`app/collector/scanner.py` 第 172-177 行改为：

```python
        now = int(time.time())
        # kind 由会话 JID 判定: @g.us → 群聊 (display_name=群名), 其余保持 single
        kind = "group" if str(chat_id).endswith("@g.us") else "single"
        self.store.upsert_chat(Chat(chat_id, self.account_id, chat_id, m.get("name"), kind, now))
        msg = Message(m["id"], self.account_id, chat_id, m.get("fromMe", False),
                      m.get("from"), m.get("timestamp", 0), m.get("type"),
                      m.get("body"), m.get("body_present", False), now,
                      m.get("sender_name"))
```

（顺带删除原来"当前采集流程仅同步单聊"的注释。）

- [ ] **Step 3: 写失败测试**

追加到 `tests/collector/test_scanner.py`：

```python
def _group_data(**overrides):
    data = {
        "chats": {}, "contacts": {},
        "groups": {"120363123456789@g.us": {"name": "海外采购群",
                                            "members": {"8615976909619@c.us": "Sonya"}}},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [{"id": "false_120363123456789@g.us_ABC123", "t": 1710000000,
                      "from": "8615976909619@c.us", "to": "120363123456789@g.us",
                      "type": "chat", "fromMe": False}],
    }
    data.update(overrides)
    return data


def test_merge_idb_dom_group_uses_group_jid_and_sender_name():
    """群聊: chatId=群 JID, kind=group, sender_name 来自群成员表, name=群名。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    merged = sc._merge_idb_dom(_group_data(), dom)
    assert merged[0]["chatId"] == "120363123456789@g.us"
    assert merged[0]["kind"] == "group"
    assert merged[0]["sender_name"] == "Sonya"
    assert merged[0]["name"] == "海外采购群"


def test_merge_group_member_missing_falls_back_to_jid():
    """群成员名缺失 (contacts/成员表/DOM 均无) → sender_name 回退为原始 JID。"""
    sc = Scanner(None, None, None)
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hello", "body_present": True}]
    data = _group_data(groups={"120363123456789@g.us": {"name": None, "members": {}}},
                       contacts={})
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["sender_name"] == "8615976909619@c.us"


def test_upsert_group_writes_kind_group_and_sender_name(tmp_data):
    """群聊消息入库: chats.kind=group + display_name=群名, messages.sender_name 随行。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    sc = Scanner(FakeCDP([{}]), store, FakeVector())
    sc._upsert_one({"id": "m1", "chatId": "120363123456789@g.us", "fromMe": False,
                    "from": "8615976909619@c.us", "timestamp": 1, "type": "chat",
                    "name": "海外采购群", "sender_name": "Sonya"})
    row = store.conn.execute("SELECT * FROM chats WHERE id='120363123456789@g.us'").fetchone()
    assert row["kind"] == "group"
    assert row["display_name"] == "海外采购群"
    msg = store.list_messages("120363123456789@g.us")[0]
    assert msg.sender_name == "Sonya"
```

- [ ] **Step 4: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_scanner.py -k "group" -v`
Expected: 3 个新测试 FAIL；其余 scanner 测试（单聊 `kind="single"` 等）PASS。

- [ ] **Step 5: 运行并确认通过**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_scanner.py`
Expected: 全部 PASS（含既有单聊行为测试 `test_upsert_writes_chat_record` 仍断言 `kind == "single"`）。

- [ ] **Step 6: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_scanner.py
git commit -m "feat: 采集器识别群聊 (@g.us → kind=group) 并解析发送者显示名"
```

---

### Task 4: 复合行净化 —— 引用排除 / 媒体行 / fromMe IDB 权威

**Files:**
- Modify: `app/config.py:30-32`（媒体行 testid 白名单，可配置）
- Modify: `app/collector/dom_snapshot.py`（模块常量 + `parse_dom_snapshot` 行识别 + `_parse_row` 引用跳过/媒体类型）
- Test: `tests/collector/test_dom_snapshot.py`、`tests/collector/test_scanner.py`

**Interfaces:**
- Consumes: `settings.dom_media_row_prefixes`（本任务新增）；`parse_dom_snapshot` 现有行结构。
- Produces: `parse_dom_snapshot(snapshot, active_chat_id)` 输出：引用容器文本不入 `body`；媒体行 `type` = 白名单前缀（去尾 `-`，如 `image-album`/`image`/`video`/`ptt`），无正文时 `body` = 媒体标记（`[相册]`/`[图片]` 等）。`_merge_idb_dom` 的 fromMe IDB 权威逻辑**已存在**（`scanner.py:109`），本任务仅补测试锁定。

- [ ] **Step 1: settings 新增媒体行白名单**

`app/config.py` 第 30-32 行 DOM 选择器配置区追加：

```python
    # 媒体消息行 testid 前缀白名单 (可配置; 未知 testid 保持忽略)
    dom_media_row_prefixes: list[str] = ["image-album-", "image-", "video-", "ptt-",
                                         "document-", "audio-", "location-"]
```

- [ ] **Step 2: dom_snapshot 模块常量 + 行识别 + 净化**

`app/collector/dom_snapshot.py`：

1. 模块顶部（`import` 之后）新增常量与 settings 引用：

```python
from app.config import settings

# 媒体行 testid 前缀 → 无正文时的占位标记
MEDIA_MARKERS = {
    "image-album-": "[相册]", "image-": "[图片]", "video-": "[视频]",
    "ptt-": "[语音]", "document-": "[文档]", "audio-": "[音频]", "location-": "[位置]",
}
```

2. `parse_dom_snapshot` 第 55 行行识别条件改为（conv-msg 或媒体白名单前缀，且带 data-id）：

```python
        media_prefixes = tuple(settings.dom_media_row_prefixes)
        ...
        if (tid.startswith("conv-msg-") or tid.startswith(media_prefixes)) and ad.get("data-id"):
            row_idx.append((i, ad))
```

3. `_parse_row` 内：遍历子树时跳过引用容器；行尾部按媒体前缀判定 type/marker。整体替换第 69-99 行为：

```python
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
```

- [ ] **Step 3: 写失败测试（引用排除 + 媒体行）**

追加到 `tests/collector/test_dom_snapshot.py`：

```python
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
```

- [ ] **Step 4: 写失败测试（fromMe IDB 权威）**

追加到 `tests/collector/test_scanner.py`：

```python
def test_merge_idb_from_me_is_authoritative_over_dom_tail():
    """fromMe 冲突时 IDB 发送者==自身账号为权威 (覆盖 DOM tail-in 信号)。"""
    sc = Scanner(None, None, None)
    data = {
        "chats": {}, "contacts": {}, "groups": {},
        "lid_to_phone": {}, "phone_by_lid": {},
        "messages": [
            # 首条入站消息确立 our_jid (=to)
            {"id": "false_8615976909619@c.us_ABC000", "t": 1700000000,
             "from": "8615976909619@c.us", "to": "8618963126542@c.us",
             "type": "chat", "fromMe": False},
            # 出站消息: from == our_jid
            {"id": "false_8615976909619@c.us_ABC123", "t": 1710000000,
             "from": "8618963126542@c.us", "to": "8615976909619@c.us",
             "type": "chat", "fromMe": True},
        ],
    }
    dom = [{"id": "ABC123", "fromMe": False, "from": None, "timestamp": 0,
            "body": "hi", "body_present": True}]  # DOM tail-in 说 fromMe=False
    merged = sc._merge_idb_dom(data, dom)
    assert merged[0]["fromMe"] is True  # IDB 权威覆盖
```

- [ ] **Step 5: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_dom_snapshot.py tests/collector/test_scanner.py -k "quote or media or from_me_is" -v`
Expected: 新测试 FAIL（引用文本仍在 body / 媒体行未识别 / fromMe 未覆盖）。

- [ ] **Step 6: 运行并确认通过（含既有回归）**

Run: `.venv/Scripts/python.exe -m pytest -q tests/collector/test_dom_snapshot.py tests/collector/test_scanner.py tests/integration/test_readonly_constraint.py`
Expected: 全部 PASS（既有 `test_parse_ignores_non_conv_msg_rows` 中 testid 为 `album-x` 不匹配白名单 → 仍被忽略；只读约束扫描仍通过）。

- [ ] **Step 7: 提交**

```bash
git add app/config.py app/collector/dom_snapshot.py tests/collector/test_dom_snapshot.py tests/collector/test_scanner.py
git commit -m "feat: DOM 解析净化引用块/识别媒体行, fromMe 冲突时 IDB 权威"
```

---

### Task 5: 画像摘要发送者标注

**Files:**
- Modify: `app/profile/service.py:17-25`（`build_chat_summary` 按 `chats.kind` 分支 + `_chat_kind` 辅助）
- Test: `tests/profile/test_service.py`

**Interfaces:**
- Consumes: `chats.kind`（Task 3 写入）、`Message.sender_name`（Task 1）。
- Produces: `build_chat_summary(store, chat_id, limit=None) -> str`：group 时入站为 `{sender_name}: {body}`、我方仍 `我:`；single 时保持 `我/客户`。下游 `refresh_customer_profile`/`analyze_customer_full` 无需改动。

- [ ] **Step 1: 实现 kind 分支**

`app/profile/service.py` 第 17-25 行替换为：

```python
def _chat_kind(store: StructuredStore, chat_id: str) -> str | None:
    try:
        row = store.conn.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
        return row["kind"] if row else None
    except Exception:
        return None

def build_chat_summary(store: StructuredStore, chat_id: str, limit: int | None = None) -> str:
    """把某会话近期消息格式化为对话摘要 (时间正序)。
    群聊按发送者标注 (成员名: 正文, 我方仍 '我:'), 单聊保持 '我/客户'。"""
    limit = limit or settings.profile_summary_messages
    kind = _chat_kind(store, chat_id)
    lines = []
    for m in reversed(store.list_messages(chat_id, limit=limit)):
        body = (m.body or "").strip()
        if body:
            if kind == "group":
                who = "我" if m.from_me else (m.sender_name or "未知")
            else:
                who = "我" if m.from_me else "客户"
            lines.append(f"{who}: {body}")
    return "\n".join(lines)
```

- [ ] **Step 2: 写失败测试**

追加到 `tests/profile/test_service.py`：

```python
def test_build_chat_summary_group_annotates_sender_name(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("g1","a1","g1","海外采购群","group",0))
    store.conn.commit()
    store.upsert_message(Message("m1", "a1", "g1", False, "8615976909619@c.us", 100,
                                 "chat", "Hi", True, int(time.time()), "Sonya"))
    store.upsert_message(Message("m2", "a1", "g1", True, "a1@c.us", 101,
                                 "chat", "Ok", True, int(time.time())))
    s = build_chat_summary(store, "g1")
    assert "Sonya: Hi" in s
    assert "我: Ok" in s


def test_build_chat_summary_single_format_unchanged(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("c1","a1","c1","Alice","single",0))
    store.conn.commit()
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 100,
                                 "chat", "Hi", True, int(time.time())))
    store.upsert_message(Message("m2", "a1", "c1", True, "a1@w", 101,
                                 "chat", "Hello", True, int(time.time())))
    s = build_chat_summary(store, "c1")
    assert "客户: Hi" in s
    assert "我: Hello" in s
```

- [ ] **Step 3: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/profile/test_service.py -k "group or single_format" -v`
Expected: 两个新测试 FAIL（群聊未按发送者标注 / 断言不符）。

- [ ] **Step 4: 运行并确认通过（含既有格式不回归）**

Run: `.venv/Scripts/python.exe -m pytest -q tests/profile/test_service.py`
Expected: 全部 PASS（既有 `test_build_chat_summary_chronological` 等未关联 chats 行 → `kind=None` → 仍走 `我/客户`）。

- [ ] **Step 5: 提交**

```bash
git add app/profile/service.py tests/profile/test_service.py
git commit -m "feat: 画像摘要群聊按发送者标注 (sender_name: body), 单聊格式不变"
```

---

### Task 6: Web 聊天页发送者展示

**Files:**
- Modify: `app/web/routes.py:112-127`（`customer_chat_messages` 查询 `chats.kind` 传入模板）
- Modify: `app/web/templates/chat_messages.html:18`（meta 区按 kind 分支展示发送者）
- Test: `tests/web/test_routes.py`

**Interfaces:**
- Consumes: `chats.kind`（Task 3）、`Message.sender_name`（Task 1）。
- Produces: `customer_chat_messages` 的 TemplateResponse 上下文新增 `kind: str|None`；模板对 `kind == "group"` 渲染 `成员名 · 时间`（我方 `我`），否则保持 `我/客户 · 时间`。

- [ ] **Step 1: route 传入 kind**

`app/web/routes.py` 第 118-126 行，在取 `msgs` 之后、`return` 之前插入 kind 查询，并把 `kind` 加入上下文：

```python
    store = _store()
    msgs = store.list_messages(chat_id, limit=50, before_ts=before)
    kind = None
    try:
        row = store.conn.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
        if row:
            kind = row["kind"]
    except Exception:
        kind = None
    # 时间正序展示
    msgs = sorted(msgs, key=lambda m: m.ts)
    older_ts = msgs[0].ts if msgs else None
    partial = request.query_params.get("partial") == "1"
    return request.app.state.templates.TemplateResponse(
        request, "chat_messages.html",
        {"customer_id": customer_id, "chat_id": chat_id, "messages": msgs,
         "older_ts": older_ts, "partial": partial, "kind": kind},
    )
```

- [ ] **Step 2: 模板按 kind 展示发送者**

`app/web/templates/chat_messages.html` 第 18 行替换为：

```html
      <div class="chat-meta">{% if kind == 'group' %}{{ '我' if m.from_me else (m.sender_name or '未知') }} · {{ m.ts }}{% else %}{{ '我' if m.from_me else '客户' }} · {{ m.ts }}{% endif %}</div>
```

- [ ] **Step 3: 写失败测试**

追加到 `tests/web/test_routes.py`：

```python
def test_chat_page_group_renders_sender_name(tmp_data):
    """群聊聊天页在 meta 区渲染发送者名。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)", ("cust1","Alice","10086",None,None,0,None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)", ("a1","g1","cust1",0.9,0,0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("g1","a1","g1","海外采购群","group",0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "g1", False, "8615976909619@c.us", 1,
                                 "chat", "hello", True, 0, "Sonya"))
    client = TestClient(create_app())
    html = client.get("/customers/cust1/chat/g1").text
    assert "Sonya ·" in html


def test_chat_page_single_keeps_customer_label(tmp_data):
    """单聊聊天页保持 '客户' 标签。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)", ("cust1","Alice","10086",None,None,0,None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)", ("a1","c1","cust1",0.9,0,0))
    store.conn.execute("INSERT INTO chats VALUES(?,?,?,?,?,?)", ("c1","a1","c1","Alice","single",0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 1,
                                 "chat", "hello", True, 0))
    client = TestClient(create_app())
    html = client.get("/customers/cust1/chat/c1").text
    assert "客户 ·" in html
```

- [ ] **Step 4: 运行并确认失败**

Run: `.venv/Scripts/python.exe -m pytest -q tests/web/test_routes.py -k "group_renders or single_keeps" -v`
Expected: 两个新测试 FAIL（模板未渲染发送者名 / 上下文无 kind）。

- [ ] **Step 5: 运行并确认通过**

Run: `.venv/Scripts/python.exe -m pytest -q tests/web/test_routes.py`
Expected: 全部 PASS（含既有 `test_chat_messages_pagination` —— 未建 chats 行 → kind=None → 单聊样式）。

- [ ] **Step 6: 提交**

```bash
git add app/web/routes.py app/web/templates/chat_messages.html tests/web/test_routes.py
git commit -m "feat: Web 聊天页群聊显示发送者名, 单聊保持 我/客户"
```

---

### Task 7: 收尾验证（全量回归）

**Files:**
- Verify: 全部改动文件；`openspec/changes/collector-message-integrity/tasks.md` 勾选。

**Interfaces:**
- 无新接口。验证前序所有产出的端到端一致性。

- [ ] **Step 1: 全量测试**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 全部 PASS。测试数从 102 增至约 **117**（新增 15：存储 2 + idb_walk 2 + scanner 4 + dom_snapshot 3 + profile 2 + web 2）。若出现失败，定位到具体任务回修（不得用 `-k` 跳过）。

- [ ] **Step 2: 构建检查**

Run: `.venv/Scripts/python.exe -m compileall -q app tests`
Expected: 无输出、退出码 0（`-q` 静默）。

- [ ] **Step 3: 只读约束回归（采集器硬约束）**

Run: `.venv/Scripts/python.exe -m pytest -q tests/integration/test_readonly_constraint.py tests/collector/test_readonly_cdp.py`
Expected: 全部 PASS（确认新增 JS/代码无发送类操作、全部经 ReadOnlyCDP）。

- [ ] **Step 4: 勾选 tasks.md**

将 `openspec/changes/collector-message-integrity/tasks.md` 中 1.1-7.3 共 17 个复选框全部勾为 `[x]`。

- [ ] **Step 5: 提交**

```bash
git add openspec/changes/collector-message-integrity/tasks.md
git commit -m "chore: collector-message-integrity 收尾回归验证 (pytest 117/117 + compileall)"
```

---

## 自检记录

- **Spec 覆盖**：设计 §3.1↔Task2；§3.2↔Task1+3；§3.3↔Task5；§3.4↔Task4(引用)；§3.5↔Task4(媒体)；§3.6↔Task3/4(合并层 fromMe IDB 权威，既有代码+测试锁定)；§3.7↔Task6；§4 测试策略逐项落到各任务；§5 风险缓解（防御式读取、静默降级、白名单可配置、单聊不变、幂等迁移）均内嵌。
- **无占位符**：所有步骤含完整代码与精确命令。
- **类型一致**：`sender_name`（Message 第 11 字段）与 `chats.kind`（"group"/"single"）在各任务拼写统一；`_merge_idb_dom` 输出键名 `sender_name`/`kind` 与 `_upsert_one` 读取一致；media type 由 `media_prefix.rstrip("-")` 派生（`image-album`/`image`/`video`/`ptt` 等）。
- **已知连带修改**：schema 加列后 `tests/web/test_routes.py` 两处 10 列 `INSERT INTO messages` 需同步为 11 列（Task 1 Step 4）。
