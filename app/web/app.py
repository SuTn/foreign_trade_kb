# app/web/app.py
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.web.routes import router, _build_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Web 进程级单例: 启动时持有 sqlite store, 关闭时释放连接。

    chroma store 由 routes 首次访问时惰性创建并缓存 (embedding_fn 需走 routes 的
    get_embedding 以便测试 monkeypatch), 此处仅预置占位 None。
    """
    store = _build_store()
    app.state.sqlite_store = store
    app.state.chroma_store = None
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
