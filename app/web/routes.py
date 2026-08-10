# app/web/routes.py
import time, uuid, tempfile
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
from app.reply.generator import generate_reply, regenerate_reply

router = APIRouter()


def _store() -> SqliteStore:
    return SqliteStore()


@router.get("/")
async def index(request: Request):
    store = _store()
    customers = {"total": store.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
                 "with_profile": store.conn.execute("SELECT COUNT(DISTINCT customer_id) FROM profiles").fetchone()[0]}
    knowledge = {"documents": store.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
                 "wiki_pages": store.conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]}
    recent = store.conn.execute(
        "SELECT chat_id, MAX(ts) AS last_ts FROM messages GROUP BY chat_id ORDER BY last_ts DESC LIMIT 10").fetchall()
    chat_names = {r["id"]: r["display_name"] for r in store.conn.execute("SELECT id, display_name FROM chats").fetchall()}
    cust_map = {r["chat_id"]: r["customer_id"] for r in store.conn.execute("SELECT chat_id, customer_id FROM customer_chat_map").fetchall()}
    recent_chats = [{"chat_id": r["chat_id"], "display_name": chat_names.get(r["chat_id"]),
                     "last_ts": r["last_ts"], "customer_id": cust_map.get(r["chat_id"])} for r in recent]
    s = read_status(settings.status_path)
    return request.app.state.templates.TemplateResponse(
        request, "home.html",
        {"customers": customers, "knowledge": knowledge, "recent_chats": recent_chats,
         "status": s or {}, "alive": is_alive(settings.status_path)})


@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path)}


@router.get("/api/stats")
async def stats():
    store = _store()
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
        "SELECT chat_id, MAX(ts) AS last_ts FROM messages GROUP BY chat_id ORDER BY last_ts DESC LIMIT 10"
    ).fetchall()
    chat_names = {r["id"]: r["display_name"] for r in
                  store.conn.execute("SELECT id, display_name FROM chats").fetchall()}
    cust_map = {r["chat_id"]: r["customer_id"] for r in
                store.conn.execute("SELECT chat_id, customer_id FROM customer_chat_map").fetchall()}
    recent_chats = [{"chat_id": r["chat_id"], "display_name": chat_names.get(r["chat_id"]),
                     "last_ts": r["last_ts"], "customer_id": cust_map.get(r["chat_id"])} for r in recent]
    s = read_status(settings.status_path)
    return {"customers": customers, "knowledge": knowledge,
            "collector": {"alive": is_alive(settings.status_path), "status": s or {}},
            "recent_chats": recent_chats}


@router.get("/customers")
async def customers(request: Request):
    store = _store()
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
    store = _store()
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
    store = _store()
    msgs = store.list_messages(chat_id, limit=50, before_ts=before)
    # 时间正序展示
    msgs = sorted(msgs, key=lambda m: m.ts)
    older_ts = msgs[0].ts if msgs else None
    partial = request.query_params.get("partial") == "1"
    return request.app.state.templates.TemplateResponse(
        request, "chat_messages.html",
        {"customer_id": customer_id, "chat_id": chat_id, "messages": msgs,
         "older_ts": older_ts, "partial": partial},
    )


@router.post("/customers/{customer_id}/analyze")
async def customer_analyze(customer_id: str, request: Request):
    """6.4: 生成客户分析 (兴趣点/活跃度/跟进建议)。仅生成不写入画像。"""
    store = _store()
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
    store = _store()
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
    store = _store()
    if field and value:
        store.upsert_profile_field(customer_id, field, value, source="manual")
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "profile_list.html", {"profile": profile, "customer_id": customer_id},
    )


@router.get("/knowledge")
async def knowledge(request: Request):
    docs = _store().list_documents()
    return request.app.state.templates.TemplateResponse(request, "knowledge.html", {"docs": docs})


@router.get("/api/knowledge/list")
async def knowledge_list():
    """knowledge-base: 文档列表 (含 chunk/wiki 状态)。"""
    return {"docs": _store().list_documents()}


@router.delete("/api/knowledge/{doc_id}")
async def knowledge_delete(doc_id: str):
    """knowledge-base: 删除文档 (chunks + 向量 + wiki 引用一并清理)。"""
    store = _store()
    deleted = store.delete_document(doc_id)
    ChromaStore(embedding_fn=get_embedding().embed).delete_chunks(doc_id)
    return {"deleted": deleted, "doc_id": doc_id}


@router.post("/api/knowledge/search")
async def knowledge_search(request: Request):
    """knowledge-base: 检索测试 — 返回含来源文档与片段的检索结果。"""
    p = await _reply_params(request)
    query = p.get("message") or ""
    store = _store()
    vs = ChromaStore(embedding_fn=get_embedding().embed)
    # 向量召回 + BM25 关键词召回, 合并去重
    vec = vs.query_chunks(query, top_k=5)
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
    return request.app.state.templates.TemplateResponse(
        request, "knowledge_search.html", {"query": query, "results": merged})


async def _reply_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {customer_id, chat_id, message, style}。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    else:
        body = await request.form()
    return {k: (body.get(k) or "") for k in ("customer_id", "chat_id", "message", "style")}


def _render_reply_result(request: Request, customer_id: str, chat_id: str,
                         message: str, result: dict):
    return request.app.state.templates.TemplateResponse(
        request, "reply_result.html",
        {"customer_id": customer_id, "chat_id": chat_id, "message": message,
         "reply": result["reply"],
         "sources": result.get("sources", []), "style": result.get("style", "default")},
    )


@router.post("/api/reply")
async def reply(request: Request):
    p = await _reply_params(request)
    store = _store()
    vs = ChromaStore(embedding_fn=get_embedding().embed)
    pipe = RagPipeline(store, vs, get_reranker(), CloudLLM())
    result = generate_reply(pipe, p["customer_id"], p["chat_id"], p["message"],
                            style=p.get("style") or "default")
    return _render_reply_result(request, p["customer_id"], p["chat_id"], p["message"], result)


@router.post("/api/reply/regenerate")
async def reply_regenerate(request: Request):
    """reply-assist: 为同一条消息重新生成获得不同候选回复。"""
    p = await _reply_params(request)
    store = _store()
    vs = ChromaStore(embedding_fn=get_embedding().embed)
    pipe = RagPipeline(store, vs, get_reranker(), CloudLLM())
    result = regenerate_reply(pipe, p["customer_id"], p["chat_id"], p["message"],
                              previous_style=p.get("style") or "default")
    return _render_reply_result(request, p["customer_id"], p["chat_id"], p["message"], result)


@router.post("/api/knowledge/upload")
async def upload(file: bytes = File(...), filename: str = Form(...)):
    doc_id = str(uuid.uuid4())
    store = _store()
    store.conn.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,?)",
        (doc_id, filename, filename.split(".")[-1], "docreader", "processing", int(time.time())),
    )
    store.conn.commit()
    tmp = tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False)
    try:
        tmp.write(file)
        tmp.close()
        text = parse_document(tmp.name)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    RagIndex(store, ChromaStore(embedding_fn=get_embedding().embed)).index(doc_id, text)
    # Wiki 索引失败不影响 RAG 索引 (双索引互不阻塞)
    try:
        WikiIndex(store, CloudLLM(), get_embedding()).index(doc_id, text)
    except Exception:
        pass  # Wiki 失败不阻塞上传; RAG 索引已成功
    return {"doc_id": doc_id}


@router.post("/api/collector/backfill")
async def collector_backfill(body: dict):
    """3.7: 按需历史回溯 — 触发采集器滚动当前会话加载更早消息。
    body: {chat_id?: str, max_scrolls?: int}。
    采集器为独立进程, 实际滚动由采集器读取 status 触发或 CLI 执行;
    此端点记录请求意图供采集器轮询, 返回 accepted。"""
    chat_id = body.get("chat_id")
    max_scrolls = int(body.get("max_scrolls", 10))
    store = _store()
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
async def export_v():
    return {"exported": export_vault(_store(), settings.vault_export_dir)}
