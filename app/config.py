from pathlib import Path
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # 路径
    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/kb.db")
    chroma_dir: Path = Path("data/chroma")
    user_data_dir: Path = Path("data/user-data-dir")
    status_path: Path = Path("data/status.json")
    vault_export_dir: Path = Path("data/vault")

    # WhatsApp 采集
    whatsapp_url: str = "https://web.whatsapp.com"
    fast_tick_sec: float = 2.0
    fast_tick_jitter: float = 0.5
    slow_tick_sec: float = 30.0
    slow_tick_jitter: float = 5.0
    idb_database: str = "model-storage"
    idb_stores: list[str] = ["message", "chat", "contact", "group-metadata"]
    max_records_per_store: int = 20000
    heartbeat_timeout_sec: float = 30.0

    # DOM 选择器 (集中配置, 便于 WhatsApp Web 变更时修补)
    dom_message_row_selector: str = "[data-id]"
    dom_conversation_header_selector: str = 'header[data-testid="conversation-header"]'

    # LLM / Embedding
    llm_provider: str = "anthropic"  # anthropic | openai
    llm_model: str = "claude-sonnet-4-6"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # RAG
    chunk_size: int = 512
    chunk_overlap: int = 64
    rerank_top_k: int = 8
    context_token_limit: int = 4000
    wiki_dedup_threshold: float = 0.85

    class Config:
        env_prefix = "KB_"
        env_file = ".env"

settings = Settings()