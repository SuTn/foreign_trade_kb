# app/web/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from app.config import settings
from app.web.routes import router

def create_app() -> FastAPI:
    app = FastAPI(title="外贸客户知识库")
    base = Path(__file__).parent
    app.mount("/static", StaticFiles(directory=str(base/"static")), name="static")
    settings.avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/avatars", StaticFiles(directory=str(settings.avatars_dir.resolve())), name="avatars")
    templates = Jinja2Templates(directory=str(base/"templates"))
    app.state.templates = templates
    app.include_router(router)
    return app
