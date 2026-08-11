from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # 路径
    data_dir: Path = Path("data")
    sqlite_path: Path = Path("data/kb.db")
    chroma_dir: Path = Path("data/chroma")
    user_data_dir: Path = Path("data/user-data-dir")
    status_path: Path = Path("data/status.json")
    vault_export_dir: Path = Path("data/vault")
    avatars_dir: Path = Path("data/avatars")

    # WhatsApp 采集
    whatsapp_url: str = "https://web.whatsapp.com"
    fast_tick_sec: float = 2.0
    fast_tick_jitter: float = 0.5
    slow_tick_sec: float = 30.0
    slow_tick_jitter: float = 5.0
    # 自动扫描全部会话 (逐会话打开读取正文; 注意会把未读消息标记为已读)
    auto_scan_chats: bool = True
    auto_scan_interval_sec: float = 600.0
    auto_scan_max_chats: int = 100
    auto_scan_settle_sec: float = 1.5
    idb_database: str = "model-storage"
    idb_stores: list[str] = ["message", "chat", "contact", "group-metadata"]
    max_records_per_store: int = 20000
    heartbeat_timeout_sec: float = 30.0

    # DOM 选择器 (集中配置, 便于 WhatsApp Web 变更时修补)
    dom_message_row_selector: str = "[data-id]"
    dom_conversation_header_selector: str = 'header[data-testid="conversation-header"]'
    # 媒体消息行 testid 前缀白名单 (可配置; 未知 testid 保持忽略)
    dom_media_row_prefixes: list[str] = ["image-album-", "image-", "video-", "ptt-",
                                         "document-", "audio-", "location-"]

    # LLM / Embedding
    llm_provider: str = "anthropic"  # anthropic | openai (openai 走 OpenAI 兼容接口)
    llm_model: str = "claude-sonnet-4-6"
    llm_api_base: str | None = None  # OpenAI 兼容接口 base URL; None=官方端点
    llm_api_key: str | None = None   # None 时回退 OPENAI_API_KEY / ANTHROPIC_API_KEY
    embedding_provider: str = "local"  # local (bge-m3 本地) | openai (OpenAI 兼容接口)
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_base: str | None = None  # 嵌入接口 base URL, 可与 LLM 分开配置
    embedding_api_key: str | None = None   # None 时回退 OPENAI_API_KEY
    embedding_dim: int = 1024  # 嵌入维度 (bge-m3=1024; openai text-embedding-3-small=1536 等)
    reranker_provider: str = "local"  # local (FlagEmbedding 本地) | ollama (OpenAI 兼容接口)
    reranker_api_base: str | None = None  # ollama provider 生效, 如 http://localhost:11434/v1
    reranker_model: str = "BAAI/bge-reranker-v2-m3"

    # 客户画像/分析
    profile_summary_messages: int = 30  # 画像抽取/客户分析所用的近期消息数

    # RAG
    chunk_size: int = 512
    chunk_overlap: int = 64
    rerank_top_k: int = 8
    context_token_limit: int = 4000
    wiki_dedup_threshold: float = 0.85

    model_config = SettingsConfigDict(
        env_prefix="KB_",
        env_file=".env",
        extra="ignore",
    )

settings = Settings()