# app/web/model_settings.py
"""模型配置读写: 在页面上配置 LLM/Embedding/Reranker 的 provider/model/api_base/api_key。

配置持久化到 .env (与 app/config.py 的 Settings 一致), 保存后:
  1. 更新 app.config.settings 对象 (即时生效)
  2. 清除进程级缓存 (app.state.llm/embedding/reranker/chroma_store), 下次调用重建
"""
import os
import logging
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# 可配置的模型字段 (key -> (env 变量名, 是否敏感))
MODEL_FIELDS = {
    "llm_provider":   ("KB_LLM_PROVIDER", False),
    "llm_model":      ("KB_LLM_MODEL", False),
    "llm_api_base":   ("KB_LLM_API_BASE", False),
    "llm_api_key":    ("KB_LLM_API_KEY", True),
    "embedding_provider": ("KB_EMBEDDING_PROVIDER", False),
    "embedding_model":    ("KB_EMBEDDING_MODEL", False),
    "embedding_api_base": ("KB_EMBEDDING_API_BASE", False),
    "embedding_api_key":  ("KB_EMBEDDING_API_KEY", True),
    "embedding_dim":      ("KB_EMBEDDING_DIM", False),
    "reranker_provider":  ("KB_RERANKER_PROVIDER", False),
    "reranker_model":     ("KB_RERANKER_MODEL", False),
    "reranker_api_base":  ("KB_RERANKER_API_BASE", False),
    "reranker_api_key":   ("KB_RERANKER_API_KEY", True),
}

# 敏感字段 (返回给前端时打码)
SENSITIVE = {"llm_api_key", "embedding_api_key", "reranker_api_key"}


def env_path() -> Path:
    """定位 .env 文件: 优先当前工作目录 (launcher 已 chdir 到 base), 否则项目根。"""
    cwd = Path.cwd() / ".env"
    if cwd.exists():
        return cwd
    # 尝试 launcher base_dir
    try:
        from launcher import paths
        p = paths.base_dir() / ".env"
        if p.exists():
            return p
    except Exception:
        pass
    return cwd


def _mask(value: str) -> str:
    """打码敏感值: 保留前 4 后 4, 中间用 ***。"""
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}***{value[-4:]}"


def get_model_config() -> dict:
    """返回当前模型配置 (敏感字段打码)。"""
    out = {}
    for key, (env_name, is_sensitive) in MODEL_FIELDS.items():
        value = getattr(settings, key, None)
        if value is None:
            value = os.environ.get(env_name, "")
        out[key] = _mask(value) if is_sensitive else str(value or "")
    return out


def save_model_config(values: dict) -> dict:
    """保存模型配置: 写 .env + 更新 settings。values 为 {attr: value}。

    校验: embedding_dim 必须为合法整数 (1~4096); 其余字段按字符串保存。
    """
    # 0. 校验数值字段
    if "embedding_dim" in values:
        try:
            dim = int(values["embedding_dim"])
        except (TypeError, ValueError):
            raise ValueError("embedding_dim 必须为整数")
        if not 1 <= dim <= 4096:
            raise ValueError("embedding_dim 须在 1~4096 之间")
        values["embedding_dim"] = str(dim)

    # 1. 读取现有 .env
    path = env_path()
    lines = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    # 2. 更新或追加配置行
    env_map = {env: attr for attr, (env, _) in MODEL_FIELDS.items()}
    new_lines = []
    written = set()
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in env_map:
                attr = env_map[key]
                if attr in values:
                    new_lines.append(f"{key}={values[attr]}")
                    written.add(attr)
                    continue
        new_lines.append(line)
    # 追加未写入的新配置
    for attr, value in values.items():
        if attr in written or attr not in MODEL_FIELDS:
            continue
        env_name, _ = MODEL_FIELDS[attr]
        new_lines.append(f"{env_name}={value}")

    # 3. 原子写回 .env (先写临时文件再替换, 避免写入中断损坏)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    tmp.replace(path)

    # 4. 更新 settings 对象 (即时生效)
    for attr, value in values.items():
        if attr not in MODEL_FIELDS:
            continue
        try:
            if attr == "embedding_dim":
                setattr(settings, attr, int(value))
            else:
                setattr(settings, attr, value)
        except Exception as e:
            log.warning("更新 settings.%s 失败: %s", attr, e)

    return {"saved": True, "path": str(path)}


def clear_cached_instances(app) -> None:
    """清除进程级模型缓存, 让下次调用用新配置重建。

    注意: 不重置 embedding_ready 标记 —— 该标记表示"预热是否完成",
    与实例缓存无关; 重置为未 set 会导致 _get_chroma_store 等待 30s 超时。
    实例清除后, _embedding()/_get_chroma_store() 会惰性重建。
    """
    for attr in ("llm", "embedding", "reranker", "chroma_store"):
        if hasattr(app.state, attr):
            setattr(app.state, attr, None)


def test_connection(provider: str, api_key: str, api_base: str, model: str) -> tuple[bool, str]:
    """测试模型连接。provider: llm | embedding | reranker。"""
    try:
        if provider == "llm":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=api_base or None)
            resp = client.chat.completions.create(
                model=model, max_tokens=8,
                messages=[{"role": "user", "content": "hi"}])
            return bool(resp.choices), "连接成功"
        elif provider == "embedding":
            import openai
            client = openai.OpenAI(api_key=api_key, base_url=api_base or None)
            resp = client.embeddings.create(model=model, input="测试", dimensions=1024)
            return bool(resp.data and resp.data[0].embedding), "连接成功"
        elif provider == "reranker":
            import httpx
            resp = httpx.post(
                f"{api_base}/compatible-api/v1/reranks",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "documents": ["测试"], "query": "测试", "top_n": 1},
                timeout=30,
            )
            resp.raise_for_status()
            return True, "连接成功"
        return False, "未知 provider"
    except Exception as e:
        return False, str(e)[:200]