from app.storage.sqlite_store import SqliteStore
from app.storage.interfaces import Message
import time

def test_duplicate_ingest_no_dup(tmp_data):
    store = SqliteStore()
    m = Message("m1","a1","c1",False,"x",1,"chat","hi",True,int(time.time()))
    for _ in range(5):
        store.upsert_message(m)
    assert len(store.list_messages("c1")) == 1
