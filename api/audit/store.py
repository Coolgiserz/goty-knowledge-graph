"""请求审计存储：SQLAlchemy ORM 抽象，后端无关（异步原生）。

设计要点：
- 使用 SQLAlchemy 的 **asyncio 扩展**（`create_async_engine` + `AsyncSession`），
  与 FastAPI 原生 async 对齐：审计写入是真正的 ``await``，不占用线程、不阻塞事件循环
  （对比「同步 SQLAlchemy + asyncio.to_thread」的线程桥接，原生异步路径更轻、吞吐更高，
  正是 FastAPI 官方 async 文档推荐的「有异步库就用 async def + await」做法）。
- 默认用 SQLite 打通流程（自动规范为 ``sqlite+aiosqlite://``）。
- 未来接 MySQL / PostgreSQL / OLAP 只需改 ``GOTY_AUDIT_DB_URL`` 为对应**异步驱动** URL
  （如 ``mysql+aiomysql://...``、``postgresql+asyncpg://...``、``clickhouse+asynch://...``）；
  ORM 模型与读写接口不变——这就是预留的「换数据库」接口。``_to_async_url`` 会把常见的
  同步驱动（sqlite / mysql+pymysql / postgresql+psycopg2）自动改写为异步驱动，因此旧配置
  也能直接复用。
- 模型与具体数据库无关：SQLite / MySQL / OLAP 的 DSN 差异由引擎层处理，模型不动。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import AnomalyEvent, AuditLog, Base

# 同步驱动 -> 异步驱动的归一化映射；命中后自动改写，旧配置无需改。
_SYNC_TO_ASYNC = {
    "sqlite": "sqlite+aiosqlite",
    "mysql": "mysql+aiomysql",
    "mysql+pymysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "postgresql+psycopg2": "postgresql+asyncpg",
}


def _to_async_url(url: str) -> str:
    """把常见同步 SQLAlchemy URL 改写为异步驱动 URL；已是异步则原样返回。"""
    lowered = url.lower()
    for drv in ("+aiosqlite", "+aiomysql", "+asyncmy", "+asyncpg"):
        if drv in lowered:
            return url
    for sync_drv, async_drv in _SYNC_TO_ASYNC.items():
        prefix = sync_drv + "://"
        if lowered.startswith(prefix):
            return url.replace(prefix, async_drv + "://", 1)
    return url


class AuditStore:
    """审计存储门面：对调用方屏蔽具体数据库（异步原生）。

    用法::

        store = AuditStore("sqlite:///./data/audit.db")  # 自动规范为 sqlite+aiosqlite
        store.init()                             # 建表（幂等，同步入口；app 构造期调用）
        await store.record_audit({...})          # 由中间件直接 await 调用
        await store.record_anomaly({...})
    """

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        async_url = _to_async_url(url)
        self.async_url = async_url
        try:
            db_path = make_url(async_url).database
            if db_path and db_path != ":memory:":
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self.engine = create_async_engine(async_url, echo=echo, future=True)
        self._session_factory = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )
        self._schema_ready = False

    async def _create_all(self) -> None:
        """在运行中的事件循环里幂等建表，并标记已就绪。"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    def init(self) -> None:
        """建表（幂等）。同步入口，供 app 构造/导入期（无运行中的事件循环）调用。

        - 无运行中的事件循环时：``asyncio.run`` 一次性建表（生产导入、同步测试）。
        - 已在事件循环中（如 async 测试内调用 ``create_app``）：跳过同步建表，
          改由首次写入经 :meth:`_ensure_schema` 在运行中的循环里惰性建表，避免
          ``asyncio.run() cannot be called from a running event loop`` 的 RuntimeError。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._create_all())
        # 已有运行中的事件循环：留给 _ensure_schema 在首次 await 时建表

    async def _ensure_schema(self) -> None:
        """惰性建表：仅在尚未建过时执行一次（幂等），在运行中的事件循环内安全调用。"""
        if self._schema_ready:
            return
        await self._create_all()

    async def record_audit(self, data: dict[str, Any]) -> None:
        """写入一条审计记录（异步原生，调用方直接 await）。"""
        await self._ensure_schema()
        async with self._session() as s:
            s.add(self._audit_from_dict(data))
            await s.commit()

    async def record_anomaly(self, data: dict[str, Any]) -> None:
        """写入一条异常事件（异步原生）。"""
        await self._ensure_schema()
        async with self._session() as s:
            s.add(AnomalyEvent(**data))
            await s.commit()

    async def count_audit(self, client_ip: str | None = None) -> int:
        """统计审计记录数（可选按 IP 过滤），用于运维/测试核对。"""
        await self._ensure_schema()
        async with self._session() as s:
            stmt = select(func.count()).select_from(AuditLog)
            if client_ip:
                stmt = stmt.where(AuditLog.client_ip == client_ip)
            return int(await s.scalar(stmt) or 0)

    async def count_anomalies(self, client_ip: str | None = None) -> int:
        await self._ensure_schema()
        async with self._session() as s:
            stmt = select(func.count()).select_from(AnomalyEvent)
            if client_ip:
                stmt = stmt.where(AnomalyEvent.client_ip == client_ip)
            return int(await s.scalar(stmt) or 0)

    # ---- 内部 ----

    @staticmethod
    def _audit_from_dict(data: dict[str, Any]) -> AuditLog:
        allowed = {c.name for c in AuditLog.__table__.columns}
        return AuditLog(**{k: v for k, v in data.items() if k in allowed})

    @asynccontextmanager
    async def _session(self) -> Any:
        s: AsyncSession = self._session_factory()
        try:
            yield s
        finally:
            await s.close()
