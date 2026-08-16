# tests/reply/test_generator.py
from app.reply.generator import generate_reply, regenerate_reply
from app.rag.pipeline import RagPipeline
from app.rag.reranker import FakeReranker
from app.storage.sqlite_store import SqliteStore
from app.storage.chroma_store import ChromaStore
from app.llm.interfaces import LLM

class FakeLLM(LLM):
    def generate(self, s, u, max_tokens=1024):
        return "建议回复: 感谢询价, LED灯报价$5/个"

def fake_embed(text): return [1.0]*8

def test_generate_reply_returns_reply_and_sources(tmp_data):
    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), FakeLLM())
    r = generate_reply(pipe, "cust1", "c1", "LED灯多少钱?")
    assert "LED" in r["reply"]
    assert isinstance(r["sources"], list)


def test_generate_reply_style_passed_to_system(tmp_data):
    """reply-assist: style 参数应进入 system 提示词, 决定候选表达风格。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    generate_reply(pipe, "cust1", "c1", "hi", style="concise")
    assert "简洁" in seen["system"]


def test_regenerate_produces_different_style(tmp_data):
    """reply-assist: 重新生成切换风格, 获得不同候选。"""
    styles = []

    class TrackingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            styles.append(s)
            return f"回复 #{len(styles)}"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), TrackingLLM())
    r1 = generate_reply(pipe, "cust1", "c1", "hi")
    assert r1["style"] == "default"
    r2 = regenerate_reply(pipe, "cust1", "c1", "hi", previous_style=r1["style"])
    assert r2["style"] != r1["style"]
    assert styles[1] != styles[0]  # 第二次提示词不同


def test_generate_reply_includes_session_history(tmp_data):
    """3.4/3.6: 会话历史作为额外 system 上下文传入 LLM。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    history = [{"role": "user", "content": "LED价格?"},
               {"role": "assistant", "content": "报价$5"}]
    generate_reply(pipe, "cust1", "c1", "何时到货?", history=history)
    assert "本次会话最近对话历史" in seen["system"]
    assert "LED价格" in seen["system"]
    assert "报价$5" in seen["system"]


def test_generate_reply_includes_recent_chat(tmp_data):
    """reply-context: 最近聊天记录注入 system, 让 AI 结合完整对话而非只盯最后一句话。"""
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    generate_reply(pipe, "cust1", "c1", "要500个", recent_chat="客户: 要500个\n我: 5美元一个")
    assert "最近聊天记录" in seen["system"]
    assert "要500个" in seen["system"]
    assert "5美元一个" in seen["system"]


def _capture(store, language="zh", scenario="auto", formality="casual", style="default"):
    seen = {}

    class CapturingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen["system"] = s
            return "回复"

    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), CapturingLLM())
    generate_reply(pipe, "cust1", "c1", "hi", style=style,
                   language=language, scenario=scenario, formality=formality)
    return seen["system"]


def test_language_instruction_in_system(tmp_data):
    """multilingual-copy: language 指令进入 system 提示词。"""
    store = SqliteStore()
    sys_ru = _capture(store, language="ru")
    assert "俄语" in sys_ru
    sys_en = _capture(store, language="en")
    assert "英语" in sys_en


def test_unknown_language_falls_back_to_chinese(tmp_data):
    """multilingual-reply-generation: 未知语种回退简体中文, 输出语言仍受约束。"""
    store = SqliteStore()
    sys_fr = _capture(store, language="fr")
    assert "用简体中文回复" in sys_fr


def test_scenario_instruction_in_system(tmp_data):
    """multilingual-copy: 手动指定场景时按场景指令生成。"""
    store = SqliteStore()
    sys_bargain = _capture(store, scenario="bargain")
    assert "让步空间" in sys_bargain


def test_auto_scenario_detection_instruction(tmp_data):
    """multilingual-copy: scenario=auto 时提示词含场景识别指令。"""
    store = SqliteStore()
    sys_auto = _capture(store, scenario="auto")
    assert "所属业务场景" in sys_auto


def test_formality_instruction_in_system(tmp_data):
    """multilingual-copy: formal 语气指令进入提示词。"""
    store = SqliteStore()
    sys_formal = _capture(store, formality="formal")
    assert "正式" in sys_formal


def test_terms_in_system(tmp_data):
    """multilingual-copy: 汽车外贸术语进入提示词。"""
    store = SqliteStore()
    assert "VIN" in _capture(store)


def test_default_params_backward_compatible(tmp_data):
    """D1: 缺省参数 (zh/auto/casual) 与现状等价, 不含多余维度指令。"""
    store = SqliteStore()
    sys_default = _capture(store)
    assert "俄语" not in sys_default
    assert "英语" not in sys_default
    assert "正式" not in sys_default


def test_regenerate_preserves_dimensions(tmp_data):
    """D3: regenerate 保留语种/场景/语气, 仅切换风格。"""
    seen = []

    class TrackingLLM(LLM):
        def generate(self, s, u, max_tokens=1024):
            seen.append(s)
            return "回复"

    store = SqliteStore()
    vs = ChromaStore(embedding_fn=fake_embed)
    pipe = RagPipeline(store, vs, FakeReranker(), TrackingLLM())
    r1 = generate_reply(pipe, "cust1", "c1", "hi", style="default",
                        language="ru", scenario="payment", formality="formal")
    r2 = regenerate_reply(pipe, "cust1", "c1", "hi", previous_style=r1["style"],
                          language="ru", scenario="payment", formality="formal")
    assert r2["style"] != r1["style"]
    assert r2["language"] == "ru" and r2["scenario"] == "payment" and r2["formality"] == "formal"
    assert "俄语" in seen[1] and "交易安全" in seen[1] and "正式" in seen[1]
