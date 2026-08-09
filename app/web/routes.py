# app/web/routes.py
import time, uuid, tempfile
from pathlib import Path

from fastapi import APIRouter, Request, File, Form

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
from app.reply.generator import generate_reply

router = APIRouter()


def _store() -> SqliteStore:
    return SqliteStore()


@router.get("/")
async def index(request: Request):
    s = read_status(settings.status_path)
    return request.app.state.templates.TemplateResponse(
        request, "home.html",
        {"status": s or {}, "alive": is_alive(settings.status_path)},
    )


@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path)}


@router.get("/customers")
async def customers(request: Request):
    store = _store()
    rows = store.conn.execute("SELECT * FROM customers").fetchall()
    return request.app.state.templates.TemplateResponse(request, "customers.html", {"customers": rows})


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
        request, "profile_list.html", {"profile": profile},
    )


@router.get("/knowledge")
async def knowledge(request: Request):
    return request.app.state.templates.TemplateResponse(request, "knowledge.html", {})


@router.post("/api/reply")
async def reply(body: dict):
    store = _store()
    vs = ChromaStore(embedding_fn=get_embedding().embed)
    pipe = RagPipeline(store, vs, get_reranker(), CloudLLM())
    return generate_reply(pipe, body["customer_id"], body["chat_id"], body["message"])


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
