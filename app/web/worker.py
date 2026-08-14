# app/web/worker.py
"""reply_tasks 常驻串行 worker (D1/D7)。

worker 以 app 引用持有 app.state 共享资源 (chroma_store/reranker/llm),
使用独立 SQLite 连接 (routes._build_store 每次新建连接), 任务间复用。
串行消费保证一次只有一个 LLM 调用。
"""
import json
import logging
import time

from fastapi import FastAPI

from app.rag.pipeline import RagPipeline
from app.reply.generator import generate_reply
from app.web.routes import _build_store, _get_chroma_store, get_reranker, CloudLLM

POLL_INTERVAL_SEC = 1.0  # 空循环 sleep (Design Doc Open Question 已定 1s)
log = logging.getLogger("reply.worker")


def _resources(app: FastAPI, store):
    """worker 线程访问共享资源的统一入口 (审计 H: 不依赖 Request)。"""
    chroma = _get_chroma_store(app)
    reranker = getattr(app.state, "reranker", None) or get_reranker()
    llm = getattr(app.state, "llm", None) or CloudLLM()
    return RagPipeline(store, chroma, reranker, llm)


def _execute_reply_task(app: FastAPI, store, task: dict) -> None:
    """串行执行单个回复任务: running → 生成 → done/failed。
    mode=generate 追加 user+assistant 到会话; mode=regenerate 只读历史不追加 (D4)。"""
    task_id = task["id"]
    try:
        store.update_reply_task(task_id, status="running")
        pipe = _resources(app, store)
        history = store.get_session_history(task["session_id"]) if task["session_id"] else []
        result = generate_reply(pipe, task["customer_id"], task["chat_id"], task["message"],
                                style=task["style"], history=history)
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
    """串行执行单个分层任务: running → 逐客户分层 → done/failed。
    每处理一个客户前检查 pending reply_tasks, 有则先消费回复 (回复优先, D7)。"""
    task_id = task["id"]
    try:
        store.update_tiering_task(task_id, status="running")
        from app.profile.tiering import tier_customers
        llm = getattr(app.state, "llm", None) or CloudLLM()
        customer_ids = task["customer_ids"]
        total = len(customer_ids)
        for i, cid in enumerate(customer_ids, start=1):
            reply = store.next_pending_reply_task()
            if reply is not None:
                _execute_reply_task(app, store, reply)
            tier_customers(store, llm, [cid])
            store.update_tiering_task(task_id, progress=i)
        store.update_tiering_task(task_id, status="done",
                                  result=json.dumps({"tiered": total}, ensure_ascii=False))
    except Exception as e:
        log.warning("tiering task %s 失败: %s", task_id, e)
        store.update_tiering_task(task_id, status="failed", error=str(e)[:300])


def worker_loop(app: FastAPI) -> None:
    """常驻循环: 串行消费 reply_tasks 与 tiering_tasks (回复优先); 空循环 sleep 1s。"""
    store = _build_store()
    while True:
        try:
            task = store.next_pending_reply_task()
            if task is not None:
                _execute_reply_task(app, store, task)
                continue
            tier_task = store.next_pending_tiering_task()
            if tier_task is not None:
                _execute_tiering_task(app, store, tier_task)
                continue
            time.sleep(POLL_INTERVAL_SEC)
        except Exception:
            log.exception("worker 循环异常")
            time.sleep(POLL_INTERVAL_SEC)
