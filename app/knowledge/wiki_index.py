# app/knowledge/wiki_index.py
"""Wiki 索引: 实体级, 两阶段全局去重。
阶段1: 每文档 LLM 抽取实体候选
阶段2: 嵌入聚类初筛 + LLM 精判去重
阶段3: 生成 Markdown 页面 (wikilinks + frontmatter)"""
import re, time, uuid
from app.knowledge.index_strategy import IndexStrategy
from app.llm.interfaces import LLM, Embedding
from app.storage.interfaces import StructuredStore, WikiPage
from app.config import settings

EXTRACT_PROMPT = """从以下外贸资料中抽取关键实体/概念, 输出 JSON 数组, 每项 {{name, type, summary}}。
资料: {text}"""

class WikiIndex(IndexStrategy):
    def __init__(self, store: StructuredStore, llm: LLM, embedding: Embedding):
        self.store = store
        self.llm = llm
        self.embedding = embedding

    def index(self, doc_id: str, text: str) -> None:
        candidates = self._extract_entities(doc_id, text)
        merged = self._global_dedup(candidates)
        for ent in merged:
            self._upsert_page(ent, doc_id)

    def _extract_entities(self, doc_id, text) -> list[dict]:
        import json
        resp = self.llm.generate("你是外贸知识抽取助手", EXTRACT_PROMPT.format(text=text[:3000]))
        try:
            ents = json.loads(resp)
            for e in ents: e["source_doc"] = doc_id
            return ents
        except Exception:
            return []

    def _global_dedup(self, candidates: list[dict]) -> list[dict]:
        """嵌入聚类初筛 + LLM 精判。"""
        if not candidates: return []
        # 初筛: 向量化摘要, 余弦相似度超阈值归为候选对
        existing = self._load_existing_entities()
        all_ents = existing + candidates
        vecs = [self.embedding.embed(e["summary"] or e["name"]) for e in all_ents]
        merged_idx = set()
        result = []
        for i, e in enumerate(all_ents):
            if i in merged_idx: continue
            cluster = [e]
            for j in range(i+1, len(all_ents)):
                if j in merged_idx: continue
                if self._cosine(vecs[i], vecs[j]) > settings.wiki_dedup_threshold:
                    # LLM 精判是否同义
                    if self._llm_synonym(e, all_ents[j]):
                        cluster.append(all_ents[j]); merged_idx.add(j)
            merged_idx.add(i)
            merged = self._merge_cluster(cluster)
            result.append(merged)
        return result

    def _llm_synonym(self, a, b) -> bool:
        resp = self.llm.generate("判断两实体是否同义, 只回 true/false",
                                 f"A={a['name']}({a['summary']}) B={b['name']}({b['summary']})")
        return resp.strip().lower().startswith("true")

    def _merge_cluster(self, cluster: list[dict]) -> dict:
        """合并同义簇: 保留首个实体, 合并 source_doc。冲突 → 保守不合并 (保留首个)。"""
        base = dict(cluster[0])
        docs = {base.get("source_doc")}
        for m in cluster[1:]:
            docs.add(m.get("source_doc"))
        base["source_doc"] = "existing" if "existing" in docs else next(iter(docs - {"existing"}))
        return base

    def _upsert_page(self, ent, doc_id):
        slug = self._slug(ent["name"])
        existing = self.store.get_wiki_page(slug)
        source_docs = existing.source_doc_ids + [doc_id] if existing else [doc_id]
        # 增量更新: 已有 manual 编辑不被覆盖 (source=manual 标记)
        body = self._build_body(ent, existing)
        page = WikiPage(
            id=existing.id if existing else str(uuid.uuid4()),
            title=ent["name"], slug=slug, body_md=body,
            frontmatter={"source_docs": source_docs, "entity_type": ent.get("type"), "updated": int(time.time())},
            source_doc_ids=source_docs, entity_type=ent.get("type"), updated_at=int(time.time()))
        self.store.upsert_wiki_page(page)

    def _build_body(self, ent, existing) -> str:
        # 正文中引用其他实体用 [[slug]]
        body = ent.get("summary", "")
        if existing and existing.frontmatter.get("manual_edited"):
            return existing.body_md  # 不覆盖人工编辑
        return body

    def _slug(self, name) -> str:
        return re.sub(r"[^\w一-龥]+", "-", name.strip().lower()).strip("-")

    def _load_existing_entities(self) -> list[dict]:
        rows = self.store.conn.execute("SELECT title, slug, body_md, entity_type FROM wiki_pages").fetchall()
        return [{"name": r["title"], "summary": r["body_md"], "type": r["entity_type"], "source_doc": "existing"} for r in rows]

    @staticmethod
    def _cosine(a, b) -> float:
        import math
        dot = sum(x*y for x, y in zip(a, b))
        na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
        return dot/(na*nb) if na and nb else 0.0
