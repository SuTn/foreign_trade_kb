# tests/llm/test_cloud_llm.py
import sys
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from app.llm.cloud_llm import CloudLLM


def _fake_openai_module(builds):
    class FakeCompletions:
        def create(self, model=None, max_tokens=None, messages=None, timeout=None):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeChat:
        @property
        def completions(self):
            return FakeCompletions()

    class FakeOpenAI:
        def __init__(self, *a, **k):
            builds.append("openai")
            self.chat = FakeChat()

    return SimpleNamespace(OpenAI=FakeOpenAI, chat=FakeChat)


def test_reuses_single_client(monkeypatch):
    """1.3: 多次 generate 复用同一 client 实例。"""
    builds = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(builds))
    llm = CloudLLM(provider="openai", api_key="test-key")
    assert llm.generate("s1", "u1") == "ok"
    assert llm.generate("s2", "u2") == "ok"
    assert len(builds) == 1


def test_concurrent_first_call_builds_once(monkeypatch):
    """1.3: 并发首次调用仅创建一个 client (threading.Lock)。"""
    builds = []
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(builds))
    llm = CloudLLM(provider="openai", api_key="test-key")
    barrier = threading.Barrier(8)

    def work(_):
        barrier.wait()
        return llm.generate("s", "u")

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(work, range(8)))
    assert results == ["ok"] * 8
    assert len(builds) == 1


def test_retries_transient_error_then_succeeds(monkeypatch):
    """llm-retry-timeout: 瞬时错误重试后成功。"""
    calls = {"n": 0}

    class FlakyCompletions:
        def create(self, model=None, max_tokens=None, messages=None, timeout=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("temporary")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeChat:
        @property
        def completions(self):
            return FlakyCompletions()

    class FakeOpenAI:
        def __init__(self, *a, **k):
            self.chat = FakeChat()

    monkeypatch.setitem(sys.modules, "openai",
                        SimpleNamespace(OpenAI=FakeOpenAI, chat=FakeChat))
    llm = CloudLLM(provider="openai", api_key="test-key", max_retries=3)
    assert llm.generate("s", "u") == "ok"
    assert calls["n"] == 3


def test_gives_up_after_max_retries(monkeypatch):
    """llm-retry-timeout: 持续失败时重试耗尽后抛出。"""
    calls = {"n": 0}

    class AlwaysFail:
        def create(self, model=None, max_tokens=None, messages=None, timeout=None):
            calls["n"] += 1
            raise RuntimeError("boom")

    class FakeClient:
        @property
        def completions(self):
            return AlwaysFail()

    class FakeOpenAI:
        def __init__(self, *a, **k):
            self.chat = FakeClient()

    monkeypatch.setitem(sys.modules, "openai",
                        SimpleNamespace(OpenAI=FakeOpenAI, chat=FakeClient))
    llm = CloudLLM(provider="openai", api_key="test-key", max_retries=3)
    try:
        llm.generate("s", "u")
        assert False, "应抛出异常"
    except RuntimeError:
        pass
    assert calls["n"] == 3
