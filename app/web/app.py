# app/web/app.py
import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.web import routes
from app.web.routes import router, _build_store
from app.web.worker import start_worker


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_local_url(raw: str) -> bool:
    """判断 Origin/Referer 是否指向本机 (127.0.0.1 / localhost / ::1)。"""
    if not raw:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(raw).hostname or "").lower()
    except Exception:
        return False
    return host in _LOCAL_HOSTS


async def _csrf_guard(request: Request, call_next):
    """CSRF 防护: 非只读请求若来自跨站 Origin/Referer 则拒绝。

    浏览器发起的跨站请求会带指向外部站点的 Origin 或 Referer (以及 Sec-Fetch-Site:
    cross-site); 本机 UI 的同源请求带本机 Origin。无这些头的客户端 (curl/脚本/测试)
    放行。服务仅绑定 127.0.0.1, 所有合法来源均为本机。
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return JSONResponse({"error": "跨站请求被拒绝"}, status_code=403)
    for hdr in (request.headers.get("origin"), request.headers.get("referer")):
        if hdr and not _is_local_url(hdr):
            return JSONResponse({"error": "跨站请求被拒绝"}, status_code=403)
    return await call_next(request)


def _hf_model_cached(model_name: str) -> bool:
    key = "models--" + model_name.replace("/", "--")
    return (Path.home() / ".cache" / "huggingface" / "hub" / key).is_dir()


def _warmup_enabled() -> bool:
    """测试环境跳过真实模型预热, 避免下载/加载拖慢用例。"""
    return "pytest" not in sys.modules


def _warmup_models(app: FastAPI):
    """后台线程预热 embedding/reranker 实例 (触发模型加载并缓存)。

    仅预载本地已缓存的模型; 未缓存/网络模型跳过, 首次请求按超时降级。
    """
    if not _warmup_enabled():
        app.state.embedding_ready.set()
        return
    from app.web import routes as _routes
    log = logging.getLogger(__name__)
    try:
        emb = _routes.get_embedding()
        app.state.embedding = emb
        name = getattr(emb, "_model_name", None)
        if name and name.startswith("BAAI/") and not _hf_model_cached(name):
            log.info("embedding 模型未本地缓存, 跳过预热: %s", name)
        else:
            emb.embed("预热")
    except Exception:
        log.warning("embedding 预热失败", exc_info=True)
    try:
        rer = _routes.get_reranker()
        app.state.reranker = rer
        name = getattr(rer, "_name", None)
        if name and name.startswith("BAAI/") and not _hf_model_cached(name):
            log.info("reranker 模型未本地缓存, 跳过预热: %s", name)
        else:
            rer.rerank("预热", [{"text": "预热"}], top_k=1)
    except Exception:
        log.warning("reranker 预热失败", exc_info=True)
    finally:
        app.state.embedding_ready.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Web 进程级单例: 持有 sqlite store / llm 单例, 启动 reply worker, 关闭时释放连接。

    D3: app.state.llm 为进程级 CloudLLM 单例 (worker 与路由复用, client 懒加载)。
    D7: 启动时清理遗留 pending/running 任务为 failed (进程重启残留)。
    D1: 常驻 daemon worker 线程消费 reply_tasks。
    chroma store 由 routes/worker 首次访问时惰性创建并缓存 (embedding_fn 需走 routes 的
    get_embedding 以便测试 monkeypatch), 此处仅预置占位 None。
    embedding/reranker 在后台线程预热 (3.3), 不阻塞启动。
    """
    store = _build_store()
    app.state.sqlite_store = store
    app.state.chroma_store = None
    app.state.embedding = None
    app.state.reranker = None
    app.state.llm = routes.CloudLLM()  # D3 单例; 走 routes 名字以便测试 monkeypatch 替换
    app.state.embedding_ready = threading.Event()
    store.mark_legacy_reply_tasks_failed()  # D7 (worker 起跑前清理)
    store.mark_legacy_summary_tasks_failed()  # 摘要任务同构清理
    app.state.reply_worker = threading.Thread(target=start_worker, args=(app,), daemon=True)
    app.state.reply_worker.start()
    threading.Thread(target=_warmup_models, args=(app,), daemon=True).start()
    try:
        yield
    finally:
        try:
            store.conn.close()
        except Exception:
            pass
        routes.close_thread_connections()  # A2: 关闭每线程连接


def create_app() -> FastAPI:
    app = FastAPI(title="外贸客户知识库", lifespan=lifespan)
    app.middleware("http")(_csrf_guard)
    base = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=str(base/"static")), name="static")
    settings.avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(settings.avatars_dir.resolve())), name="avatars")
    templates = Jinja2Templates(directory=str(base/"templates"))
    templates.env.filters["fmt_ts"] = _fmt_ts
    templates.env.filters["ws_time"] = _ws_time
    app.state.templates = templates
    app.include_router(router)
    return app


def _fmt_ts(ts) -> str:
    """把 unix 时间戳格式化为可读日期 (YYYY-MM-DD HH:MM); 非法/空返回原样。"""
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts) if ts else ""


def _ws_time(ts) -> str:
    """工作台左栏相对时间: 今天显示 HH:MM, 昨天显示 '昨天', 更早显示 MM-DD。"""
    import datetime
    try:
        dt = datetime.datetime.fromtimestamp(int(ts))
    except (TypeError, ValueError, OSError):
        return ""
    now = datetime.datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if (now.date() - dt.date()).days == 1:
        return "昨天"
    if dt.year == now.year:
        return dt.strftime("%m-%d")
    return dt.strftime("%Y-%m-%d")
