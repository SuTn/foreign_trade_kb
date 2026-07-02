from app.collector.merger import merge_messages

def test_merge_idb_with_dom_body():
    idb = [{"id": "m1", "chatId": "c1", "fromMe": False, "from": "x", "timestamp": 1000, "type": "chat"}]
    dom = [{"message_id": "m1", "body": "hello", "sender": "x", "ts": 1000}]
    merged = merge_messages(idb, dom)
    assert merged[0]["body"] == "hello"
    assert merged[0]["body_present"] is True

def test_merge_missing_dom_body():
    idb = [{"id": "m2", "chatId": "c1", "fromMe": True, "from": None, "timestamp": 2000, "type": "chat"}]
    merged = merge_messages(idb, [])
    assert merged[0]["body"] is None
    assert merged[0]["body_present"] is False
