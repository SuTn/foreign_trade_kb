# app/storage/runtime_settings.py
import time
from app.config import settings


class RuntimeSettings:
    """settings 表 (key-value) 读写层。复用调用方 SqliteStore 连接。
    DEFAULTS 以 .env 默认值为准；DB 只存用户显式配置项，未配置项 get 回退默认。"""

    DEFAULTS = {
        "fast_tick_sec": settings.fast_tick_sec,
        "slow_tick_sec": settings.slow_tick_sec,
        "auto_scan_interval_sec": settings.auto_scan_interval_sec,
        "auto_scan_max_chats": settings.auto_scan_max_chats,
        "auto_scan_settle_sec": settings.auto_scan_settle_sec,
        "auto_scan_chats": settings.auto_scan_chats,
        "send_enabled": False,
    }

    def __init__(self, store):
        self.store = store
        self._cache = {}  # refresh() 后生效

    def refresh(self):
        """主循环每轮调用: 一次 SELECT 拉全量 DB 值入缓存。"""
        rows = self.store.conn.execute("SELECT key, value FROM settings").fetchall()
        self._cache = {r["key"]: r["value"] for r in rows}

    def get(self, key, default=None):
        """DB 值 (字符串)；无行返回 default；default 为 None 时用 DEFAULTS。"""
        if key in self._cache:
            return self._cache[key]
        row = self.store.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return default if default is not None else self.DEFAULTS.get(key)

    def set(self, key, value):
        """UPSERT；value 一律存字符串。"""
        self.store.conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, str(value), int(time.time())))
        self.store.conn.commit()
        self._cache[key] = str(value)

    def reset(self, key):
        """删除该行，恢复 .env 默认。"""
        self.store.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self.store.conn.commit()
        self._cache.pop(key, None)

    def all(self):
        """DB 全部键值（不含默认，供合并展示）。"""
        rows = self.store.conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_typed(self, key, default):
        """按 DEFAULTS 类型转换；解析失败回退 default (单条脏数据不搞崩采集器)。"""
        raw = self.get(key, None)
        if raw is None:
            return default
        default_val = self.DEFAULTS.get(key, default)
        try:
            if isinstance(default_val, bool):
                s = str(raw).strip().lower()
                if s in ("1", "true", "yes", "on"):
                    return True
                if s in ("0", "false", "no", "off"):
                    return False
                raise ValueError(raw)
            if isinstance(default_val, int):
                return int(raw)
            v = float(raw)
            if v != v or v in (float("inf"), float("-inf")):  # NaN / Infinity 视为脏数据
                raise ValueError(raw)
            return v
        except (TypeError, ValueError):
            return default
