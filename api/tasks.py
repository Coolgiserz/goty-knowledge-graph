"""后台任务管理器：把耗时的探索计算放到有界线程池异步执行，请求立即返回任务 ID。

设计目标（云端 demo）：
- 不阻塞用户：``POST /api/jobs`` 立刻返回 ``job_id``（状态 pending），计算在后台线程跑；
  前端轮询 ``GET /api/jobs/{id}`` 拿结果。
- 背压：线程池并发数有上限（默认 2），超出排队的任务处于 pending，天然限制同时运行的重计算。
- 待处理上限：单 owner 的未完成任务数超过阈值即拒绝（429），防止队列被刷爆。
- 归属与隔离：每个任务记 owner；普通用户只能看自己的任务，持令牌者可用 ``?scope=all`` 看全部。
- 轻量：纯标准库 + 进程内内存存储，无数据库、无额外依赖；重启即清空（demo 足够）。
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor


class Task:
    """单个计算任务的状态机：pending → running → done / failed / canceled。"""

    def __init__(self, tid: str, board: str, params: dict, owner: str):
        self.id = tid
        self.board = board
        self.params = params
        self.owner = owner
        self.status = "pending"
        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None
        self.result = None
        self.error = None

    def to_dict(self, include_result: bool = False, position: int | None = None) -> dict:
        d = {
            "id": self.id,
            "board": self.board,
            "status": self.status,
            "owner": self.owner,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if position is not None:
            d["queue_position"] = position
        if include_result and self.status in ("done", "failed"):
            d["result"] = self.result
            d["error"] = self.error
        return d


class TaskManager:
    def __init__(self, max_workers: int = 2, max_pending_per_owner: int = 5):
        self._lock = threading.Lock()
        self._tasks: dict = {}
        self.max_workers = max(1, max_workers)
        self._exec = ThreadPoolExecutor(max_workers=self.max_workers)
        self.max_pending_per_owner = max(1, max_pending_per_owner)

    # ---- 写入态操作（加锁） ----
    def create(self, board: str, params: dict, owner: str) -> Task:
        with self._lock:
            pending = sum(
                1
                for t in self._tasks.values()
                if t.owner == owner and t.status in ("pending", "running")
            )
            if pending >= self.max_pending_per_owner:
                raise TooManyPending(self.max_pending_per_owner)
            tid = uuid.uuid4().hex[:8]
            t = Task(tid, board, params, owner)
            self._tasks[tid] = t
        self._exec.submit(self._run, t)
        return t

    def _run(self, t: Task):
        # 延迟导入，避免与 app 的循环依赖
        from .graph_loader import data_matches_baseline
        from .registry import run_board

        try:
            t.status = "running"
            t.started_at = time.time()
            t.result = run_board(t.board, t.params, data_matches_baseline())
            t.status = "done"
        except Exception as e:  # 捕获一切，避免线程静默崩溃
            t.error = str(e)
            t.status = "failed"
        finally:
            t.finished_at = time.time()

    def cancel(self, tid: str) -> bool:
        with self._lock:
            t = self._tasks.get(tid)
            if t and t.status == "pending":
                t.status = "canceled"
                return True
        return False

    # ---- 读取态操作 ----
    def get(self, tid: str):
        with self._lock:
            return self._tasks.get(tid)

    def list(self, owner: str | None = None, all_scope: bool = False):
        with self._lock:
            items = list(self._tasks.values())
        if not all_scope:
            items = [t for t in items if t.owner == owner]
        # 最近创建在前
        items.sort(key=lambda t: t.created_at, reverse=True)
        return items

    def queue_position(self, tid: str):
        """返回某任务在并发队列中的 1-based 位次；非排队态（已运行/完成等）返回 None。

        语义：位次 = 比它更早创建、且仍处于 pending/running（仍在占用或等待算力）的
        任务数量 + 1。即「前面还有多少任务在算或在等」，反映真正被服务的前后顺序。
        """
        with self._lock:
            t = self._tasks.get(tid)
            if not t or t.status != "pending":
                return None
            earlier = [
                o
                for o in self._tasks.values()
                if o.created_at < t.created_at and o.status in ("pending", "running")
            ]
            return len(earlier) + 1

    def queue_stats(self) -> dict:
        """全局队列快照：运行中 / 等待中 / 并发上限，供前端展示整体负荷。"""
        with self._lock:
            running = sum(1 for t in self._tasks.values() if t.status == "running")
            waiting = sum(1 for t in self._tasks.values() if t.status == "pending")
        return {
            "running": running,
            "waiting": waiting,
            "max_workers": self.max_workers,
        }


class TooManyPending(Exception):
    def __init__(self, limit: int):
        super().__init__(f"待处理任务过多（上限 {limit}），请等待现有任务完成后再提交。")
        self.limit = limit
