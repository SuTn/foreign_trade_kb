# app/web/app.py
import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.web.routes import router, _build_store


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
    """Web 进程级单例: 启动时持有 sqlite store, 关闭时释放连接。

    chroma store 由 routes 首次访问时惰性创建并缓存 (embedding_fn 需走 routes 的
    get_embedding 以便测试 monkeypatch), 此处仅预置占位 None。
    embedding/reranker 在后台线程预热 (3.3), 不阻塞启动。
    """
    store = _build_store()
    app.state.sqlite_store = store
    app.state.chroma_store = None
    app.state.embedding = None
    app.state.reranker = None
    app.state.embedding_ready = threading.Event()
    threading.Thread(target=_warmup_models, args=(app,), daemon=True).start()
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
    app.mount("/static", StaticFiles(directory=str(base/"static")), name="static")
    settings.avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(settings.avatars_dir.resolve())), name="avatars")
    templates = Jinja2Templates(directory=str(base/"templates"))
    app.state.templates = templates
    app.include_router(router)
    return app
