# app/web/routes.py
import time, uuid, tempfile
import sqlite3
import json
from pathlib import Path

from fastapi import APIRouter, Request, File, Form
from fastapi.responses import HTMLResponse

from app.config import settings
from app.collector.scanner import read_status, is_alive
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.rag.pipeline import RagPipeline
from app.rag.reranker import get_reranker
from app.llm.cloud_llm import CloudLLM
from app.llm.bge_embedding import get_embedding
from app.knowledge.parser import parse_document
from app.knowledge.rag_index import RagIndex
from app.knowledge.wiki_index import WikiIndex
from app.knowledge.wiki_export import export_vault
from app.reply.generator import generate_reply, regenerate_reply, NEXT_STYLE

router = APIRouter()

WARMUP_TIMEOUT_SEC = 30.0  # 首次请求等待模型预热就绪的超时 (3.3)


def _embedding(request: Request):
    """返回进程级 embedding 实例 (lifespan 预热共享; 无 lifespan 时惰性创建)。"""
    emb = getattr(request.app.state, "embedding", None)
    if emb is None:
        emb = get_embedding()
        request.app.state.embedding = emb
    return emb


def _embedding_ready(app) -> bool:
    """等待模型预热完成 (有超时)。无 lifespan/无预热机制视为已就绪。"""
    ready = getattr(app.state, "embedding_ready", None)
    if ready is None:
        return True
    return ready.wait(WARMUP_TIMEOUT_SEC)


def _build_store() -> SqliteStore:
    """进程级 sqlite 单例: 连接需跨线程共享 (TestClient 各请求可能在不同线程),
    故以 check_same_thread=False + WAL 重建连接 (顺序访问下安全)。"""
    store = SqliteStore()
    store.conn.close()
    conn = sqlite3.connect(str(store.path), timeout=5.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    store.conn = conn
    return store


def _store(request: Request) -> SqliteStore:
    """返回进程级 sqlite 单例 (lifespan 创建; 测试无 lifespan 时惰性创建并缓存)。"""
    if not hasattr(request.app.state, "sqlite_store"):
        request.app.state.sqlite_store = _build_store()
    return request.app.state.sqlite_store


def _get_chroma_store(app) -> ChromaStore:
    """返回进程级 chroma 单例 (首次访问惰性创建, 复用 embedding_fn 便于测试替换)。

    模型预热未就绪时按 WARMUP_TIMEOUT_SEC 等待, 超时抛错由调用方降级。
    request 无关: worker 线程同样经此访问共享 chroma (审计 H)。
    """
    if not getattr(app.state, "chroma_store", None):
        if not _embedding_ready(app):
            raise RuntimeError("embedding 模型预热超时未就绪, 请稍后重试")
        emb = getattr(app.state, "embedding", None) or get_embedding()
        app.state.chroma_store = ChromaStore(embedding_fn=emb.embed)
    return app.state.chroma_store


def _chroma_store(request: Request) -> ChromaStore:
    return _get_chroma_store(request.app)


def _build_stats(store) -> dict:
    """首页/统计共用聚合: 返回 {customers, knowledge, recent_chats}。"""
    customers = {
        "total": store.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
        "with_profile": store.conn.execute("SELECT COUNT(DISTINCT customer_id) FROM profiles").fetchone()[0],
        "linked_chats": store.conn.execute("SELECT COUNT(*) FROM customer_chat_map").fetchone()[0],
    }
    knowledge = {
        "documents": store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "chunks": store.conn.execute("SELECT COUNT(*) FROM doc_chunks").fetchone()[0],
        "wiki_pages": store.conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0],
    }
    recent = store.conn.execute(
        "SELECT chat_id, MAX(ts) AS last_ts FROM messages GROUP BY chat_id "
        "ORDER BY last_ts DESC, chat_id LIMIT 10"
    ).fetchall()
    chat_names = {r["id"]: r["display_name"] for r in
                  store.conn.execute("SELECT id, display_name FROM chats").fetchall()}
    cust_map = {r["chat_id"]: r["customer_id"] for r in
                store.conn.execute("SELECT chat_id, customer_id FROM customer_chat_map").fetchall()}
    recent_chats = [{"chat_id": r["chat_id"], "display_name": chat_names.get(r["chat_id"]),
                     "last_ts": r["last_ts"], "customer_id": cust_map.get(r["chat_id"])} for r in recent]
    return {"customers": customers, "knowledge": knowledge, "recent_chats": recent_chats}


@router.get("/")
async def index(request: Request):
    stats = _build_stats(_store(request))
    s = read_status(settings.status_path)
    return request.app.state.templates.TemplateResponse(
        request, "home.html",
        {**stats, "status": s or {}, "alive": is_alive(settings.status_path)})


@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path)}


@router.get("/api/stats")
async def stats(request: Request):
    st = _build_stats(_store(request))
    s = read_status(settings.status_path)
    return {**st, "collector": {"alive": is_alive(settings.status_path), "status": s or {}}}


@router.get("/customers")
async def customers(request: Request):
    store = _store(request)
    rows = store.conn.execute("SELECT * FROM customers").fetchall()
    profiles_by_customer: dict[str, str] = {}
    for r in store.conn.execute("SELECT customer_id, field, value FROM profiles").fetchall():
        s = profiles_by_customer.setdefault(r["customer_id"], "")
        profiles_by_customer[r["customer_id"]] = f"{s} {r['field']}={r['value']}"
    countries = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT value FROM profiles WHERE field='country' "
        "AND value IS NOT NULL AND value != '' ORDER BY value").fetchall()]
    companies = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT value FROM profiles WHERE field='company' "
        "AND value IS NOT NULL AND value != '' ORDER BY value").fetchall()]
    return request.app.state.templates.TemplateResponse(
        request, "customers.html",
        {"customers": rows, "profiles_by_customer": profiles_by_customer,
         "countries": countries, "companies": companies})


@router.get("/customers/{customer_id}")
async def customer_detail(customer_id: str, request: Request):
    store = _store(request)
    customer = store.conn.execute(
        "SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    chats = store.conn.execute(
        "SELECT chat_id, match_confidence FROM customer_chat_map WHERE customer_id=?",
        (customer_id,)).fetchall()
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "chat.html",
        {"customer_id": customer_id, "customer": dict(customer) if customer else None,
         "chats": chats, "profile": profile},
    )


@router.get("/customers/{customer_id}/chat/{chat_id}")
async def customer_chat_messages(customer_id: str, chat_id: str, request: Request):
    """web-app: 聊天浏览页 — 分页展示该会话历史消息 (含元数据与正文), 支持触发回复。"""
    before_raw = request.query_params.get("before_ts")
    before = int(before_raw) if before_raw and before_raw.isdigit() else None
    store = _store(request)
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
         "older_ts": older_ts, "partial": partial, "kind": kind,
         "session_id": store.find_or_create_reply_session(customer_id, chat_id)},
    )


@router.post("/customers/{customer_id}/analyze")
async def customer_analyze(customer_id: str, request: Request):
    """6.4: 生成客户分析 (兴趣点/活跃度/跟进建议)。仅生成不写入画像。"""
    store = _store(request)
    from app.profile.service import analyze_customer_full
    try:
        analysis = analyze_customer_full(store, CloudLLM(), customer_id)
    except Exception as e:
        analysis = f"分析失败: {e}"
    return request.app.state.templates.TemplateResponse(
        request, "analysis.html", {"customer_id": customer_id, "analysis": analysis},
    )


@router.post("/customers/{customer_id}/refresh-profile")
async def customer_refresh_profile(customer_id: str, request: Request):
    """6.2: 手动重新抽取画像 (auto 来源, 不覆盖 manual)。"""
    store = _store(request)
    from app.profile.service import refresh_customer_profile
    try:
        refresh_customer_profile(store, CloudLLM(), customer_id)
    except Exception:
        pass  # 抽取失败展示旧画像
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "profile_list.html", {"profile": profile, "customer_id": customer_id},
    )


@router.post("/customers/{customer_id}/profile")
async def customer_profile_save(customer_id: str, request: Request):
    """web-app: 画像页编辑某字段并保存 → 持久化并标记为人工来源 (source=manual)。"""
    body = await request.form()
    field = (body.get("field") or "").strip()
    value = (body.get("value") or "").strip()
    store = _store(request)
    if field and value:
        store.upsert_profile_field(customer_id, field, value, source="manual")
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "profile_list.html", {"profile": profile, "customer_id": customer_id},
    )


@router.get("/knowledge")
async def knowledge(request: Request):
    docs = _store(request).list_documents()
    return request.app.state.templates.TemplateResponse(request, "knowledge.html", {"docs": docs})


@router.get("/api/knowledge/list")
async def knowledge_list(request: Request):
    """knowledge-base: 文档列表 (含 chunk/wiki 状态)。"""
    return {"docs": _store(request).list_documents()}


@router.delete("/api/knowledge/{doc_id}")
async def knowledge_delete(request: Request, doc_id: str):
    """knowledge-base: 删除文档 (chunks + 向量 + wiki 引用一并清理)。"""
    store = _store(request)
    deleted = store.delete_document(doc_id)
    _chroma_store(request).delete_chunks(doc_id)
    return {"deleted": deleted, "doc_id": doc_id}


@router.post("/api/knowledge/search")
async def knowledge_search(request: Request):
    """knowledge-base: 检索测试 — 返回含来源文档与片段的检索结果。"""
    p = await _reply_params(request)
    query = p.get("message") or ""
    store = _store(request)
    degraded = None
    vec = []
    # 向量召回 + BM25 关键词召回, 合并去重; 嵌入失败降级为 BM25-only
    try:
        vs = _chroma_store(request)
        vec = vs.query_chunks(query, top_k=5)
    except Exception:
        degraded = "向量检索不可用"
        vec = []
    bm25 = store.search_fts("doc_chunks", query, limit=5)
    # FTS 外部内容表不含 doc_id, 需 join 回 doc_chunks
    doc_lookup = {}
    for r in store.conn.execute("SELECT id, doc_id, text FROM doc_chunks").fetchall():
        doc_lookup[r["text"]] = r["doc_id"]
    seen = set(); merged = []
    for c in vec:
        if c["text"] in seen: continue
        seen.add(c["text"])
        merged.append({"source": "vector", "doc_id": c["metadata"].get("doc_id"),
                       "text": c["text"]})
    for r in bm25:
        if r["text"] in seen: continue
        seen.add(r["text"])
        merged.append({"source": "bm25", "doc_id": doc_lookup.get(r["text"]), "text": r["text"]})
    if degraded:
        merged.insert(0, {"source": "degraded", "doc_id": None, "text": degraded})
    return request.app.state.templates.TemplateResponse(
        request, "knowledge_search.html",
        {"query": query, "results": merged, "degraded": degraded})


async def _reply_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {customer_id, chat_id, message, style, session_id}。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    else:
        body = await request.form()
    return {k: (body.get(k) or "") for k in ("customer_id", "chat_id", "message", "style", "session_id")}


def _render_reply_result(request: Request, customer_id: str, chat_id: str,
                         message: str, result: dict, session_id: str | None = None):
    return request.app.state.templates.TemplateResponse(
        request, "reply_result.html",
        {"customer_id": customer_id, "chat_id": chat_id, "message": message,
         "reply": result.get("reply", ""),
         "sources": result.get("sources", []), "style": result.get("style", "default"),
         "session_id": session_id, "error": result.get("error")},
    )


async def _reply_session(request: Request, customer_id: str, chat_id: str,
                         session_id: str | None = None) -> str:
    """D4: 每 chat 一个会话; 显式 session_id 存在且归属该 chat 则沿用, 否则按 customer_id+chat_id find-or-create。"""
    store = _store(request)
    if session_id:
        row = store.conn.execute(
            "SELECT id FROM reply_sessions WHERE id=? AND customer_id=? AND chat_id=?",
            (session_id, customer_id, chat_id)).fetchone()
        if row:
            return session_id
    return store.find_or_create_reply_session(customer_id, chat_id)


@router.post("/api/reply")
async def reply(request: Request):
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    task_id = store.create_reply_task(p["customer_id"], p["chat_id"], p["message"],
                                      p.get("style") or "default", session_id, mode="generate")
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})


@router.post("/api/reply/regenerate")
async def reply_regenerate(request: Request):
    """reply-assist: 重生成任务 (mode=regenerate, worker 不追加会话历史)。"""
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    next_style = NEXT_STYLE.get(p.get("style") or "default", "default")
    task_id = store.create_reply_task(p["customer_id"], p["chat_id"], p["message"],
                                      next_style, session_id, mode="regenerate")
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})


@router.get("/api/reply/status/{task_id}")
async def reply_status(request: Request, task_id: str):
    """D2: 轮询端点。pending/running → 处理中片段(继续轮询);
    done → 完整结果(停止轮询); failed → 错误片段。"""
    store = _store(request)
    task = store.get_reply_task(task_id)
    if task is None:
        return HTMLResponse('<p class="muted">任务不存在或已过期</p>')
    if task["status"] in ("pending", "running"):
        return request.app.state.templates.TemplateResponse(
            request, "reply_polling.html", {"task_id": task_id})
    if task["status"] == "failed":
        return _render_reply_result(request, task["customer_id"], task["chat_id"],
                                    task["message"],
                                    {"reply": "", "sources": [], "style": task["style"],
                                     "error": task["error"]},
                                    session_id=task["session_id"])
    result = json.loads(task["result"] or "{}")
    return _render_reply_result(request, task["customer_id"], task["chat_id"],
                                task["message"], result, session_id=task["session_id"])


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
    if not text.strip():
        # 空文本跳过向量化直接 done (chromadb 空 upsert 会抛错)
        store.conn.execute("UPDATE documents SET status='done' WHERE id=?", (doc_id,))
        store.conn.commit()
        return {"doc_id": doc_id, "status": "done"}
    try:
        RagIndex(store, _chroma_store(request)).index(doc_id, text)
        store.conn.execute("UPDATE documents SET status='done' WHERE id=?", (doc_id,))
        store.conn.commit()
    except Exception as e:
        store.conn.execute("UPDATE documents SET status='failed' WHERE id=?", (doc_id,))
        store.conn.commit()
        return {"doc_id": doc_id, "error": f"索引失败: {e}", "status": "failed"}
    # Wiki 索引失败不影响 RAG 状态 (双索引互不阻塞)
    try:
        WikiIndex(store, CloudLLM(), get_embedding()).index(doc_id, text)
    except Exception:
        pass  # Wiki 失败不阻塞上传; RAG 索引已成功
    return {"doc_id": doc_id, "status": "done"}


@router.post("/api/collector/backfill")
async def collector_backfill(request: Request, body: dict):
    """3.7: 按需历史回溯 — 触发采集器滚动当前会话加载更早消息。
    body: {chat_id?: str, max_scrolls?: int}。
    采集器为独立进程, 实际滚动由采集器读取 status 触发或 CLI 执行;
    此端点记录请求意图供采集器轮询, 返回 accepted。"""
    chat_id = body.get("chat_id")
    max_scrolls = int(body.get("max_scrolls", 10))
    store = _store(request)
    store.conn.execute(
        "CREATE TABLE IF NOT EXISTS backfill_requests "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, max_scrolls INTEGER, requested_at INTEGER, done INTEGER DEFAULT 0)"
    )
    store.conn.execute(
        "INSERT INTO backfill_requests (chat_id, max_scrolls, requested_at) VALUES (?,?,?)",
        (chat_id, max_scrolls, int(time.time())),
    )
    store.conn.commit()
    return {"accepted": True, "chat_id": chat_id, "max_scrolls": max_scrolls}


@router.post("/api/knowledge/export-vault")
async def export_v(request: Request):
    return {"exported": export_vault(_store(request), settings.vault_export_dir)}
