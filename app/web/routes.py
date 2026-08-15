# app/web/routes.py
import time, uuid, tempfile
import sqlite3
import json
from pathlib import Path

from fastapi import APIRouter, Request, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.config import settings
from app.collector.scanner import read_status, is_alive
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.storage.runtime_settings import RuntimeSettings
from app.rag.pipeline import RagPipeline
from app.rag.reranker import get_reranker
from app.llm.cloud_llm import CloudLLM
from app.llm.bge_embedding import get_embedding
from app.knowledge.parser import parse_document
from app.knowledge.rag_index import RagIndex
from app.knowledge.wiki_index import WikiIndex
from app.knowledge.wiki_export import export_vault
from app.reply.generator import generate_reply, NEXT_STYLE

router = APIRouter()

WARMUP_TIMEOUT_SEC = 30.0  # 首次请求等待模型预热就绪的超时 (3.3)

# multilingual-reply-generation: 结果卡片展示用中文标签 (hx-vals 回传仍用原始码值)
_REPLY_LANGUAGE_LABELS = {"zh": "中文", "en": "English", "ru": "俄语"}
_REPLY_SCENARIO_LABELS = {"inquiry": "询价", "bargain": "砍价", "inspection": "看车",
                          "logistics": "物流", "payment": "付款", "after_sale": "售后"}


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
    return {"status": s, "alive": is_alive(settings.status_path),
            "scan": (s or {}).get("scan") or None}


SETTING_VALIDATORS = {
    "fast_tick_sec":        {"kind": "float", "min": 1e-9},
    "slow_tick_sec":        {"kind": "float", "min": 1e-9},
    "auto_scan_interval_sec": {"kind": "float", "min": 1e-9},
    "auto_scan_max_chats":  {"kind": "int", "min": 1, "max": 1000},
    "auto_scan_settle_sec": {"kind": "float", "min": 0.1, "max": 30},
    "auto_scan_chats":      {"kind": "bool"},
}


def _validate_setting(key, raw) -> tuple:
    """返回 (ok, 规范化值 or 错误提示)。bool 返回 Python bool, 数值返回字符串。"""
    spec = SETTING_VALIDATORS.get(key)
    if spec is None:
        return False, "未知参数"
    if spec["kind"] == "bool":
        s = str(raw).strip().lower()
        if s in ("true", "1"):
            return True, True
        if s in ("false", "0"):
            return True, False
        return False, "必须是布尔值 (true/false)"
    try:
        val = float(raw) if spec["kind"] == "float" else int(raw)
    except (TypeError, ValueError):
        return False, "必须为数值"
    if spec["kind"] == "float" and (val != val or val in (float("inf"), float("-inf"))):
        return False, "必须为有限数值"  # NaN / ±Infinity 视为非法 (与 get_typed 一致)
    if spec["kind"] == "int" and float(raw) != val:
        return False, "必须为整数"
    if val <= spec.get("min", 1e-9) or val > spec.get("max", float("inf")):
        rng = f"须在 {spec.get('min')}~{spec.get('max')}" if "max" in spec else "须大于 0"
        return False, rng
    return True, str(val)


def _rt(request: Request) -> RuntimeSettings:
    return RuntimeSettings(_store(request))


def _typed_values(rt: RuntimeSettings) -> dict:
    """各参数的当前生效值 (typed: bool→bool, 数值→float/int)。"""
    db = rt.all()
    out = {}
    for key, default in RuntimeSettings.DEFAULTS.items():
        out[key] = rt.get_typed(key, default)
    return out


@router.get("/api/settings")
async def settings_get(request: Request):
    rt = _rt(request)
    return {"values": _typed_values(rt), "defaults": dict(RuntimeSettings.DEFAULTS)}


@router.post("/api/settings")
async def settings_post(request: Request):
    body = await request.json()
    payload = (body or {}).get("values") or {}
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body.values 必须为对象", "field": None}, status_code=400)
    rt = _rt(request)
    validated = {}
    for key, raw in payload.items():
        ok, msg_or_val = _validate_setting(key, raw)
        if not ok:
            return JSONResponse({"error": f"{key}: {msg_or_val}", "field": key}, status_code=400)
        validated[key] = msg_or_val
    # 全通过才写库 (原子); bool 存字符串, 数值存规范化字符串
    for key, value in validated.items():
        rt.set(key, value if isinstance(value, str) else ("true" if value else "false"))
    return {"values": _typed_values(rt)}


@router.post("/api/settings/reset")
async def settings_reset(request: Request):
    body = await request.json()
    key = (body or {}).get("key")
    if key not in RuntimeSettings.DEFAULTS:
        return JSONResponse({"error": "未知参数", "field": key}, status_code=400)
    rt = _rt(request)
    rt.reset(key)
    return {"defaults": {key: RuntimeSettings.DEFAULTS[key]}}


def _search_messages(store, query, limit=20):
    """D1: 消息 FTS 行 join 回 messages 取 chat_id/body/ts (search_fts 已含 rowid)。"""
    out = []
    for r in store.search_fts("messages", query, limit):
        row = store.conn.execute(
            "SELECT chat_id, ts, body FROM messages WHERE rowid=?", (r["rowid"],)).fetchone()
        if row:
            out.append({"chat_id": row["chat_id"], "ts": row["ts"], "body": row["body"]})
    return out


def _search_knowledge(store, query, limit=20):
    """D1: 知识库 FTS join 回 doc_chunks 取 doc_id (参照 knowledge_search 的 doc_lookup)。"""
    doc_lookup = {}
    for r in store.conn.execute("SELECT rowid, doc_id FROM doc_chunks").fetchall():
        doc_lookup[r["rowid"]] = r["doc_id"]
    out = []
    for r in store.search_fts("doc_chunks", query, limit):
        out.append({"doc_id": doc_lookup.get(r["rowid"]), "text": r["text"]})
    return out


@router.get("/api/search")
async def api_search(request: Request, q: str = ""):
    """D1: 全局搜索聚合四源 → JSON 分组; htmx 请求 (HX-Request) 返回渲染片段。"""
    query = (q or "").strip()
    store = _store(request)
    result = {"query": query, "customers": [], "messages": [], "knowledge": [], "profiles": []}
    try:
        if query:
            result["customers"] = store.search_customers(query)
            result["messages"] = _search_messages(store, query)
            result["knowledge"] = _search_knowledge(store, query)
            result["profiles"] = store.search_profiles(query)
    except Exception as e:
        result["error"] = f"搜索失败: {e}"
    if request.headers.get("HX-Request"):
        return request.app.state.templates.TemplateResponse(request, "search_results.html", result)
    return result


@router.get("/search")
async def search_page(request: Request):
    """D1: 全局搜索页 (htmx 驱动 /api/search)。"""
    return request.app.state.templates.TemplateResponse(request, "search.html", {})


async def _parse_body(request: Request) -> dict:
    """统一解析请求体: JSON body 或表单 (htmx 默认 form-encoded) → dict。
    JSON 解析失败或非 dict 时回退空 dict。"""
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
    else:
        body = await request.form()
    return body


async def _cleanup_params(request: Request) -> dict:
    """从 JSON body 或表单解析 {mode, chat_id, days} (htmx 表单默认 form-encoded)。"""
    body = await _parse_body(request)

    def _safe_str(v):
        return v if isinstance(v, str) else ""

    return {"mode": _safe_str(body.get("mode")).strip(),
            "chat_id": _safe_str(body.get("chat_id")).strip(),
            "days": body.get("days")}


@router.post("/api/cleanup")
async def cleanup(request: Request):
    """D2: 手动清理聊天消息。body: {mode: chat|days, chat_id?, days?}。
    删除 messages + 重建 FTS + 对应 chat 消息向量; 保留画像与知识库。"""
    body = await _cleanup_params(request)
    mode = body["mode"]
    store = _store(request)
    if mode == "chat":
        chat_id = (body.get("chat_id") or "").strip()
        if not chat_id:
            return JSONResponse({"error": "chat 模式需提供 chat_id"}, status_code=400)
        try:
            res = store.delete_messages_by_chat(chat_id)
        except Exception as e:
            return {"error": f"清理失败: {e}"}
        chat_ids = res["affected_chats"]
    elif mode == "days":
        days_raw = body.get("days")
        if days_raw is None or str(days_raw).strip() == "":
            return JSONResponse({"error": "days 模式需提供天数"}, status_code=400)
        if isinstance(days_raw, bool) or not isinstance(days_raw, (int, float, str)):
            return JSONResponse({"error": "days 必须为正整数"}, status_code=400)
        try:
            days = int(days_raw)
        except (TypeError, ValueError):
            return JSONResponse({"error": "days 必须为正整数"}, status_code=400)
        if days <= 0 or float(days_raw) != days:
            return JSONResponse({"error": "days 必须为正整数"}, status_code=400)
        cutoff = int(time.time()) - days * 86400
        try:
            res = store.delete_messages_before(cutoff)
        except Exception as e:
            return {"error": f"清理失败: {e}"}
        chat_ids = res["affected_chats"]
    else:
        return JSONResponse({"error": "mode 必须是 chat 或 days"}, status_code=400)
    try:
        vs = _chroma_store(request)
        for cid in chat_ids:
            vs.delete_message_vectors(cid)
    except Exception as e:
        return {"deleted_rows": res["deleted_rows"], "affected_chats": chat_ids,
                "error": f"消息已删除但向量清理失败: {e}"}
    return {"deleted_rows": res["deleted_rows"], "affected_chats": chat_ids}


@router.get("/cleanup")
async def cleanup_page(request: Request):
    """D2: 数据清理管理页 (chat / days 两种模式, 删除前确认)。"""
    return request.app.state.templates.TemplateResponse(request, "cleanup.html", {})


@router.get("/settings")
async def settings_page(request: Request):
    """采集器设置中心页 (JS 驱动 /api/settings)。"""
    return request.app.state.templates.TemplateResponse(request, "settings.html", {})


@router.get("/api/stats")
async def stats(request: Request):
    st = _build_stats(_store(request))
    s = read_status(settings.status_path)
    return {**st, "collector": {"alive": is_alive(settings.status_path), "status": s or {}}}


@router.get("/workspace")
async def workspace(request: Request):
    """workspace-layout: 三栏工作台 — 左栏客户列表 + 空中栏/右栏 (点客户渐进加载)。
    workspace-live-refresh: 左栏按最近活跃降序, 附最近消息时间与未读数。"""
    store = _store(request)
    rows = store.conn.execute("SELECT * FROM customers").fetchall()
    profiles_by_customer: dict[str, str] = {}
    for r in store.conn.execute("SELECT customer_id, field, value FROM profiles").fetchall():
        s = profiles_by_customer.setdefault(r["customer_id"], "")
        profiles_by_customer[r["customer_id"]] = f"{s} {r['field']}={r['value']}"
    # 每个客户最近活跃 (最近消息时间 + 未读数), 按最近活跃降序 (批量查询避免 N+1)
    activity = store.get_customers_recent_activity([r["id"] for r in rows])
    customers = sorted(rows, key=lambda c: activity.get(c["id"], {}).get("last_ts", 0), reverse=True)
    return request.app.state.templates.TemplateResponse(
        request, "workspace.html",
        {"customers": customers, "profiles_by_customer": profiles_by_customer,
         "activity": activity})


@router.get("/workspace/customer/{customer_id}/chat")
async def workspace_chat(customer_id: str, request: Request):
    """workspace-layout: 中栏客户聊天窗口 — 取该客户关联会话消息。
    支持 ?chat_id= 指定会话; 缺省取置信度最高会话。返回全部会话供切换。"""
    store = _store(request)
    customer = store.conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    chats = store.conn.execute(
        "SELECT chat_id, match_confidence FROM customer_chat_map WHERE customer_id=? "
        "ORDER BY match_confidence DESC", (customer_id,)).fetchall()
    # 补充每个会话的显示名与类型 (群聊/私聊)
    chat_list = []
    for c in chats:
        row = store.conn.execute("SELECT display_name, kind FROM chats WHERE id=?", (c["chat_id"],)).fetchone()
        chat_list.append({
            "chat_id": c["chat_id"],
            "match_confidence": c["match_confidence"],
            "name": (row["display_name"] if row and row["display_name"] else c["chat_id"]),
            "kind": (row["kind"] if row else "single"),
        })
    # 指定会话或取置信度最高
    req_chat = (request.query_params.get("chat_id") or "").strip()
    if req_chat and any(c["chat_id"] == req_chat for c in chat_list):
        chat_id = req_chat
    else:
        chat_id = chat_list[0]["chat_id"] if chat_list else None
    msgs = store.list_messages(chat_id, limit=50) if chat_id else []
    msgs = sorted(msgs, key=lambda m: m.ts)
    # workspace-load-earlier: 判断是否还有更早消息 (供"加载更早"按钮)
    has_more = False
    oldest_ts = None
    if msgs:
        oldest_ts = msgs[0].ts
        has_more = store.conn.execute(
            "SELECT 1 FROM messages WHERE chat_id=? AND ts<? LIMIT 1",
            (chat_id, oldest_ts)).fetchone() is not None
    kind = None
    if chat_id:
        row = store.conn.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
        kind = row["kind"] if row else None
    session_id = store.find_or_create_reply_session(customer_id, chat_id) if chat_id else None
    # workspace-live-refresh: 打开聊天视为已读, 记录最后查看时间
    if chat_id:
        store.set_last_seen(customer_id, int(time.time()))
    return request.app.state.templates.TemplateResponse(
        request, "workspace_chat.html",
        {"customer_id": customer_id, "chat_id": chat_id, "messages": msgs, "kind": kind,
         "session_id": session_id, "chats": chat_list,
         "has_more": has_more, "oldest_ts": oldest_ts,
         "customer": dict(customer) if customer else None})


@router.get("/workspace/customer/{customer_id}/chat/earlier")
async def workspace_chat_earlier(customer_id: str, request: Request):
    """workspace-load-earlier: 中栏加载更早消息 — 返回 ts < before_ts 的消息气泡片段。
    前端 prepend 到消息列表顶部 (hx-swap=outerHTML 替换加载按钮)。"""
    store = _store(request)
    before_raw = request.query_params.get("before_ts") or "0"
    try:
        before = int(before_raw)
    except (TypeError, ValueError):
        before = 0
    chat_id = (request.query_params.get("chat_id") or "").strip()
    if chat_id:
        # 校验该会话确实属于该客户 (防越权读取其他客户会话)
        owned = store.conn.execute(
            "SELECT 1 FROM customer_chat_map WHERE customer_id=? AND chat_id=?",
            (customer_id, chat_id)).fetchone()
        if not owned:
            chat_id = ""
    if not chat_id:
        chats = store.conn.execute(
            "SELECT chat_id FROM customer_chat_map WHERE customer_id=? "
            "ORDER BY match_confidence DESC", (customer_id,)).fetchall()
        chat_id = chats[0]["chat_id"] if chats else None
    if not chat_id:
        return HTMLResponse("")
    msgs = store.list_messages(chat_id, limit=50, before_ts=before)
    msgs = sorted(msgs, key=lambda m: m.ts)
    has_more = False
    oldest_ts = None
    if msgs:
        oldest_ts = msgs[0].ts
        has_more = store.conn.execute(
            "SELECT 1 FROM messages WHERE chat_id=? AND ts<? LIMIT 1",
            (chat_id, oldest_ts)).fetchone() is not None
    kind = None
    row = store.conn.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
    kind = row["kind"] if row else None
    return request.app.state.templates.TemplateResponse(
        request, "workspace_chat_earlier.html",
        {"customer_id": customer_id, "chat_id": chat_id, "messages": msgs, "kind": kind,
         "has_more": has_more, "oldest_ts": oldest_ts})


@router.get("/workspace/customer/{customer_id}/chat/poll")
async def workspace_chat_poll(customer_id: str, request: Request):
    """workspace-live-refresh: 中栏增量拉取 — 返回 ts > after_ts 的新消息气泡片段。
    前端轮询追加 (hx-swap=beforeend)。无新消息返回空片段。"""
    store = _store(request)
    after_raw = request.query_params.get("after_ts") or "0"
    try:
        after_ts = int(after_raw)
    except (TypeError, ValueError):
        after_ts = 0
    chat_id = (request.query_params.get("chat_id") or "").strip()
    if chat_id:
        # 校验该会话确实属于该客户 (防越权读取其他客户会话)
        owned = store.conn.execute(
            "SELECT 1 FROM customer_chat_map WHERE customer_id=? AND chat_id=?",
            (customer_id, chat_id)).fetchone()
        if not owned:
            chat_id = ""
    if not chat_id:
        # 缺省取该客户置信度最高会话
        chats = store.conn.execute(
            "SELECT chat_id FROM customer_chat_map WHERE customer_id=? "
            "ORDER BY match_confidence DESC", (customer_id,)).fetchall()
        chat_id = chats[0]["chat_id"] if chats else None
    if not chat_id:
        return HTMLResponse("")
    msgs = store.list_messages_after(chat_id, after_ts, limit=200)
    if not msgs:
        return HTMLResponse("")
    kind = None
    row = store.conn.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
    kind = row["kind"] if row else None
    return request.app.state.templates.TemplateResponse(
        request, "workspace_chat_poll.html",
        {"messages": msgs, "kind": kind})


@router.get("/workspace/customer/{customer_id}/side")
async def workspace_side(customer_id: str, request: Request):
    """workspace-layout: 右栏客户画像 + 对话摘要 + AI 建议。"""
    store = _store(request)
    customer = store.conn.execute("SELECT * FROM customers WHERE id=?", (customer_id,)).fetchone()
    profile = store.get_profile(customer_id)
    summary = store.get_customer_summary(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "workspace_side.html",
        {"customer_id": customer_id, "customer": dict(customer) if customer else None,
         "profile": profile, "summary": summary})


@router.post("/customers/{customer_id}/summarize")
async def customer_summarize(customer_id: str, request: Request):
    """customer-summary: 创建摘要任务 (worker 异步生成/增量更新), 返回轮询片段。
    仅生成不覆盖画像。"""
    store = _store(request)
    task_id = store.create_summary_task(customer_id)
    return request.app.state.templates.TemplateResponse(
        request, "summary_polling.html", {"task_id": task_id},
    )


@router.get("/api/summary/status/{task_id}")
async def summary_status(task_id: str, request: Request):
    """customer-summary: 轮询端点。pending/running → 处理中片段(继续轮询);
    done → 摘要卡片(停止轮询); failed → 错误片段。"""
    store = _store(request)
    task = store.get_summary_task(task_id)
    if task is None:
        return HTMLResponse('<p class="muted">任务不存在或已过期</p>')
    if task["status"] in ("pending", "running"):
        return request.app.state.templates.TemplateResponse(
            request, "summary_polling.html", {"task_id": task_id})
    if task["status"] == "failed":
        return request.app.state.templates.TemplateResponse(
            request, "summary.html",
            {"customer_id": task["customer_id"], "summary": None, "error": task["error"]})
    # done: 从 customer_summaries 读最新摘要展示
    summary = store.get_customer_summary(task["customer_id"])
    return request.app.state.templates.TemplateResponse(
        request, "summary.html",
        {"customer_id": task["customer_id"], "summary": summary, "error": None},
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


@router.post("/customers/{customer_id}/followup")
async def customer_followup(customer_id: str, request: Request):
    """workspace-reply-profile: 生成结构化跟进建议 (优先级/下一步/话术/时机/依据)。"""
    store = _store(request)
    from app.profile.followup import generate_followup
    try:
        followup = generate_followup(store, CloudLLM(), customer_id)
    except Exception as e:
        followup = {"priority": "medium", "next_action": f"生成失败: {e}",
                    "suggested_message": "", "best_time": "", "reason": ""}
    return request.app.state.templates.TemplateResponse(
        request, "followup.html", {"customer_id": customer_id, "followup": followup},
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
    """web-app: 画像页编辑某字段并保存 → 持久化并标记为人工来源 (source=manual)。

    intent_level/tags 允许空值 (未分层/清空标签, F1); 其他字段保持原守卫。
    人工调整 intent_level/tags 追加 manual 历史行 (D3, F2)。
    """
    body = await request.form()
    field = (body.get("field") or "").strip()
    value = (body.get("value") or "").strip()
    store = _store(request)
    if field and (value or field in ("intent_level", "tags")):
        store.upsert_profile_field(customer_id, field, value, source="manual")
        if field in ("intent_level", "tags"):
            # 单字段观察: 另一字段取当前画像值
            current = {p.field: p.value for p in store.get_profile(customer_id)}
            level = value if field == "intent_level" else current.get("intent_level", "")
            tags = value if field == "tags" else current.get("tags", "")
            store.add_tier_history(customer_id, level, tags, "manual")
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
    """从 JSON body 或表单解析 {customer_id, chat_id, message, style, session_id,
    language, scenario, formality}。"""
    body = await _parse_body(request)
    keys = ("customer_id", "chat_id", "message", "style", "session_id",
            "language", "scenario", "formality")
    return {k: (body.get(k) or "") for k in keys}


def _render_reply_result(request: Request, customer_id: str, chat_id: str,
                         message: str, result: dict, session_id: str | None = None):
    language = result.get("language", "")
    scenario = result.get("scenario", "")
    return request.app.state.templates.TemplateResponse(
        request, "reply_result.html",
        {"customer_id": customer_id, "chat_id": chat_id, "message": message,
         "reply": result.get("reply", ""),
         "sources": result.get("sources", []), "style": result.get("style", "default"),
         "language": language, "scenario": scenario,
         "language_label": _REPLY_LANGUAGE_LABELS.get(language, language),
         "scenario_label": _REPLY_SCENARIO_LABELS.get(scenario, scenario),
         "formality": result.get("formality", ""),
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


async def _create_reply_task(request: Request, mode: str) -> Response:
    """创建回复任务 (W3 合并逻辑): mode=generate 追加会话历史;
    mode=regenerate 只读历史不追加, 并用 NEXT_STYLE 轮换风格。"""
    p = await _reply_params(request)
    store = _store(request)
    session_id = await _reply_session(request, p["customer_id"], p["chat_id"], p.get("session_id"))
    style = p.get("style") or "default"
    if mode == "regenerate":
        style = NEXT_STYLE.get(style, "default")
    task_id = store.create_reply_task(
        p["customer_id"], p["chat_id"], p["message"], style, session_id, mode=mode,
        language=p.get("language") or None, scenario=p.get("scenario") or None,
        formality=p.get("formality") or None)
    return request.app.state.templates.TemplateResponse(
        request, "reply_polling.html", {"task_id": task_id})


@router.post("/api/reply")
async def reply(request: Request):
    """reply-assist: 创建回复任务 (mode=generate, 追加会话历史)。"""
    return await _create_reply_task(request, "generate")


@router.post("/api/reply/regenerate")
async def reply_regenerate(request: Request):
    """reply-assist: 重生成别名 — 强制 mode=regenerate (向后兼容)。"""
    return await _create_reply_task(request, "regenerate")


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


@router.post("/api/collector/scan")
async def collector_scan(request: Request):
    """手动触发全量扫描 (意图表排队, 采集器轮询消费)。
    已有 pending/running 未完成请求 → 409 busy; 采集器离线不拦截。"""
    store = _store(request)
    if store.has_active_scan_request():
        return JSONResponse({"busy": True, "error": "已有扫描进行中"}, status_code=409)
    store.create_scan_request()
    return {"accepted": True}


@router.post("/api/knowledge/export-vault")
async def export_v(request: Request):
    return {"exported": export_vault(_store(request), settings.vault_export_dir)}


# ---- customer-intent-tiering: 分层分析 API ----
@router.post("/api/tiering/analyze")
async def tiering_analyze(request: Request):
    """创建分层任务。body 可选 customer_ids (缺省=近期活跃客户)。"""
    store = _store(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    customer_ids = body.get("customer_ids") if isinstance(body, dict) else None
    if customer_ids is not None:
        if (not isinstance(customer_ids, list) or not customer_ids
                or not all(isinstance(c, str) for c in customer_ids)):
            return JSONResponse({"error": "customer_ids 必须为非空字符串数组"}, status_code=400)
    else:
        customer_ids = store.list_recent_active_customers(settings.tiering_active_days)
    if not customer_ids:
        return {"task_id": None, "error": "无待分层客户"}
    dropped = 0
    if len(customer_ids) > settings.tiering_max_customers:
        dropped = len(customer_ids) - settings.tiering_max_customers
        customer_ids = customer_ids[:settings.tiering_max_customers]
    task_id = store.create_tiering_task(customer_ids)
    return {"task_id": task_id, "dropped": dropped}


@router.get("/api/tiering/status/{task_id}")
async def tiering_status(task_id: str, request: Request):
    store = _store(request)
    task = store.get_tiering_task(task_id)
    if task is None:
        return {"status": "not_found"}
    return {"status": task["status"], "progress": task["progress"],
            "result": task["result"], "error": task["error"]}


@router.get("/api/tiering/history/{customer_id}")
async def tiering_history(customer_id: str, request: Request):
    store = _store(request)
    return {"customer_id": customer_id, "history": store.get_tier_history(customer_id)}
