from fastapi.testclient import TestClient
from app.web.app import create_app
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings


def test_settings_get_returns_effective_and_defaults(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("fast_tick_sec", "3.5")
    client = TestClient(create_app())
    r = client.get("/api/settings")
    assert r.status_code == 200
    j = r.json()
    assert j["values"]["fast_tick_sec"] == 3.5    # DB 值生效 (typed)
    assert j["defaults"]["fast_tick_sec"] == 2.0  # .env 默认
    assert set(j["values"]) == set(j["defaults"])  # 六项齐全
    assert j["values"]["auto_scan_chats"] is True  # bool typed


def test_settings_post_saves_and_returns_new_values(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"fast_tick_sec": "4.0",
                                                       "auto_scan_chats": "false"}})
    assert r.status_code == 200
    j = r.json()
    assert j["values"]["fast_tick_sec"] == 4.0
    assert j["values"]["auto_scan_chats"] is False
    store = SqliteStore()
    assert RuntimeSettings(store).get("fast_tick_sec") == "4.0"
    assert RuntimeSettings(store).get("auto_scan_chats") == "false"


def test_settings_post_rejects_invalid_and_keeps_original(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("fast_tick_sec", "3.0")
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"fast_tick_sec": "-1"}})
    assert r.status_code == 400
    j = r.json()
    assert "field" in j and j["field"] == "fast_tick_sec"
    assert RuntimeSettings(store).get("fast_tick_sec") == "3.0"  # 原值未变 (原子)


def test_settings_post_rejects_unknown_key(tmp_data):
    client = TestClient(create_app())
    r = client.post("/api/settings", json={"values": {"nope_key": "1"}})
    assert r.status_code == 400
    assert r.json()["field"] == "nope_key"


def test_settings_reset_restores_default(tmp_data):
    store = SqliteStore()
    RuntimeSettings(store).set("slow_tick_sec", "99")
    client = TestClient(create_app())
    r = client.post("/api/settings/reset", json={"key": "slow_tick_sec"})
    assert r.status_code == 200
    assert r.json()["defaults"]["slow_tick_sec"] == 30.0
    assert "slow_tick_sec" not in RuntimeSettings(store).all()


def test_settings_boundary_validation(tmp_data):
    client = TestClient(create_app())
    cases = [
        {"auto_scan_max_chats": "0"},      # <1
        {"auto_scan_max_chats": "1001"},   # >1000
        {"auto_scan_max_chats": "1.5"},    # 非整数
        {"auto_scan_settle_sec": "0.05"},  # <0.1
        {"auto_scan_settle_sec": "31"},    # >30
        {"auto_scan_chats": "yes"},        # 非布尔
        {"fast_tick_sec": "abc"},          # 非数值
    ]
    for v in cases:
        r = client.post("/api/settings", json={"values": v})
        assert r.status_code == 400, v


def test_settings_page_renders(tmp_data):
    html = TestClient(create_app()).get("/settings").text
    assert 'id="settings-form"' in html
    assert "fast_tick_sec" in html and "auto_scan_chats" in html
    assert 'href="/settings"' in html and "nav-ico" in html  # 导航含设置入口 (SVG 图标)
