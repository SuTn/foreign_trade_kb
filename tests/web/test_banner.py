from fastapi.testclient import TestClient
from app.web.app import create_app


def test_collector_banner_renders_in_base(tmp_data):
    """3.1: base.html 含隐藏横幅容器, 任意继承页可渲染。"""
    html = TestClient(create_app()).get("/").text
    assert 'id="collector-banner"' in html
    assert "hidden" in html
    assert "采集器异常" in html
