# tests/knowledge/test_wiki_index.py
from app.knowledge.wiki_index import WikiIndex
from app.storage.sqlite_store import SqliteStore
from app.llm.interfaces import LLM, Embedding

class FakeLLM(LLM):
    def generate(self, system, user, max_tokens=1024):
        if "抽取" in user:
            return '[{"name":"LED灯","type":"product","summary":"LED照明产品"}]'
        return "false"  # 不同义

class FakeEmbed(Embedding):
    def embed(self, text): return [1.0, 0.0, 0.0]
    def dim(self): return 3

def test_wiki_index_creates_page(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f.pdf','pdf','p','done',1)")
    store.conn.commit()
    wi = WikiIndex(store, FakeLLM(), FakeEmbed())
    wi.index("d1", "LED灯是照明产品")
    page = store.get_wiki_page("led灯")
    assert page is not None
    assert page.title == "LED灯"

def test_wiki_dedup_merges_synonym(tmp_data):
    store = SqliteStore()
    store.conn.execute("INSERT INTO documents VALUES('d1','f','pdf','p','done',1)")
    store.conn.commit()
    # 两次抽取同名实体 → 合并为一个页面
    class MergeLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            return '[{"name":"LED灯","type":"product","summary":"LED"}]' if "抽取" in u else "true"
    wi = WikiIndex(store, MergeLLM(), FakeEmbed())
    wi.index("d1", "LED灯")
    wi.index("d1", "LED灯")
    rows = store.conn.execute("SELECT * FROM wiki_pages WHERE slug='led灯'").fetchall()
    assert len(rows) == 1
