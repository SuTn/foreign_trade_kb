# tests/web/test_app.py
from fastapi.testclient import TestClient
from app.web.app import create_app

def test_index():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200

def test_collector_status_endpoint():
    client = TestClient(create_app())
    r = client.get("/api/collector/status")
    assert r.status_code == 200
    assert "alive" in r.json()

def test_app_state_singletons():
    """Task4: Web 进程级 store 单例 — lifespan 启动持有 sqlite/chroma 单例, 关闭时释放。"""
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    with TestClient(create_app()) as client:
        app = client.app
        assert hasattr(app.state, "sqlite_store")
        assert hasattr(app.state, "chroma_store")
        assert app.state.sqlite_store is app.state.sqlite_store  # 单例
        r = client.get("/")
        assert r.status_code == 200

def test_sqlite_store_reused_across_requests():
    """无 lifespan (TestClient 不带 with) 时, 首次请求惰性创建并缓存单例, 后续复用。"""
    from fastapi.testclient import TestClient
    from app.web.app import create_app

    client = TestClient(create_app())
    r1 = client.get("/")
    assert r1.status_code == 200
    s1 = client.app.state.sqlite_store
    assert s1 is not None
    r2 = client.get("/api/stats")
    assert r2.status_code == 200
    assert client.app.state.sqlite_store is s1
