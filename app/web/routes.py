# app/web/routes.py
from fastapi import APIRouter, Request
from app.config import settings
from app.collector.scanner import read_status, is_alive

router = APIRouter()

@router.get("/")
async def index(request: Request):
    return request.app.state.templates.TemplateResponse(request, "base.html", {"page": "home"})

@router.get("/api/collector/status")
async def collector_status():
    s = read_status(settings.status_path)
    return {"status": s, "alive": is_alive(settings.status_path)}
