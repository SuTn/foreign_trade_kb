from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Chat:
    id: str; account_id: str; jid: str; display_name: str | None
    kind: str | None; last_synced_at: int

@dataclass
class Message:
    id: str; account_id: str; chat_id: str; from_me: bool
    sender_jid: str | None; ts: int; type: str | None
    body: str | None; body_present: bool; ingested_at: int
    sender_name: str | None = None

@dataclass
class ProfileField:
    customer_id: str; field: str; value: str; source: str; updated_at: int

@dataclass
class WikiPage:
    id: str; title: str; slug: str; body_md: str; frontmatter: dict
    source_doc_ids: list[str]; entity_type: str | None; updated_at: int

class StructuredStore(ABC):
    @abstractmethod
    def upsert_chat(self, chat: Chat) -> None: ...
    @abstractmethod
    def upsert_message(self, msg: Message) -> None: ...
    @abstractmethod
    def upsert_profile_field(self, customer_id: str, field: str, value: str, source: str) -> None: ...
    @abstractmethod
    def get_profile(self, customer_id: str) -> list[ProfileField]: ...
    @abstractmethod
    def list_messages(self, chat_id: str, limit: int = 50, before_ts: int | None = None) -> list[Message]: ...
    @abstractmethod
    def search_fts(self, table: str, query: str, limit: int = 20) -> list[dict]: ...
    @abstractmethod
    def upsert_wiki_page(self, page: WikiPage) -> None: ...
    @abstractmethod
    def get_wiki_page(self, slug: str) -> WikiPage | None: ...
    @abstractmethod
    def list_documents(self) -> list[dict]: ...
    @abstractmethod
    def delete_document(self, doc_id: str) -> bool: ...

class VectorStore(ABC):
    @abstractmethod
    def upsert_message_vector(self, key: str, text: str, metadata: dict) -> None: ...
    @abstractmethod
    def upsert_chunks(self, chunks: list[dict]) -> None: ...  # {id, text, metadata}
    @abstractmethod
    def query_messages(self, text: str, chat_id: str | None, top_k: int = 5) -> list[dict]: ...
    @abstractmethod
    def query_chunks(self, text: str, top_k: int = 5) -> list[dict]: ...
    @abstractmethod
    def delete_chunks(self, doc_id: str) -> None: ...
    @abstractmethod
    def delete_message_vectors(self, chat_id: str) -> None: ...
