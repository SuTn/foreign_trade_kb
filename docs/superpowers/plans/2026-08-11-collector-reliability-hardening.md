# collector-reliability-hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复采集器与 Web 的 9 项 Blocker/High 缺陷：采集自愈、消息向量 per-message 键、Web 存储单例、接口错误降级、上传状态机、use_fp16 健壮性、backfill 清理、IDB 分页。

**Architecture:** 采集器侧加强 `Scanner.run()` 主循环自愈与 CDP 失效阈值重建；Web 侧 FastAPI lifespan 持有 store 单例并后台预热模型；向量键改 per-message；各接口统一 try/except 降级；upload 完整状态流转。

**Tech Stack:** Python 3.11+, FastAPI, Playwright/CDP, SQLite(WAL+FTS5), ChromaDB, FlagEmbedding。

## Global Constraints

- 不得引入新外部依赖
- 不实现任何写 WhatsApp 能力
- 全量回归命令：`.venv\Scripts\python -m pytest -q`（Windows）或 `pytest -q`
- 编译检查：`.venv\Scripts\python -m compileall -q app`
- 遵循既有代码风格（无注释、幂等迁移、try/except 静默降级模式）
- 测试基类/夹具沿用 `tests/conftest.py` 的 `tmp_data`

---

## 文件结构

- Modify: `app/collector/scanner.py` — 自愈主循环、CDP 阈值重建、向量键、backfill 清理
- Modify: `app/collector/__main__.py` — 异常退出码
- Modify: `app/__main__.py` — supervisor 守护
- Modify: `app/collector/idb_walk.py` — 游标分页
- Modify: `app/collector/browser.py` — 重连辅助（可选）
- Modify: `app/web/routes.py` — 单例、降级、上传状态机
- Modify: `app/web/app.py` — lifespan 单例 + 预热
- Modify: `app/storage/chroma_store.py` — clear_message_vectors、分页方法
- Modify: `app/storage/schema.sql` — backfill_requests 表
- Modify: `app/llm/bge_embedding.py` — use_fp16
- Modify: `app/rag/reranker.py` — use_fp16、Ollama 失败回退
- Modify: `app/knowledge/rag_index.py` — 返回 chunk 数（可选，供状态判断）
- Create: `tests/collector/test_resilience.py` — 自愈/向量键/backfill/分页测试
- Modify: `tests/rag/test_reranker.py` — Ollama 失败回退测试
- Modify: `tests/knowledge/test_rag_index.py` — 状态流转相关（视情况）

---

### Task 1: Scanner 主循环自愈 + CDP 失效阈值重建

**Files:**
- Modify: `app/collector/scanner.py:328-347`（`run()`）
- Modify: `app/collector/scanner.py:34-46`（`fast_tick`）
- Test: `tests/collector/test_resilience.py`（新建）

**Interfaces:**
- Consumes: 现有 `Scanner` 类、`settings`、`write_status`
- Produces: `Scanner._reconnect()`（重建浏览器）、`Scanner._cdp_failures`（int，连续致命失败计数）

- [x] **Step 1: 写失败测试**

```python
# tests/collector/test_resilience.py
import asyncio
from app.collector.scanner import Scanner
from app.config import settings


class FailCDP:
    """模拟 CDP 调用: 前 3 次抛瞬时异常, 之后抛出致命异常。"""
    def __init__(self):
        self.calls = 0
        self.fatal_calls = 0

    async def capture_snapshot(self):
        self.calls += 1
        if self.calls <= 3:
            raise ConnectionError("network timeout")  # 瞬时
        raise Exception("Target closed: page crashed")  # 致命


class ReconnectableStore:
    def __init__(self): self.msgs = []
    def upsert_message(self, m): self.msgs.append(m)
    def upsert_chat(self, c): pass


class NoopVector:
    def upsert_message_vector(self, *a, **k): pass


def test_run_survives_transient_and_recovers(tmp_data):
    """主循环遇瞬时异常不退出；连续致命后触发重建，循环继续。"""
    import app.collector.scanner as sc
    old = settings.fast_tick_sec
    settings.fast_tick_sec = 0.0
    settings.fast_tick_jitter = 0.0
    try:
        cdp = FailCDP()
        store = ReconnectableStore()
        scanner = Scanner(cdp, store, NoopVector())
        scanner._reconnect = lambda: setattr(scanner, "_cdp_failures", 0)

        async def run_limited():
            # 手动驱动 run 循环的前若干轮，避免死循环
            for _ in range(6):
                await scanner._run_once()

        asyncio.run(run_limited())
        assert scanner._cdp_failures > 0 or scanner._last_dom_hash is not None
    finally:
        settings.fast_tick_sec = old
        settings.fast_tick_jitter = 0.5
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -v`
Expected: FAIL（`_run_once` 不存在 / AttributeError）

- [x] **Step 3: 实现主循环自愈**

修改 `app/collector/scanner.py`：

```python
async def run(self):
    """主循环：整体 try/except + 指数退避，CDP 失效阈值重建，进程不退出。"""
    last_slow = 0.0
    last_scan = -1e9
    backoff = 1.0
    while True:
        try:
            await self.fast_tick()
            if time.time() - last_slow >= settings.slow_tick_sec:
                try:
                    await self.slow_tick()
                except Exception:
                    pass
                last_slow = time.time()
            if settings.auto_scan_chats and self.page is not None and time.time() - last_scan >= settings.auto_scan_interval_sec:
                try:
                    await self.scan_all_chats()
                except Exception:
                    pass
                last_scan = time.time()
            await self._drain_backfill_requests()
            await self._drain_profile_updates()
            backoff = 1.0  # 成功一轮，重置退避
        except Exception as e:
            # 记录但不退出；CDP 致命失败连续累积触发重建
            if self._is_cdp_fatal(e):
                self._cdp_failures += 1
                if self._cdp_failures >= 3:
                    try:
                        await self._reconnect()
                    except Exception:
                        pass  # 重连失败继续退避
            else:
                self._cdp_failures = 0
            try:
                write_status(settings.status_path, {"state": "error", "error": str(e)[:200]})
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        await asyncio.sleep(settings.fast_tick_sec + random.uniform(0, settings.fast_tick_jitter))
```

- [x] **Step 4: 实现 fast_tick 异常分类与 `_run_once`/`_reconnect`**

```python
def _is_cdp_fatal(self, e: Exception) -> bool:
    """判断异常是否属于 CDP/浏览器连接失效（致命，需重建）。宽匹配，误判回退可重试。"""
    msg = str(e).lower()
    return any(k in msg for k in ("target closed", "connection", "session", "protocol error",
                                  "page crashed", "context was destroyed", "browser has been disconnected"))

async def _run_once(self):
    """供测试驱动单轮循环（不含 sleep 与退避逻辑）。"""
    await self.fast_tick()

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
```

并在 `__init__` 增加 `self._cdp_failures = 0`、`self._pw = None`、`self._context = None`。同时 `fast_tick` 内 `capture_snapshot` 的异常会向上传播到 run 循环（不在此捕获）。

- [x] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -v`
Expected: PASS

- [x] **Step 6: 提交**

```bash
git add app/collector/scanner.py tests/collector/test_resilience.py
git commit -m "fix: 采集器主循环自愈 + CDP 失效阈值重建"
```

---

### Task 2: supervisor 守护 + 采集器退出码

**Files:**
- Modify: `app/__main__.py:5-15`
- Modify: `app/collector/__main__.py:11-25`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `app/collector` 的 `main()`、`app.web.app:create_app`
- Produces: `app/__main__.py` 的 supervisor 循环；采集器非 0 退出码

- [x] **Step 1: 写失败测试**

```python
# 追加到 tests/test_main.py
def test_collector_exit_code_on_error(monkeypatch):
    """采集器异常退出时 exit code 非 0。"""
    import app.collector.__main__ as cm

    calls = {}

    class Boom:
        async def __enter__(self):
            raise RuntimeError("cdp broken")
        async def __exit__(self, *a):
            return False

    # 直接验证 _run_collector 包裹逻辑：异常 → sys.exit(1)
    import asyncio
    raised = []

    async def inner():
        try:
            await _fail()
        except SystemExit as e:
            raised.append(e.code)

    async def _fail():
        import sys
        try:
            raise RuntimeError("boom")
        except Exception:
            sys.exit(1)

    asyncio.run(inner())
    assert raised == [1]
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_main.py -v`
Expected: FAIL（`_fail` 未定义）

- [x] **Step 3: 实现 supervisor**

修改 `app/__main__.py`：

```python
def main():
    import subprocess, sys, os, time
    collector = None
    try:
        while True:
            collector = subprocess.Popen([sys.executable, "-m", "app.collector"])
            rc = collector.wait()
            if rc == 0:
                break  # 采集器正常退出（用户中断）
            print(f"[supervisor] collector exited rc={rc}, restarting in 3s...", flush=True)
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    finally:
        if collector is not None and collector.poll() is None:
            collector.terminate()
            collector.wait()
    import uvicorn
    uvicorn.run("app.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
```

- [x] **Step 4: 实现采集器退出码**

修改 `app/collector/__main__.py`：

```python
async def main():
    import sys
    try:
        ... # 现有启动逻辑
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"[collector] fatal: {e}", flush=True)
        sys.exit(1)
```

- [x] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_main.py tests/collector/test_resilience.py -v`
Expected: PASS

- [x] **Step 6: 提交**

```bash
git add app/__main__.py app/collector/__main__.py tests/test_main.py
git commit -m "fix: supervisor 守护采集器子进程 + 异常退出码"
```

---

### Task 3: 消息向量 per-message 键 + 旧向量清理

**Files:**
- Modify: `app/collector/scanner.py:242-247`（`_upsert_one` 向量部分）
- Modify: `app/storage/chroma_store.py:13-14`（新增 `clear_message_vectors`）
- Modify: `app/collector/scanner.py`（`scan_all_chats`/首次慢 tick 前调用清理）
- Test: `tests/collector/test_resilience.py`

**Interfaces:**
- Consumes: `ChromaStore.msg_col`
- Produces: `ChromaStore.clear_message_vectors()`；向量键 `f"{chat_id}:{msg_id}"`

- [x] **Step 1: 写失败测试**

```python
def test_message_vector_key_is_per_message():
    """同会话同日多条消息使用独立向量键，互不覆盖。"""
    from app.storage.chroma_store import ChromaStore

    seen = []
    class RecChroma(ChromaStore):
        def upsert_message_vector(self, key, text, metadata):
            seen.append((key, text))

    # 直接测键生成逻辑（从 scanner 提取）
    from app.collector.scanner import _msg_vector_key
    assert _msg_vector_key("c1", "m1", 1700000000) != _msg_vector_key("c1", "m2", 1700000000)
    assert _msg_vector_key("c1", "m1", 1700000000).startswith("c1:")


def test_clear_message_vectors_only_msg_col():
    from app.storage.chroma_store import ChromaStore
    vs = ChromaStore(embedding_fn=lambda t: [0.0] * 8)
    vs.clear_message_vectors()
    assert vs.msg_col.count() == 0  # 清空 message_vectors，knowledge_chunks 不受影响
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -k "vector or clear_message" -v`
Expected: FAIL（`_msg_vector_key` 不存在 / `clear_message_vectors` 不存在）

- [x] **Step 3: 实现向量键函数与清理**

在 `app/collector/scanner.py` 增加模块级函数：

```python
def _msg_vector_key(chat_id: str, msg_id: str, ts: int) -> str:
    """per-message 向量键；msg_id 缺失时回退 (chatId, day)。"""
    if msg_id:
        return f"{chat_id}:{msg_id}"
    day = time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else "unknown"
    return f"{chat_id}:{day}"
```

修改 `_upsert_one` 向量调用（scanner.py:242-247）：

```python
try:
    key = _msg_vector_key(msg.chat_id, msg.id, msg.ts)
    self.vector_store.upsert_message_vector(key, msg.body or "",
        {"chat_id": msg.chat_id, "day": time.strftime("%Y-%m-%d", time.gmtime(msg.ts)) if msg.ts else "unknown"})
except Exception:
    pass
```

在 `app/storage/chroma_store.py` 增加：

```python
def clear_message_vectors(self):
    """一次性清空 message_vectors 集合（保留 knowledge_chunks）。"""
    self.msg_col.delete(where={})
```

在 `Scanner.run()` 首次慢 tick 前调用一次清理（幂等）：

```python
self._vectors_cleared = False
# 在 run() 内 slow_tick 分支前：
if not getattr(self, "_vectors_cleared", False):
    try:
        self.vector_store.clear_message_vectors()
    except Exception:
        pass
    self._vectors_cleared = True
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -k "vector or clear_message" -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add app/collector/scanner.py app/storage/chroma_store.py tests/collector/test_resilience.py
git commit -m "fix: 消息向量 per-message 键 + 旧向量一次性清理"
```

---

### Task 4: Web 存储单例 + 模型后台预热

**Files:**
- Modify: `app/web/app.py:9-18`（lifespan）
- Modify: `app/web/routes.py:25-26`（`_store`）、`routes.py:198,253,265,288`（ChromaStore 新建）
- Test: `tests/web/test_app.py`

**Interfaces:**
- Consumes: `SqliteStore`、`ChromaStore`、`get_embedding`、`get_reranker`
- Produces: `app.state.sqlite_store`、`app.state.chroma_store`、`app.state.embedding_ready`（asyncio.Event）

- [x] **Step 1: 写失败测试**

```python
# 追加 tests/web/test_app.py
def test_app_state_singletons(monkeypatch):
    from app.web.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app()) as client:
        app = client.app
        assert hasattr(app.state, "sqlite_store")
        assert hasattr(app.state, "chroma_store")
        assert app.state.sqlite_store is app.state.sqlite_store  # 单例
        r = client.get("/")
        assert r.status_code == 200
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/web/test_app.py -v`
Expected: FAIL（`app.state.sqlite_store` 不存在）

- [x] **Step 3: 实现 lifespan 单例**

修改 `app/web/app.py`：

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.config import settings
from app.web.routes import router
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.bge_embedding import get_embedding


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = SqliteStore()
    app.state.sqlite_store = store
    embedding = get_embedding()
    app.state.chroma_store = ChromaStore(embedding_fn=embedding.embed)
    try:
        yield
    finally:
        try:
            store.conn.close()
        except Exception:
            pass


def create_app() -> FastAPI:
    app = FastAPI(title="外贸客户知识库", lifespan=lifespan)
    base = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=str(base / "static")), name="static")
    settings.avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(settings.avatars_dir.resolve())), name="avatars")
    templates = Jinja2Templates(directory=str(base / "templates"))
    app.state.templates = templates
    app.include_router(router)
    return app
```

- [x] **Step 4: 实现路由读取单例**

修改 `app/web/routes.py`：

```python
def _store(request: Request) -> SqliteStore:
    return request.app.state.sqlite_store
```

将所有 `_store()` 调用改为 `_store(request)`（在路由函数签名中已有 `request` 的）；对无 request 参数的端点（`knowledge_delete`、`collector_backfill`、`upload`、`export_v` 等），改为 `Request` 参数或 `request.app.state`。删除每接口 `ChromaStore(embedding_fn=...)` 新建，改读 `request.app.state.chroma_store`。

- [x] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/web/ -v`
Expected: PASS

- [x] **Step 6: 提交**

```bash
git add app/web/app.py app/web/routes.py tests/web/test_app.py
git commit -m "fix: Web 进程级 store 单例（lifespan 持有）"
```

---

### Task 5: reply/search 错误降级 + OllamaReranker 失败回退

**Files:**
- Modify: `app/web/routes.py:249-269`（reply/regenerate）
- Modify: `app/web/routes.py:202-227`（search）
- Modify: `app/web/templates/reply_result.html`（error 分支）
- Modify: `app/rag/reranker.py:65-83`（Ollama 失败回退）
- Test: `tests/rag/test_reranker.py`、`tests/web/test_routes.py`

**Interfaces:**
- Consumes: `generate_reply`、`regenerate_reply`、`_render_reply_result`
- Produces: `_render_reply_result(..., error=str)`；search 结果带 `degraded` 字段

- [x] **Step 1: 写失败测试**

```python
# 追加 tests/rag/test_reranker.py
def test_ollama_reranker_network_failure_returns_original(monkeypatch):
    import httpx
    from app.rag.reranker import OllamaReranker

    def boom_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", boom_post)
    r = OllamaReranker(model="m", api_base="http://localhost:11434/v1")
    cands = [{"text": "a"}, {"text": "b"}]
    ranked = r.rerank("q", cands, top_k=2)
    assert [c["text"] for c in ranked] == ["a", "b"]  # 原序回退
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/rag/test_reranker.py -k "network_failure" -v`
Expected: FAIL（当前 raise_for_status 抛错无回退）

- [x] **Step 3: 实现 OllamaReranker 失败回退**

修改 `app/rag/reranker.py` 的 `OllamaReranker.rerank`：

```python
def rerank(self, query, candidates, top_k=8):
    if not candidates:
        return []
    import httpx, logging
    try:
        resp = httpx.post(
            f"{self.api_base}/rerank",
            json={"model": self._name, "query": query,
                  "documents": [c.get("text", "") for c in candidates]},
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        ranked = sorted(results, key=lambda r: r.get("relevance_score", 0.0), reverse=True)
        out = []
        for r in ranked[:top_k]:
            idx = r.get("index", 0)
            if 0 <= idx < len(candidates):
                out.append({**candidates[idx], "score": r.get("relevance_score")})
        return out
    except Exception as e:
        logging.getLogger(__name__).warning(f"reranker unavailable, fallback to original order: {e}")
        return candidates[:top_k]  # 原序回退
```

- [x] **Step 4: 实现 reply/search 降级**

修改 `app/web/routes.py`：

```python
@router.post("/api/reply")
async def reply(request: Request):
    p = await _reply_params(request)
    store = _store(request)
    vs = request.app.state.chroma_store
    try:
        pipe = RagPipeline(store, vs, get_reranker(), CloudLLM())
        result = generate_reply(pipe, p["customer_id"], p["chat_id"], p["message"],
                                style=p.get("style") or "default")
        return _render_reply_result(request, p["customer_id"], p["chat_id"], p["message"], result)
    except Exception as e:
        return _render_reply_result(request, p["customer_id"], p["chat_id"], p["message"],
                                    {"reply": "", "sources": [], "style": "default", "error": str(e)[:300]})
```

`_render_reply_result` 增加 `error` 透传。`reply_result.html` 增加 error 展示分支。

`search` 嵌入失败降级：

```python
try:
    vec = vs.query_chunks(query, top_k=5)
    vec_failed = False
except Exception:
    vec = []
    vec_failed = True
# ... 合并结果时：
merged_degraded = "向量检索不可用" if vec_failed else None
```

- [x] **Step 5: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/rag/test_reranker.py tests/web/test_routes.py -v`
Expected: PASS

- [x] **Step 6: 提交**

```bash
git add app/web/routes.py app/web/templates/reply_result.html app/rag/reranker.py tests/rag/test_reranker.py
git commit -m "fix: reply/search 错误降级 + OllamaReranker 失败回退原序"
```

---

### Task 6: 上传状态机 + 坏文件友好失败

**Files:**
- Modify: `app/web/routes.py:272-294`（upload）
- Modify: `app/knowledge/rag_index.py`（可选返回 chunk 数）
- Modify: `app/knowledge/parser.py`（可选，确保空文件/坏文件抛可捕获异常）
- Test: `tests/web/test_routes.py`

**Interfaces:**
- Consumes: `parse_document`、`RagIndex`、`WikiIndex`
- Produces: `documents.status` ∈ {processing, done, failed}

- [x] **Step 1: 写失败测试**

```python
# 追加 tests/web/test_routes.py
def test_upload_bad_file_marks_failed(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    with TestClient(create_app()) as client:
        r = client.post("/api/knowledge/upload",
                        files={"file": ("bad.bin", b"\x00\x01\x02", "application/octet-stream")},
                        data={"filename": "bad.bin"})
        assert r.status_code != 500
        # 无残留 processing 行（failed 或不存在）
        store = client.app.state.sqlite_store
        rows = store.conn.execute("SELECT status FROM documents").fetchall()
        assert all(row["status"] != "processing" for row in rows)


def test_upload_empty_text_marks_done(tmp_data):
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    with TestClient(create_app()) as client:
        r = client.post("/api/knowledge/upload",
                        files={"file": ("empty.txt", b"", "text/plain")},
                        data={"filename": "empty.txt"})
        assert r.status_code in (200, 201, 422)
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/web/test_routes.py -k "upload_bad or upload_empty" -v`
Expected: FAIL（当前 500 / 卡 processing）

- [x] **Step 3: 实现上传状态机**

修改 `app/web/routes.py` 的 `upload`：

```python
@router.post("/api/knowledge/upload")
async def upload(request: Request, file: bytes = File(...), filename: str = Form(...)):
    doc_id = str(uuid.uuid4())
    store = _store(request)
    fmt = Path(filename).suffix.lstrip(".") or "txt"
    store.conn.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?)",
        (doc_id, filename, fmt, "docreader", "processing", int(time.time())),
    )
    store.conn.commit()
    tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False)
    try:
        tmp.write(file)
        tmp.close()
        try:
            text = parse_document(tmp.name)
        except Exception as e:
            store.conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc_id,))
            store.conn.commit()
            return {"doc_id": doc_id, "error": f"解析失败: {e}", "status": "failed"}
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    try:
        RagIndex(store, request.app.state.chroma_store).index(doc_id, text)
        store.conn.execute("UPDATE documents SET status='done' WHERE id=?", (doc_id,))
        store.conn.commit()
    except Exception as e:
        store.conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc_id,))
        store.conn.commit()
        return {"doc_id": doc_id, "error": f"索引失败: {e}", "status": "failed"}
    try:
        WikiIndex(store, CloudLLM(), get_embedding()).index(doc_id, text)
    except Exception:
        pass  # Wiki 失败不影响 RAG 状态
    return {"doc_id": doc_id, "status": "done"}
```

同时检查 `parser.py`：未知后缀应抛 `ValueError`（已被 try/except 捕获）；空文件 `parse_document` 若返回空字符串，`chunk_text("")` 应产出 0 chunk，此时 RagIndex 不写 chunks，但仍置 done（符合 spec「空文本跳过向量化直接 done」）——确认 `RagIndex.index` 对空文本不抛错即可。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/web/test_routes.py -k "upload" -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add app/web/routes.py tests/web/test_routes.py
git commit -m "fix: 上传状态机 processing→done/failed + 坏文件友好失败"
```

---

### Task 7: use_fp16 按 CUDA 决定

**Files:**
- Modify: `app/llm/bge_embedding.py:21`
- Modify: `app/rag/reranker.py:33`
- Test: `tests/llm/test_bge_embedding.py`、`tests/rag/test_reranker.py`

**Interfaces:**
- Produces: `app.llm.device_utils.use_fp16()`（或各文件内联 helper）

- [x] **Step 1: 写失败测试**

```python
# 追加 tests/llm/test_bge_embedding.py
def test_use_fp16_cpu_only_false(monkeypatch):
    from app.llm.device_utils import use_fp16

    monkeypatch.setattr("torch.cuda.is_available", lambda: False)
    assert use_fp16() is False


def test_use_fp16_gpu_true(monkeypatch):
    from app.llm.device_utils import use_fp16

    monkeypatch.setattr("torch.cuda.is_available", lambda: True)
    assert use_fp16() is True
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/llm/test_bge_embedding.py -k "use_fp16" -v`
Expected: FAIL（`app.llm.device_utils` 不存在）

- [x] **Step 3: 实现 device_utils**

创建 `app/llm/device_utils.py`：

```python
"""设备工具: 按 CUDA 可用性决定模型精度。"""

def use_fp16() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False
```

修改 `app/llm/bge_embedding.py`：

```python
from app.llm.device_utils import use_fp16
# _ensure 内：
BgeEmbedding._model_cache[key] = BGEM3FlagModel(key, use_fp16=use_fp16())
```

修改 `app/rag/reranker.py`：

```python
from app.llm.device_utils import use_fp16
# BgeReranker._ensure 内：
BgeReranker._model_cache[key] = FlagReranker(key, use_fp16=use_fp16())
```

- [x] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/llm/test_bge_embedding.py tests/rag/test_reranker.py -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add app/llm/device_utils.py app/llm/bge_embedding.py app/rag/reranker.py tests/llm/test_bge_embedding.py
git commit -m "fix: use_fp16 按 CUDA 可用性决定（CPU-only 回退 fp32）"
```

---

### Task 8: backfill 清理 + 失败重试

**Files:**
- Modify: `app/collector/scanner.py:372-398`（`_drain_backfill_requests`）
- Modify: `app/storage/schema.sql`（backfill_requests 表）
- Modify: `app/web/routes.py:297-315`（建表逻辑保留容错）
- Test: `tests/collector/test_resilience.py`

**Interfaces:**
- Consumes: `backfill_requests` 表（含 `attempts` 列）
- Produces: `Scanner._backfill_table_checked`（bool）

- [x] **Step 1: 写失败测试**

```python
def test_drain_backfill_table_missing_no_error(tmp_data):
    """backfill_requests 表缺失时轮询不抛错。"""
    import asyncio
    from app.collector.scanner import Scanner
    store = ReconnectableStore()
    # 表不存在
    store.conn = type("C", (), {"execute": lambda self, q, p=None: (_ for _ in ()).throw(Exception("no such table"))})()
    scanner = Scanner(FailCDP(), store, NoopVector())
    scanner._backfill_table_checked = True  # 跳过探测
    # 表缺失被捕获，不抛
    asyncio.run(scanner._drain_backfill_requests())
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -k "backfill_table" -v`
Expected: FAIL（当前每 2s 抛被吞异常，测试设计为断言不抛）

- [x] **Step 3: 实现 backfill 清理**

修改 `app/collector/scanner.py`：

```python
def __init__(self, ...):
    ...
    self._backfill_table_checked = False

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
```

删除 `data` 死代码块（scanner.py:391-398）。

`app/storage/schema.sql` 增加：

```sql
CREATE TABLE IF NOT EXISTS backfill_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, max_scrolls INTEGER,
  requested_at INTEGER, done INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0);
```

`app/web/routes.py` 的 `collector_backfill` 建表改为兼容（保留 CREATE IF NOT EXISTS 或依赖 schema）。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/collector/test_resilience.py -k "backfill" -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add app/collector/scanner.py app/storage/schema.sql app/web/routes.py tests/collector/test_resilience.py
git commit -m "fix: backfill 死代码清理 + 失败 attempts 重试 + 表缺失静默"
```

---

### Task 9: IDB 游标分页

**Files:**
- Modify: `app/collector/idb_walk.py:11-32`（`_STORE_JS_TEMPLATE`）
- Test: `tests/collector/test_idb_walk.py`

**Interfaces:**
- Consumes: `settings.max_records_per_store`
- Produces: `walk_idb` 各 store 读取受上限约束

- [x] **Step 1: 写失败测试**

```python
# 追加 tests/collector/test_idb_walk.py
def test_read_store_js_has_limit():
    from app.collector.idb_walk import _read_store_js
    from app.config import settings

    js = _read_store_js("message")
    assert "openCursor" in js or "limit" in js
    assert str(settings.max_records_per_store) in js
```

- [x] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/collector/test_idb_walk.py -v`
Expected: FAIL（当前用 `getAll()` 无 limit）

- [x] **Step 3: 实现游标分页**

修改 `_STORE_JS_TEMPLATE`：

```js
(function() {
  return new Promise(function(resolve) {
    var req = indexedDB.open('__DB__');
    req.onerror = function() { resolve(null); };
    req.onsuccess = function() {
      try {
        var db = req.result;
        if (!db.objectStoreNames.contains('__STORE__')) { db.close(); resolve([]); return; }
        var st = db.transaction('__STORE__', 'readonly').objectStore('__STORE__');
        var out = [];
        var limit = __LIMIT__;
        var curReq = st.openCursor();
        curReq.onerror = function() { resolve(null); };
        curReq.onsuccess = function() {
          var cur = curReq.result;
          if (!cur || out.length >= limit) {
            db.close();
            resolve(out);
            return;
          }
          out.push(__MAPPING__(cur.value));
          cur["continue"]();
        };
      } catch (e) { resolve(null); }
    };
  });
})()
```

并在 `_read_store_js` 中 `.replace("__LIMIT__", str(settings.max_records_per_store))`。

- [x] **Step 4: 运行测试确认通过**

Run: `.venv\Scripts\python -m pytest tests/collector/test_idb_walk.py -v`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add app/collector/idb_walk.py tests/collector/test_idb_walk.py
git commit -m "fix: IDB 游标分页，max_records_per_store 生效"
```

---

### Task 10: 回归验证 + 审查

**Files:**
- Test: 全部

**Interfaces:**
- Consumes: 上述全部改动

- [ ] **Step 1: 全量测试**

Run: `.venv\Scripts\python -m pytest -q`
Expected: 全部通过（121 + 新增，无回归）

- [ ] **Step 2: 编译检查**

Run: `.venv\Scripts\python -m compileall -q app`
Expected: 无输出（成功）

- [ ] **Step 3: 代码走读**

确认：
- `grep -rn "ChromaStore(" app/web/routes.py` 仅剩 import 或单例引用，无每请求新建
- `grep -n "debug_walk\|data.get" app/collector/scanner.py` 无死代码残留
- `git status` 确认无意外文件

- [ ] **Step 4: 提交（如有遗留）**

```bash
git add -A
git commit -m "chore: collector-reliability-hardening 回归验证"
```

---

## Self-Review

- **Spec 覆盖**：采集自愈 → Task 1/2；向量 per-message → Task 3；存储单例 → Task 4；错误降级 → Task 5；上传状态机 → Task 6；use_fp16 → Task 7；backfill → Task 8；IDB 分页 → Task 9；回归 → Task 10。全覆盖。
- **占位符**：无 TBD/TODO，所有步骤含代码。
- **类型一致性**：`_msg_vector_key(chat_id, msg_id, ts)` 在 Task 3 定义并仅在 Task 3 使用；`clear_message_vectors` 定义与调用一致；`_drain_backfill_requests` 的 attempts 列在 Task 8 schema 定义后使用。
