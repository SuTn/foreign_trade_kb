# app/web/worker.py
"""reply_tasks / tiering_tasks / summary_tasks 常驻 worker (D1/D7)。

worker 以 app 引用持有 app.state 共享资源 (chroma_store/reranker/llm),
使用独立 SQLite 连接 (routes._build_store 每次新建连接), 任务间复用。
双线程并发 (worker 并发优化): 回复线程只消费 reply_tasks (最高优先级, 永不阻塞);
后台线程消费 tiering_tasks / summary_tasks (低优先级)。各线程独立 SQLite 连接,
WAL + busy_timeout 兜底并发写。
"""
import json
import logging
import time

from fastapi import FastAPI

from app.rag.pipeline import RagPipeline
from app.reply.generator import generate_reply
from app.web.routes import _build_store, _get_chroma_store, get_reranker, CloudLLM

POLL_INTERVAL_SEC = 1.0  # 空循环 sleep (Design Doc Open Question 已定 1s)
REPLY_STUCK_TIMEOUT_SEC = 180  # 回复生成超时兜底: 超过则标记 failed, 避免前端永久「正在生成…」
WATCHDOG_INTERVAL_SEC = 10
log = logging.getLogger("reply.worker")


def _resources(app: FastAPI, store):
    """worker 线程访问共享资源的统一入口 (审计 H: 不依赖 Request)。"""
    chroma = _get_chroma_store(app)
    reranker = getattr(app.state, "reranker", None) or get_reranker()
    llm = getattr(app.state, "llm", None) or CloudLLM()
    return RagPipeline(store, chroma, reranker, llm)


def _recent_chat_context(store, chat_id: str, limit: int = 15) -> str | None:
    """取最近 N 条聊天记录拼成文本 (我: / 客户:), 供 AI 结合完整对话上下文。
    失败静默返回 None (不阻塞回复生成)。"""
    try:
        msgs = store.list_messages(chat_id, limit=limit)  # ts DESC
        msgs = sorted(msgs, key=lambda m: m.ts)           # 转 ASC 时间序
        lines = []
        for m in msgs:
            if not m.body:
                continue
            lines.append(f"{'我' if m.from_me else '客户'}: {m.body}")
        return "\n".join(lines) or None
    except Exception:
        return None


def _execute_reply_task(app: FastAPI, store, task: dict) -> None:
    """串行执行单个回复任务: running → 生成 → done/failed。
    mode=generate 追加 user+assistant 到会话; mode=regenerate 只读历史不追加 (D4)。"""
    task_id = task["id"]
    try:
        store.update_reply_task(task_id, status="running")
        pipe = _resources(app, store)
        history = store.get_session_history(task["session_id"]) if task["session_id"] else []
        recent_chat = _recent_chat_context(store, task["chat_id"])
        result = generate_reply(pipe, task["customer_id"], task["chat_id"], task["message"],
                                style=task["style"],
                                language=task.get("language") or "zh",
                                scenario=task.get("scenario") or "auto",
                                formality=task.get("formality") or "casual",
                                history=history, recent_chat=recent_chat)
        if task["mode"] == "generate":
            store.append_session_message(task["session_id"], "user", task["message"])
            store.append_session_message(task["session_id"], "assistant", result["reply"])
        store.update_reply_task(task_id, status="done",
                                result=json.dumps({**result, "session_id": task["session_id"]},
                                                  ensure_ascii=False))
    except Exception as e:
        log.warning("reply task %s 失败: %s", task_id, e)
        store.update_reply_task(task_id, status="failed", error=str(e)[:300])


def _execute_tiering_task(app: FastAPI, store, task: dict) -> None:
    """后台线程执行单个分层任务: running → 逐客户分层 → done/failed。
    回复由独立回复线程消费, 不再在此检查回复优先 (worker 并发优化)。"""
    task_id = task["id"]
    try:
        store.update_tiering_task(task_id, status="running")
        from app.profile.tiering import tier_customers
        llm = getattr(app.state, "llm", None) or CloudLLM()
        customer_ids = task["customer_ids"]
        tiered = 0
        untiered = 0
        for i, cid in enumerate(customer_ids, start=1):
            r = tier_customers(store, llm, [cid])
            tiered += r["tiered"]
            untiered += r["untiered"]
            store.update_tiering_task(task_id, progress=i)
        store.update_tiering_task(
            task_id, status="done",
            result=json.dumps({"tiered": tiered, "untiered": untiered}, ensure_ascii=False))
    except Exception as e:
        log.warning("tiering task %s 失败: %s", task_id, e)
        store.update_tiering_task(task_id, status="failed", error=str(e)[:300])


def _execute_summary_task(app: FastAPI, store, task: dict) -> None:
    """后台线程执行单个摘要任务: running → 生成/增量更新摘要 → done/failed。
    结果写入 customer_summaries 表, task.result 存 JSON 摘要供轮询展示。"""
    task_id = task["id"]
    try:
        store.update_summary_task(task_id, status="running")
        from app.profile.summarizer import summarize_customer
        llm = getattr(app.state, "llm", None) or CloudLLM()
        result = summarize_customer(store, llm, task["customer_id"])
        store.update_summary_task(task_id, status="done",
                                  result=json.dumps(result, ensure_ascii=False))
    except Exception as e:
        log.warning("summary task %s 失败: %s", task_id, e)
        store.update_summary_task(task_id, status="failed", error=str(e)[:300])


def _reply_loop(app: FastAPI) -> None:
    """回复线程: 只消费 reply_tasks (最高优先级, 永不阻塞)。独立 SQLite 连接。"""
    store = _build_store()
    while True:
        try:
            task = store.next_pending_reply_task()
            if task is not None:
                _execute_reply_task(app, store, task)
                continue
            time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            log.exception("回复 worker 循环异常")
            time.sleep(POLL_INTERVAL_SEC)


def _background_loop(app: FastAPI) -> None:
    """后台线程: 消费 tiering_tasks / summary_tasks (低优先级)。独立 SQLite 连接。"""
    store = _build_store()
    while True:
        try:
            tier_task = store.next_pending_tiering_task()
            if tier_task is not None:
                _execute_tiering_task(app, store, tier_task)
                continue
            summary_task = store.next_pending_summary_task()
            if summary_task is not None:
                _execute_summary_task(app, store, summary_task)
                continue
            time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            log.exception("后台 worker 循环异常")
            time.sleep(POLL_INTERVAL_SEC)


def _reply_watchdog(app: FastAPI) -> None:
    """看门狗线程: 独立于回复线程, 周期性把卡死的 running 回复任务标记 failed。

    回复线程串行执行 _execute_reply_task, 若其中模型加载/LLM 调用卡死,
    回复线程本身会被阻塞而无法自愈; 本线程用独立 SQLite 连接兜底, 让前端
    轮询能拿到 failed 结果并退出「正在生成回复…」, 而非永久等待。
    """
    store = _build_store()
    while True:
        try:
            n = store.mark_stuck_reply_tasks_failed(REPLY_STUCK_TIMEOUT_SEC)
            if n:
                log.warning("回复看门狗: %d 个 running 任务超时已标记 failed", n)
        except Exception:
            log.exception("回复看门狗循环异常")
        time.sleep(WATCHDOG_INTERVAL_SEC)


def start_worker(app: FastAPI) -> None:
    """启动双线程 worker: 回复线程 + 后台线程 (分层/摘要)。
    回复永不阻塞于分层/摘要; 各线程独立 SQLite 连接 (WAL + busy_timeout 兜底并发写)。
    另起看门狗线程兜底「回复生成卡死」, 保证前端轮询终态可达。"""
    import threading
    threading.Thread(target=_reply_loop, args=(app,), daemon=True).start()
    threading.Thread(target=_background_loop, args=(app,), daemon=True).start()
    threading.Thread(target=_reply_watchdog, args=(app,), daemon=True).start()
