import time
from app.storage.sqlite_store import SqliteStore
from app.storage.runtime_settings import RuntimeSettings
from app.config import settings


def test_get_returns_default_when_unset(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    assert rt.get("fast_tick_sec") == settings.fast_tick_sec
    assert rt.get("auto_scan_chats") == settings.auto_scan_chats


def test_set_get_roundtrip_stores_string(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "3.5")
    assert rt.get("fast_tick_sec") == "3.5"   # DB 存字符串
    assert rt.all() == {"fast_tick_sec": "3.5"}  # 只含显式配置项


def test_reset_deletes_row_restores_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("slow_tick_sec", "99")
    rt.reset("slow_tick_sec")
    assert rt.get("slow_tick_sec") == settings.slow_tick_sec
    assert "slow_tick_sec" not in rt.all()


def test_refresh_reloads_db_values(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "4.0")
    rt.refresh()  # 模拟主循环新一轮
    assert rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == 4.0


def test_get_typed_converts_by_default_type(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("auto_scan_max_chats", "250")
    assert rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats) == 250
    rt.set("auto_scan_chats", "false")
    assert rt.get_typed("auto_scan_chats", settings.auto_scan_chats) is False


def test_get_typed_dirty_value_falls_back_to_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "abc")   # 脏数据
    assert rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == settings.fast_tick_sec
    rt.set("auto_scan_max_chats", "oops")
    assert rt.get_typed("auto_scan_max_chats", settings.auto_scan_max_chats) == settings.auto_scan_max_chats


def test_get_typed_unset_returns_default(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    assert rt.get_typed("slow_tick_sec", settings.slow_tick_sec) == settings.slow_tick_sec


def test_get_typed_nan_infinity_falls_back(tmp_data):
    rt = RuntimeSettings(SqliteStore())
    rt.set("fast_tick_sec", "NaN")
    assert rt.get_typed("fast_tick_sec", settings.fast_tick_sec) == settings.fast_tick_sec
    rt.set("slow_tick_sec", "inf")
    assert rt.get_typed("slow_tick_sec", settings.slow_tick_sec) == settings.slow_tick_sec
