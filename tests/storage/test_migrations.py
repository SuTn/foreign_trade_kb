# tests/storage/test_migrations.py
import sqlite3
from pathlib import Path
from app.storage.sqlite_store import SqliteStore, MIGRATIONS


def _latest_version() -> int:
    return MIGRATIONS[-1][0]


def test_fresh_db_gets_latest_user_version(tmp_data):
    """新库: schema.sql 建全表, user_version 推进到最新。"""
    store = SqliteStore()
    v = store.conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == _latest_version()
    # 新库已含全部迁移列
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(customers)")}
    assert "avatar_path" in cols
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(reply_tasks)")}
    assert {"language", "scenario", "formality"} <= cols


def test_old_db_migrates_missing_columns(tmp_data):
    """旧库 (缺迁移列, user_version=0) 实例化后补齐列并推进版本。"""
    # 构造一个"旧库": 只建 customers 表 (无 avatar_path), user_version=0
    db = tmp_data / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE customers(id TEXT PRIMARY KEY, display_name TEXT)")
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()
    # 实例化 SqliteStore → 应补齐 avatar_path 并推进版本
    store = SqliteStore(path=db)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(customers)")}
    assert "avatar_path" in cols
    v = store.conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == _latest_version()


def test_migration_idempotent_on_reopen(tmp_data):
    """迁移幂等: 已迁移的库再次实例化不报错, 版本不变。"""
    store = SqliteStore()
    v1 = store.conn.execute("PRAGMA user_version").fetchone()[0]
    store.conn.close()
    # 重新打开同一库
    store2 = SqliteStore()
    v2 = store2.conn.execute("PRAGMA user_version").fetchone()[0]
    assert v2 == v1 == _latest_version()


def test_partial_migration_advances_version(tmp_data):
    """部分迁移: 旧库 user_version=3, 只应用 4..latest 的迁移。"""
    db = tmp_data / "partial.db"
    conn = sqlite3.connect(db)
    # 模拟迁移前的 reply_tasks (无 language/scenario/formality 列)
    conn.execute(
        "CREATE TABLE reply_tasks(id TEXT PRIMARY KEY, customer_id TEXT, chat_id TEXT, "
        "message TEXT, style TEXT, session_id TEXT, mode TEXT, status TEXT, result TEXT, "
        "error TEXT, created_at INTEGER, updated_at INTEGER)")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()
    store = SqliteStore(db)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(reply_tasks)")}
    assert {"language", "scenario", "formality"} <= cols  # 4/5/6 已应用
    v = store.conn.execute("PRAGMA user_version").fetchone()[0]
    assert v == _latest_version()