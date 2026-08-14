---
change: multilingual-reply-generation
design-doc: docs/superpowers/specs/2026-08-14-multilingual-reply-generation-design.md
base-ref: 188d87838a2d739fca62881a844e05f165e311dc
archived-with: 2026-08-14-multilingual-reply-generation
---

# 场景化多语种话术生成 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回复生成支持中/英/俄三语 + 6 类业务场景（自动识别/手动指定）+ 正式/口语语气，参数沿现有异步任务链路透传，前端可选维度。

**Architecture:** 扩展 `app/reply/generator.py` 的 `generate_reply` 签名（`language`/`scenario`/`formality` 可选，缺省与现状等价），提示词以维度指令拼装（`LANGUAGES`/`SCENARIOS`/`FORMALITY`/`TERMS` 常量）。`reply_tasks` 表加 3 个可空列，`create_reply_task`/worker/`_reply_params`/路由逐层透传。`chat_messages.html` 加三个原生 select 经 `hx-vals` 提交，`reply_result.html` 展示维度并透传 regenerate。

**Tech Stack:** Python 3 / FastAPI / SQLite / Jinja2 / HTMX

## Global Constraints

- 新参数 `language`/`scenario`/`formality` 全部可选，默认 `zh`/`auto`/`casual`，与现状行为等价，**向后兼容**（现有测试不得改坏）。
- `regenerate_reply` 与「重新生成」按钮必须透传三参数，仅切换 `NEXT_STYLE` 风格。
- `reply_tasks` 迁移幂等：`ALTER TABLE ADD COLUMN` + try/except `OperationalError`（对齐现有 `avatar_path`/`sender_name` 模式）。
- `scenario="auto"` 时提示词包含场景识别指令（6 类 + 通用兜底）。
- 语种仅 `zh`/`en`/`ru`；场景仅 `auto`/`inquiry`/`bargain`/`inspection`/`logistics`/`payment`/`after_sale`；语气仅 `casual`/`formal`。未知值回退默认。

---

### Task 1: 生成器扩展（generator.py）

**Files:**
- Modify: `app/reply/generator.py`
- Test: `tests/reply/test_generator.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/reply/test_generator.py`：

```python
def _capture(store, language="zh", scenario="auto", formality="casual", style="default"):
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    generate_reply(pipe, "cust1", "c1", "hi", style=style,
                   language=language, scenario=scenario, formality=formality)
    return seen["system"]


def test_language_instruction_in_system(tmp_data):
    """multilingual-copy: language 指令进入 system 提示词。"""
    store = SqliteStore()
    sys_ru = _capture(store, language="ru")
    assert "俄语" in sys_ru
    sys_en = _capture(store, language="en")
    assert "英语" in sys_en


def test_scenario_instruction_in_system(tmp_data):
    """multilingual-copy: 手动指定场景时按场景指令生成。"""
    store = SqliteStore()
    sys_bargain = _capture(store, scenario="bargain")
    assert "砍价" in sys_bargain


def test_auto_scenario_detection_instruction(tmp_data):
    """multilingual-copy: scenario=auto 时提示词含场景识别指令。"""
    store = SqliteStore()
    sys_auto = _capture(store, scenario="auto")
    assert "所属业务场景" in sys_auto


def test_formality_instruction_in_system(tmp_data):
    """multilingual-copy: formal 语气指令进入提示词。"""
    store = SqliteStore()
    sys_formal = _capture(store, formality="formal")
    assert "正式" in sys_formal


def test_terms_in_system(tmp_data):
    """multilingual-copy: 汽车外贸术语进入提示词。"""
    store = SqliteStore()
    assert "VIN" in _capture(store)


def test_default_params_backward_compatible(tmp_data):
    """D1: 缺省参数 (zh/auto/casual) 与现状等价, 不含多余维度指令。"""
    store = SqliteStore()
    sys_default = _capture(store)
    assert "俄语" not in sys_default
    assert "英语" not in sys_default
    assert "正式" not in sys_default


def test_regenerate_preserves_dimensions(tmp_data):
    """D3: regenerate 保留语种/场景/语气, 仅切换风格。"""
    seen = []

    class TrackingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen.append(s)
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), TrackingLLM())
    r1 = generate_reply(pipe, "cust1", "c1", "hi", style="default",
                        language="ru", scenario="payment", formality="formal")
    r2 = regenerate_reply(pipe, "cust1", "c1", "hi", previous_style=r1["style"],
                          language="ru", scenario="payment", formality="formal")
    assert r2["style"] != r1["style"]
    assert r2["language"] == "ru" and r2["scenario"] == "payment" and r2["formality"] == "formal"
    assert "俄语" in seen[1] and "付款" in seen[1] and "正式" in seen[1]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/reply/test_generator.py -v`
Expected: FAIL（`generate_reply` 不接受 `language` 等关键字参数 → TypeError，或断言不满足）

- [ ] **Step 3: 实现生成器扩展**

重写 `app/reply/generator.py` 为：

```python
# app/reply/generator.py
"""辅助回复: RAG 召回画像+历史+产品知识 + 当前消息 → 建议回复。
仅生成不自动发送。支持多候选: 通过 style 提示词让 LLM 产出不同表达。
多轮会话: history 为最近 N 轮 [{"role","content"}] 列表, 作为额外 system 上下文 (D4)。
多语种话术 (multilingual-reply-generation): language/scenario/formality 三维度, 缺省与旧版等价。"""
from app.rag.pipeline import RagPipeline

REPLY_SYSTEM = """你是外贸业务员的回复助手。基于客户画像、历史聊天、产品知识, 针对客户最新消息生成建议回复。
要求: 专业、得体、可直接复制发送。给出一条主回复。{style}{language}{scenario}{formality}{terms}"""

REPLY_STYLE_VARIANTS = {
    "default": "",
    "concise": "语气简洁、直接，控制在三句话以内。",
    "warm": "语气热情友好，主动表达对客户需求的重视。",
    "formal": "语气正式严谨，突出专业与条理。",
}

LANGUAGES = {
    "zh": "用简体中文回复。",
    "en": "用英语回复。",
    "ru": "用俄语回复。",
}

SCENARIOS = {
    "auto": "先判断本条消息所属业务场景（询价/砍价/看车/物流/付款/售后），按该场景生成；无法判断时按通用场景处理。",
    "inquiry": "本条消息属于询价场景，突出车型信息与价格。",
    "bargain": "本条消息属于砍价场景，强调产品价值与让步空间。",
    "inspection": "本条消息属于看车场景，突出车况与看车安排。",
    "logistics": "本条消息属于物流场景，说明运输方式与交期。",
    "payment": "本条消息属于付款场景，说明付款方式与交易安全。",
    "after_sale": "本条消息属于售后场景，安抚客户并说明质保与处理流程。",
}

FORMALITY = {
    "casual": "",
    "formal": "使用正式书面语气，措辞严谨。",
}

TERMS = "汽车外贸术语: 车架号VIN, 排量, 手续齐全, 报关单, 关税, 运输时间, 付款方式(定金/尾款), 质保, 出港, 到港。话术中使用标准贸易术语, 术语表达要准确。"

NEXT_STYLE = {"default": "concise", "concise": "warm", "warm": "formal", "formal": "default"}


def _build_system(style_instruction: str, history: list[dict] | None,
                  language: str = "zh", scenario: str = "auto", formality: str = "casual") -> str:
    base = REPLY_SYSTEM.format(
        style=style_instruction,
        language=LANGUAGES.get(language, ""),
        scenario=SCENARIOS.get(scenario, SCENARIOS["auto"]),
        formality=FORMALITY.get(formality, ""),
        terms=TERMS,
    )
    if history:
        lines = "\n".join(f"{h['role']}: {h['content']}" for h in history)
        base = f"{base}\n\n本次会话最近对话历史:\n{lines}"
    return base


def generate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                   incoming_message: str, style: str = "default",
                   language: str = "zh", scenario: str = "auto", formality: str = "casual",
                   history: list[dict] | None = None) -> dict:
    """返回 {reply, sources, style, language, scenario, formality}。不发送。
    language: zh|en|ru; scenario: auto|inquiry|bargain|inspection|logistics|payment|after_sale;
    formality: casual|formal。缺省与旧版行为一致。"""
    style_instruction = REPLY_STYLE_VARIANTS.get(style, "")
    system = _build_system(style_instruction, history, language, scenario, formality)
    result = pipeline.run(incoming_message, customer_id=customer_id, chat_id=chat_id,
                          system=system)
    return {"reply": result["answer"], "sources": result["sources"], "style": style,
            "language": language, "scenario": scenario, "formality": formality}


def regenerate_reply(pipeline: RagPipeline, customer_id: str, chat_id: str,
                     incoming_message: str, previous_style: str = "default",
                     language: str = "zh", scenario: str = "auto", formality: str = "casual",
                     history: list[dict] | None = None) -> dict:
    """重新生成获得不同候选 (切换表达风格 + LLM 温度自然差异); 保留语种/场景/语气 (D3)。"""
    next_style = NEXT_STYLE.get(previous_style, "default")
    return generate_reply(pipeline, customer_id, chat_id, incoming_message,
                          style=next_style, language=language, scenario=scenario,
                          formality=formality, history=history)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/reply/test_generator.py -v`
Expected: PASS（含原有 4 个用例 + 新增 7 个用例全绿）

- [ ] **Step 5: 提交**

```bash
git add app/reply/generator.py tests/reply/test_generator.py
git commit -m "feat: 回复生成器支持语种/场景/语气维度 (multilingual-reply-generation)"
```

---

### Task 2: 存储层迁移 + create_reply_task 透传

**Files:**
- Modify: `app/storage/schema.sql`
- Modify: `app/storage/sqlite_store.py`
- Test: `tests/storage/test_reply_store.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/storage/test_reply_store.py`：

```python
def test_create_reply_task_persists_generation_params(tmp_data):
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate",
                                  language="ru", scenario="payment", formality="formal")
    t = store.get_reply_task(tid)
    assert t["language"] == "ru"
    assert t["scenario"] == "payment"
    assert t["formality"] == "formal"


def test_create_reply_task_defaults_generation_params(tmp_data):
    """缺省语言/场景/语气为 NULL, 生成器侧回退默认 (zh/auto/casual)。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate")
    t = store.get_reply_task(tid)
    assert t["language"] is None
    assert t["scenario"] is None
    assert t["formality"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_reply_store.py -v`
Expected: FAIL（`OperationalError: table reply_tasks has no column named language` 或 `TypeError: create_reply_task() got an unexpected keyword argument`）

- [ ] **Step 3: 实现 schema 迁移**

在 `app/storage/schema.sql` 的 `reply_tasks` 定义后追加 3 列（追加在 `updated_at INTEGER` 之后、右括号之前）：

```sql
CREATE TABLE IF NOT EXISTS reply_tasks(
  id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, message TEXT, style TEXT,
  session_id TEXT, mode TEXT, status TEXT, result TEXT, error TEXT, created_at INTEGER, updated_at INTEGER,
  language TEXT, scenario TEXT, formality TEXT);
```

在 `app/storage/sqlite_store.py` 的 `_init_schema` 末尾（`backfill_requests` try 之后）追加：

```python
        try:
            self.conn.execute("ALTER TABLE reply_tasks ADD COLUMN language TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # 列已存在 (新库 schema.sql 已含) — 幂等
        try:
            self.conn.execute("ALTER TABLE reply_tasks ADD COLUMN scenario TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            self.conn.execute("ALTER TABLE reply_tasks ADD COLUMN formality TEXT")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass
```

- [ ] **Step 4: 实现 create_reply_task 透传**

修改 `app/storage/sqlite_store.py` 的 `create_reply_task`（约 210 行）为：

```python
    def create_reply_task(self, customer_id, chat_id, message, style, session_id, mode,
                          language=None, scenario=None, formality=None):
        task_id = uuid.uuid4().hex
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO reply_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, customer_id, chat_id, message, style, session_id, mode,
             "pending", None, None, now, now, language, scenario, formality))
        self.conn.commit()
        return task_id
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/storage/test_reply_store.py -v`
Expected: PASS（含原有 4 个用例 + 新增 2 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/storage/schema.sql app/storage/sqlite_store.py tests/storage/test_reply_store.py
git commit -m "feat: reply_tasks 增加 language/scenario/formality 列并透传 (multilingual-reply-generation)"
```

---

### Task 3: worker + routes 链路透传

**Files:**
- Modify: `app/web/worker.py`
- Modify: `app/web/routes.py`
- Test: `tests/reply/test_worker.py`（追加）、`tests/web/test_reply_async.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/reply/test_worker.py`：

```python
def test_execute_reply_passes_generation_params(tmp_data):
    """multilingual-copy: worker 将 language/scenario/formality 传给 generate_reply。"""
    store = SqliteStore()
    sid = store.find_or_create_reply_session("cust1", "c1")
    llm = FakeLLM()
    app = _make_app(store, llm)
    tid = store.create_reply_task("cust1", "c1", "hi", "default", sid, "generate",
                                  language="ru", scenario="payment", formality="formal")
    worker._execute_reply_task(app, store, store.get_reply_task(tid))
    assert "俄语" in llm.prompts[0]
    assert "付款" in llm.prompts[0]
    assert "正式" in llm.prompts[0]
    done = store.get_reply_task(tid)
    assert done["status"] == "done"
    result = json.loads(done["result"])
    assert result["language"] == "ru" and result["scenario"] == "payment" and result["formality"] == "formal"
```

追加到 `tests/web/test_reply_async.py`：

```python
def test_reply_post_persists_generation_params(tmp_data):
    """multilingual-copy: POST /api/reply 解析 language/scenario/formality 并持久化。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    client = TestClient(create_app())
    r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi",
                                        "language": "en", "scenario": "inquiry", "formality": "formal"})
    assert r.status_code == 200
    tid = reply_task_id(r.text)
    row = SqliteStore().conn.execute("SELECT * FROM reply_tasks WHERE id=?", (tid,)).fetchone()
    assert row["language"] == "en"
    assert row["scenario"] == "inquiry"
    assert row["formality"] == "formal"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/reply/test_worker.py tests/web/test_reply_async.py -v`
Expected: FAIL（worker/routes 未透传参数）

- [ ] **Step 3: 实现 worker 透传**

修改 `app/web/worker.py` 的 `_execute_reply_task`（约 38 行），将 `generate_reply` 调用改为：

```python
        result = generate_reply(pipe, task["customer_id"], task["chat_id"], task["message"],
                                style=task["style"],
                                language=task.get("language") or "zh",
                                scenario=task.get("scenario") or "auto",
                                formality=task.get("formality") or "casual",
                                history=history)
```

- [ ] **Step 4: 实现 routes 解析**

修改 `app/web/routes.py` 的 `_reply_params`（约 507 行）为：

```python
async def _reply_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {customer_id, chat_id, message, style, session_id,
    language, scenario, formality}。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    else:
        body = await request.form()
    keys = ("customer_id", "chat_id", "message", "style", "session_id",
            "language", "scenario", "formality")
    return {k: (body.get(k) or "") for k in keys}
```

修改 `POST /api/reply`（约 540 行）为：

```python
@router.post("/api/reply")
async def reply(request: Request):
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    task_id = store.create_reply_task(
        p["customer_id"], p["chat_id"], p["message"],
        p.get("style") or "default", session_id, mode="generate",
        language=p.get("language") or None, scenario=p.get("scenario") or None,
        formality=p.get("formality") or None)
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})
```

修改 `POST /api/reply/regenerate`（约 551 行）为：

```python
@router.post("/api/reply/regenerate")
async def reply_regenerate(request: Request):
    """reply-assist: 重生成任务 (mode=regenerate, worker 不追加会话历史);
    保留语种/场景/语气 (D3)。"""
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    next_style = NEXT_STYLE.get(p.get("style") or "default", "default")
    task_id = store.create_reply_task(
        p["customer_id"], p["chat_id"], p["message"], next_style, session_id, mode="regenerate",
        language=p.get("language") or None, scenario=p.get("scenario") or None,
        formality=p.get("formality") or None)
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/reply/test_worker.py tests/web/test_reply_async.py -v`
Expected: PASS（含原有用例 + 新增 2 个用例全绿）

- [ ] **Step 6: 提交**

```bash
git add app/web/worker.py app/web/routes.py tests/reply/test_worker.py tests/web/test_reply_async.py
git commit -m "feat: worker/routes 透传语种/场景/语气参数 (multilingual-reply-generation)"
```

---

### Task 4: 前端展示

**Files:**
- Modify: `app/web/templates/chat_messages.html`
- Modify: `app/web/templates/reply_result.html`
- Modify: `app/web/static/css/app.css`
- Test: `tests/web/test_reply_async.py`（追加）

- [ ] **Step 1: 写失败测试**

追加到 `tests/web/test_reply_async.py`：

```python
def test_chat_page_has_generation_dimension_selects(tmp_data):
    """multilingual-copy: 聊天页含语种/场景/语气选择器。"""
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.execute("INSERT INTO customer_chat_map VALUES(?,?,?,?,?,?)",
                       ("a1", "c1", "cust1", 0.9, 0, 0))
    store.conn.commit()
    from app.storage.interfaces import Message
    store.upsert_message(Message("m1", "a1", "c1", False, "x@w", 1, "chat", "hello", True, 0))
    client = TestClient(create_app())
    html = client.get("/customers/cust1/chat/c1").text
    assert 'name="language"' in html
    assert 'name="scenario"' in html
    assert 'name="formality"' in html
    assert "Русский" in html


def test_reply_result_shows_generation_dimensions(tmp_data, monkeypatch):
    """multilingual-copy: 结果卡片展示语种/场景标签。"""
    from app.web import routes
    from app.web.app import create_app

    class FakeLLM:
        def generate(self, s, u, max_tokens=1024):
            return "Официальный ответ"  # 俄语正式回复

    class FakeRerank:
        def rerank(self, q, c, top_k=8):
            return c[:top_k]

    class FakeEmbed:
        def embed(self, text):
            return [1.0] * 8

    monkeypatch.setattr(routes, "CloudLLM", FakeLLM)
    monkeypatch.setattr(routes, "get_reranker", lambda: FakeRerank())
    monkeypatch.setattr(routes, "get_embedding", lambda: FakeEmbed())
    from app.storage.sqlite_store import SqliteStore
    store = SqliteStore()
    store.conn.execute("INSERT INTO customers VALUES(?,?,?,?,?,?,?)",
                       ("cust1", "Alice", "10086", None, None, 0, None))
    store.conn.commit()
    with TestClient(create_app()) as client:
        r = client.post("/api/reply", data={"customer_id": "cust1", "chat_id": "c1", "message": "hi",
                                            "language": "ru", "scenario": "payment", "formality": "formal"})
        done = wait_reply_done(client, reply_task_id(r.text))
        assert "Официальный ответ" in done.text
        assert "俄语" in done.text
        assert "付款" in done.text
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_reply_async.py -v`
Expected: FAIL（模板无选择器 / 结果无维度标签）

- [ ] **Step 3: 修改 chat_messages.html**

在 `app/web/templates/chat_messages.html` 的 `reply-{{ m.id }}` div 内（`生成回复`按钮前）追加三个选择器与按钮：

```html
      <div id="reply-{{ m.id }}">
        <div class="reply-controls">
          <select name="language" class="input">
            <option value="zh">中文</option>
            <option value="en">English</option>
            <option value="ru">Русский</option>
          </select>
          <select name="scenario" class="input">
            <option value="auto">自动场景</option>
            <option value="inquiry">询价</option>
            <option value="bargain">砍价</option>
            <option value="inspection">看车</option>
            <option value="logistics">物流</option>
            <option value="payment">付款</option>
            <option value="after_sale">售后</option>
          </select>
          <select name="formality" class="input">
            <option value="casual">口语</option>
            <option value="formal">正式</option>
          </select>
        </div>
        <button class="btn btn-sm" hx-post="/api/reply"
                hx-include="closest div"
                hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ (m.body or '')|replace('"', '&quot;') }}", "session_id": "{{ session_id }}" }'
                hx-target="#reply-{{ m.id }}" hx-swap="innerHTML">生成回复</button>
      </div>
```

> 说明：`hx-include="closest div"` 将同级三个 select 的 name/value 一并提交（`_reply_params` 会忽略多余字段）。选择器无 `id` 前缀，页面每条消息各一份。

- [ ] **Step 4: 修改 reply_result.html**

重写 `app/web/templates/reply_result.html` 为：

```html
<div class="result-card">
  {% if error %}
  <p><strong>回复生成失败</strong></p>
  <p class="muted">{{ error }}</p>
  {% else %}
  <p><strong>建议回复</strong>
    <span class="tag">风格: {{ style }}</span>
    {% if language %}<span class="tag">语种: {{ language }}</span>{% endif %}
    {% if scenario and scenario != 'auto' %}<span class="tag">场景: {{ scenario }}</span>{% endif %}
    {% if formality == 'formal' %}<span class="tag">正式语气</span>{% endif %}
  </p>
  <textarea class="input" id="reply-text" rows="4" style="width:100%">{{ reply }}</textarea>
  <div class="btn-row">
    <button class="btn" type="button" data-copy="reply-text">复制</button>
    <button class="btn" hx-post="/api/reply/regenerate"
            hx-vals='{"customer_id": "{{ customer_id }}", "chat_id": "{{ chat_id }}", "message": "{{ message|default('', true) }}", "style": "{{ style }}", "language": "{{ language|default('', true) }}", "scenario": "{{ scenario|default('', true) }}", "formality": "{{ formality|default('', true) }}", "session_id": "{{ session_id|default('', true) }}" }'
            hx-target="closest div" hx-swap="innerHTML">重新生成</button>
  </div>
  <details class="sources">
    <summary>检索来源 ({{ sources|length }})</summary>
    <ul>
      {% for s in sources %}
      <li><small>{{ s.get('text', '')[:120] }}</small></li>
      {% else %}
      <li>无来源</li>
      {% endfor %}
    </ul>
  </details>
  {% endif %}
</div>
```

同时修改 `app/web/routes.py` 的 `_render_reply_result`（约 516 行），将 language/scenario/formality 传入模板：

```python
def _render_reply_result(request: Request, customer_id: str, chat_id: str,
                         message: str, result: dict, session_id: str | None = None):
    return request.app.state.templates.TemplateResponse(
        request, "reply_result.html",
        {"customer_id": customer_id, "chat_id": chat_id, "message": message,
         "reply": result.get("reply", ""),
         "sources": result.get("sources", []), "style": result.get("style", "default"),
         "language": result.get("language", ""),
         "scenario": result.get("scenario", ""),
         "formality": result.get("formality", ""),
         "session_id": session_id, "error": result.get("error")},
    )
```

- [ ] **Step 5: 修改 app.css**

在 `app/web/static/css/app.css` 末尾追加：

```css
/* ---- multilingual-reply-generation: 回复维度选择器 ---- */
.reply-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
  margin-bottom: 6px;
}
.reply-controls .input { width: auto; padding: 3px 8px; font-size: 12px; }
```

- [ ] **Step 6: 运行测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/web/test_reply_async.py -v`
Expected: PASS（含原有用例 + 新增 2 个用例全绿）

- [ ] **Step 7: 提交**

```bash
git add app/web/templates/chat_messages.html app/web/templates/reply_result.html app/web/static/css/app.css app/web/routes.py tests/web/test_reply_async.py
git commit -m "feat: 前端语种/场景/语气选择 + 结果维度展示 (multilingual-reply-generation)"
```

---

### Task 5: 全量回归验证

**Files:**
- 无新增（仅验证）。

- [ ] **Step 1: 编译检查**

Run: `.venv\Scripts\python.exe -m compileall app tests`
Expected: 无语法错误输出

- [ ] **Step 2: 全量测试**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: 全部通过（含既有用例，无回归）

- [ ] **Step 3: 手动验证清单**

本地运行 `.venv\Scripts\python.exe -m app.web.app`（或项目启动命令）验证：

1. 打开 `/customers/{id}/chat/{chat_id}`，确认消息区有语种/场景/语气三个下拉。
2. 选「Русский + 砍价 + 正式」生成回复 → 输出俄语砍价正式话术，结果卡片显示语种/场景标签。
3. 点「重新生成」→ 仍为俄语/砍价/正式，仅风格切换。
4. 不动选择器直接生成（缺省）→ 中文通用话术，与改造前一致。
5. 选「自动场景」发送询价类消息 → 按询价场景生成。

- [ ] **Step 4: 提交（如有修复）**

```bash
git add -A
git commit -m "fix: 多语种话术回归修复 (multilingual-reply-generation)"
```

---

## Self-Review

**Spec 覆盖核对：**
- 多语种话术生成（中/英/俄）→ Task 1 `LANGUAGES` + `generate_reply` language 参数。
- 业务场景识别与指定（6 类 + 自动 + 通用兜底）→ Task 1 `SCENARIOS`（auto 含识别指令）+ 手动指定映射。
- 语气风格（口语/正式）→ Task 1 `FORMALITY`。
- 汽车外贸术语约束 → Task 1 `TERMS` 内嵌提示词。
- 回复生成异步任务支持生成参数 → Task 2 `reply_tasks` 3 列 + Task 3 路由/worker 透传 + `_render_reply_result`。
- 前端选择器 → Task 4 `chat_messages.html`；结果维度展示 → Task 4 `reply_result.html`。

**占位符扫描：** 无 TBD/TODO；所有代码步骤含完整实现。

**类型一致性：** `generate_reply`/`regenerate_reply` 三参数签名在 Task 1 定义、Task 3 worker 调用一致；`create_reply_task(..., language=None, scenario=None, formality=None)` 在 Task 2 定义、Task 3 路由调用一致；`_reply_params` 返回键在 Task 3 定义、路由消费一致；`_render_reply_result` 传入模板的键（language/scenario/formality）在 Task 3 定义、Task 4 模板消费一致。缺省 NULL → worker `or "zh"/"auto"/"casual"` 回退，与旧任务兼容。
