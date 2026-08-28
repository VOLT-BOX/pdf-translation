# -*- coding: utf-8 -*-
"""共享任务状态(单例)。

独立成模块,避免 app.py 作为 `__main__`(python app.py 启动)与作为 `app`(被
`from app import` 导入)双重加载时,各自生成不同的 tasks_store / _task_queue 实例。
本模块被任何一方导入都走同一份(Python 模块缓存),保证全局唯一。

worker 队列(单线程串行):混合件内部两引擎并行,但一次只处理一个 PDF 任务。
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from config import settings

logger = logging.getLogger("unified.service")


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ---------------- 任务存储 ----------------


class TaskStore:
    """进程内任务存储(对标 v3 store.py / RetainPDF _JOBS)。重启丢失。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(self, task: dict[str, Any]) -> None:
        with self._lock:
            self._tasks[task["task_id"]] = task

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._tasks.values())

    def update(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is not None:
                t.update(fields)

    def mark_running(self, task_id: str) -> None:
        self.update(task_id, status="running", started_at=_now_iso(), stage="running")

    def mark_done(self, task_id: str, result_files: list[str]) -> None:
        self.update(
            task_id,
            status="succeeded",
            finished_at=_now_iso(),
            progress=100.0,
            stage="succeeded",
            result_files=result_files,
        )

    def mark_failed(self, task_id: str, error: str, traceback: str | None = None) -> None:
        self.update(
            task_id,
            status="failed",
            finished_at=_now_iso(),
            stage="failed",
            error=error,
            traceback=traceback,
        )

    def public_view(self, task_id: str) -> dict[str, Any]:
        """对外视图:脱敏(不含 inputs/openai_api_key/traceback)。"""
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                return {}
        inputs: dict = t.get("inputs", {})
        return {
            "task_id": t["task_id"],
            "kind": t.get("kind", "pdf"),
            "status": t.get("status", "pending"),
            "original_filename": t.get("original_filename"),
            "created_at": t.get("created_at"),
            "started_at": t.get("started_at"),
            "finished_at": t.get("finished_at"),
            "progress": round(float(t.get("progress") or 0.0), 1),
            "stage": t.get("stage", ""),
            "decision": t.get("decision", ""),
            "sub_tasks": t.get("sub_tasks", {}),
            "result_files": t.get("result_files", []),
            "image_size": t.get("image_size"),
            "error": t.get("error"),
            # 回显创建任务时的关键输入参数(脱敏:不含 openai_api_key)
            "lang_in": inputs.get("lang_in", "en"),
            "lang_out": inputs.get("lang_out", "zh"),
            "concurrency": inputs.get("concurrency", 4),
            "custom_system_prompt": inputs.get("custom_system_prompt"),
            "glossary_hard": inputs.get("glossary_hard", False),
        }

    def delete(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.pop(task_id, None)
        if t is None:
            return False
        shutil.rmtree(t.get("work_dir", ""), ignore_errors=True)
        return True

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            ids = list(self._tasks.keys())
        for tid in ids:
            with self._lock:
                t = self._tasks.get(tid)
                if t is None:
                    continue
            status = t.get("status")
            if status in ("pending", "running"):
                continue
            finished = t.get("finished_at")
            if not finished:
                continue
            try:
                age = now - datetime.fromisoformat(finished).timestamp()
            except (ValueError, TypeError):
                continue
            if age >= settings.retention_seconds:
                self.delete(tid)
                removed += 1
        return removed

    def cleanup_orphan_dirs(self) -> int:
        root = Path(settings.work_root)
        if not root.exists():
            return 0
        with self._lock:
            known_ids = set(self._tasks.keys())
        removed = 0
        for d in root.iterdir():
            if d.is_dir() and d.name not in known_ids and d.name not in ("typst-package-cache", "typst-packages"):
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed


# 全局单例(本模块只导入一次,故只此一份)
tasks_store = TaskStore()

# worker 队列
_task_queue: "queue.Queue[str]" = queue.Queue()


def new_work_dir(task_id: str) -> Path:
    d = Path(settings.work_root) / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_task_id() -> str:
    return uuid.uuid4().hex


def _worker_loop() -> None:
    # lazy import 规避循环依赖(orchestrate 会反向 import store)
    from orchestrate import run_image_task, run_router_task

    while True:
        task_id = _task_queue.get()
        try:
            task = tasks_store.get(task_id)
            if task is not None and task.get("kind") == "image":
                run_image_task(task_id)
            else:
                run_router_task(task_id)
        except Exception:  # noqa: BLE001
            logger.exception(f"worker crashed on task {task_id}")
            try:
                tasks_store.mark_failed(task_id, error="worker crashed")
            except Exception:  # noqa: BLE001
                logger.exception(f"failed to mark {task_id} as failed after crash")
        finally:
            _task_queue.task_done()


def _cleanup_loop() -> None:
    interval = (
        max(60, min(settings.retention_seconds // 2, 600))
        if settings.retention_seconds > 0
        else 0
    )
    if interval <= 0:
        return
    while True:
        time.sleep(interval)
        try:
            n = tasks_store.cleanup_expired()
            if n:
                logger.info(f"cleanup_expired removed {n} task(s)")
        except Exception:  # noqa: BLE001
            logger.exception("cleanup loop error")


def start_background_workers() -> None:
    """启动 worker 线程与清理线程(daemon)。启动幂等,可重复调用。"""
    threading.Thread(target=_worker_loop, daemon=True).start()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
