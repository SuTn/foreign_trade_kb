# tests/collector/test_idb_walk.py
from app.collector import idb_walk


class FakeCDP:
    def __init__(self, rows_by_store):
        self.rows_by_store = rows_by_store
        self.expressions = []

    async def eval_async_readonly(self, expression):
        self.expressions.append(expression)
        store = None
        for key in ("message", "chat", "contact", "group-metadata"):
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
        "contact": [
            {"id": "100245207838777@lid", "name": "Kakajan", "phone": "123456789"},
            {"id": "447974905044@c.us", "name": "Lucas", "lid": "106558658740375@lid"},
        ],
    })
    data = await idb_walk.walk_idb(cdp, "me")
    assert data["messages"][0]["id"].endswith("3EB06C1E7DA73250B3B4")
    assert data["chats"] == {"8615976909619@c.us": "Sonya"}
    assert data["contacts"] == {
        "100245207838777@lid": "Kakajan",
        "447974905044@c.us": "Lucas",
        "106558658740375@lid": "Lucas",
        "123456789": "Kakajan",
    }
    assert data["lid_to_phone"] == {"106558658740375@lid": "447974905044@c.us"}
    assert data["lids"]["106558658740375@lid"]["name"] == "Lucas"
    assert data["phone_by_lid"] == {"100245207838777@lid": "123456789"}


def test_read_store_js_is_readonly():
    js = idb_walk._read_store_js("message")
    # 只读: readonly 事务, 无写操作
    assert "readonly" in js
    assert "readwrite" not in js
    assert "delete" not in js and "put(" not in js and "add(" not in js


def test_read_store_js_extracts_fromme_from_msgkey():
    """fromMe 的真实来源是消息 id/msgKey 序列化串 'true_<jid>_<hex>' 前缀,
    顶层 m.fromMe 与 m.id.fromMe (MsgKey 对象) 在多数消息里不存在。
    漏取会导致自己发出的消息被误判为入站 (归属错误)。"""
    js = idb_walk._read_store_js("message")
    assert "objMe(idv)" in js        # 兼容 MsgKey 对象形态 (x.fromMe === true)
    assert "m.fromMe === true" in js   # 兼容旧格式顶层 fromMe
    assert "indexOf('true_') === 0" in js  # 序列化串前缀 true_ (权威来源)


def test_read_store_js_extracts_chatjid():
    """chatJid: 会话 JID 从 id/msgKey/key 序列化串 'true_/false_<jid>_<hex>' 提取 <jid>,
    或对象形态 .remote。这是消息归因会话的权威来源, 不依赖「当前打开哪个会话」。"""
    js = idb_walk._read_store_js("message")
    assert "chatJid" in js
    assert "jidFromStr" in js     # 字符串形态解析
    assert "remoteFrom" in js     # 对象形态 .remote 兜底


async def test_walk_idb_reads_group_metadata(monkeypatch):
    monkeypatch.setattr(idb_walk.settings, "idb_stores",
                        ["message", "chat", "contact", "group-metadata"])
    cdp = FakeCDP({
        "group-metadata": [
            {"id": "120363123456789@g.us", "name": "海外采购群",
             "members": [{"jid": "8615976909619@c.us", "name": "Sonya"},
                         {"jid": "8616111222333@c.us", "name": None}]},
        ],
    })
    data = await idb_walk.walk_idb(cdp, "me")
    g = data["groups"]["120363123456789@g.us"]
    assert g["name"] == "海外采购群"
    assert g["members"] == {"8615976909619@c.us": "Sonya", "8616111222333@c.us": None}


async def test_walk_idb_group_metadata_missing_silently_empty(monkeypatch):
    """store 缺失 (null) → groups 空字典, 不抛异常。"""
    monkeypatch.setattr(idb_walk.settings, "idb_stores",
                        ["message", "chat", "contact", "group-metadata"])
    cdp = FakeCDP({"group-metadata": None})
    data = await idb_walk.walk_idb(cdp, "me")
    assert data["groups"] == {}


def test_read_store_js_has_limit():
    from app.config import settings

    js = idb_walk._read_store_js("message")
    assert "openCursor" in js or "limit" in js
    assert str(settings.max_records_per_store) in js
