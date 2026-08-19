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
    # 默认关闭: 全量扫描会阻塞采集器、导致实时失效与心跳超时; 改为「未读列表监控 + 点谁同步谁」
    auto_scan_chats: bool = False
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
    llm_max_tokens: int = 2048  # 回复生成最大 token 数 (长回复不被截断; 短任务单独传更小值)
    embedding_provider: str = "openai"  # openai (OpenAI 兼容接口, 阿里云等)
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_api_base: str | None = None  # 嵌入接口 base URL, 可与 LLM 分开配置
    embedding_api_key: str | None = None   # None 时回退 OPENAI_API_KEY
    embedding_dim: int = 1024  # 嵌入维度 (qwen3.7-text-embedding 支持 2560/2048/1536/1024/768/512/256)
    reranker_provider: str = "aliyun"  # aliyun (阿里云 qwen3-rerank) | ollama (OpenAI 兼容接口)
    reranker_api_base: str | None = None  # 重排接口 base URL, 如 https://xxx.maas.aliyuncs.com
    reranker_api_key: str | None = None   # 重排 API Key (阿里云 DashScope Key)
    reranker_model: str = "qwen3-rerank"

    # 客户画像/分析
    profile_summary_messages: int = 30  # 画像抽取/客户分析所用的近期消息数

    # 客户分层 (customer-intent-tiering)
    tiering_active_days: int = 30   # 近期活跃客户默认天数
    tiering_max_customers: int = 50  # 单次分层任务客户数上限

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
