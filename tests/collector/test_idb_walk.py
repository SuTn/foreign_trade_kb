# tests/collector/test_idb_walk.py
from app.collector import idb_walk


class FakeCDP:
    def __init__(self, rows_by_store):
        self.rows_by_store = rows_by_store
        self.expressions = []

    async def eval_async_readonly(self, expression):
        self.expressions.append(expression)
        store = None
        for key in ("message", "chat", "contact"):
            if f"'{key}'" in expression:
                store = key
                break
        return self.rows_by_store.get(store, [])


async def test_walk_idb_builds_chats_contacts_messages(monkeypatch):
    monkeypatch.setattr(idb_walk.settings, "idb_stores", ["message", "chat", "contact"])
    cdp = FakeCDP({
        "message": [
            {"id": "false_8615976909619@c.us_3EB06C1E7DA73250B3B4", "t": 1710000000,
             "from": "8615976909619@c.us", "to": "8618963126542@c.us", "type": "chat", "fromMe": False},
        ],
        "chat": [{"id": "8615976909619@c.us", "name": "Sonya"}],
        "contact": [{"id": "100245207838777@lid", "name": "Kakajan"}],
    })
    data = await idb_walk.walk_idb(cdp, "me")
    assert data["messages"][0]["id"].endswith("3EB06C1E7DA73250B3B4")
    assert data["chats"] == {"8615976909619@c.us": "Sonya"}
    assert data["contacts"] == {"100245207838777@lid": "Kakajan"}


def test_read_store_js_is_readonly():
    js = idb_walk._read_store_js("message")
    # 只读: readonly 事务, 无写操作
    assert "readonly" in js
    assert "readwrite" not in js
    assert "delete" not in js and "put(" not in js and "add(" not in js
