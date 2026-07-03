# app/web/routes.py
import time, uuid, tempfile
from pathlib import Path

from fastapi import APIRouter, Request, File, Form

from app.config import settings
from app.collector.scanner import read_status, is_alive
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.rag.pipeline import RagPipeline
from app.rag.reranker import BgeReranker
from app.llm.cloud_llm import CloudLLM
from app.llm.bge_embedding import BgeEmbedding
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
    return request.app.state.templates.TemplateResponse(request, "base.html", {"page": "home"})


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
    profile = store.get_profile(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "chat.html", {"customer_id": customer_id, "profile": profile}
    )


@router.get("/knowledge")
async def knowledge(request: Request):
    return request.app.state.templates.TemplateResponse(request, "knowledge.html", {})


@router.post("/api/reply")
async def reply(body: dict):
    store = _store()
    vs = ChromaStore(embedding_fn=BgeEmbedding().embed)
    pipe = RagPipeline(store, vs, BgeReranker(), CloudLLM())
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
    RagIndex(store, ChromaStore(embedding_fn=BgeEmbedding().embed)).index(doc_id, text)
    # Wiki 索引失败不影响 RAG 索引 (双索引互不阻塞)
    try:
        WikiIndex(store, CloudLLM(), BgeEmbedding()).index(doc_id, text)
    except Exception:
        pass  # Wiki 失败不阻塞上传; RAG 索引已成功
    return {"doc_id": doc_id}


@router.post("/api/knowledge/export-vault")
async def export_v():
    return {"exported": export_vault(_store(), settings.vault_export_dir)}
