"""请求审计存储：SQLAlchemy ORM 抽象，后端无关，**同时提供同步与异步两套接口**。

设计要点：
- 模块同时导出 ``SyncAuditStore``（同步 ``Session``）与 ``AuditStore``（异步 ``AsyncSession``），
  二者共享同一套 ORM 模型（``AuditLog`` / ``AnomalyEvent``），仅底层引擎/会话不同。
  这与 SQLAlchemy 自身的 ``Session`` / ``AsyncSession`` 一致：**支持异步不代表删掉同步接口**，
  调用方按运行环境选型——FastAPI 中间件走异步 ``await``，运维脚本 / CLI / 同步测试走同步。
- 异步接口用 ``create_async_engine`` + ``AsyncSession``，写入是真正的 ``await``，不占用线程、
  不阻塞事件循环（对齐 FastAPI 官方「有异步库就用 async def + await」）。
- 同步接口用 ``create_engine`` + ``Session``，纯同步，无需事件循环，适合脚本/管理命令。
- 默认用 SQLite 打通流程：异步自动规范为 ``sqlite+aiosqlite://``，同步用 ``sqlite://``。
  ``_to_async_url`` / ``_to_sync_url`` 会在两套驱动 URL 间互转，旧配置无需改。
- 未来接 MySQL / PostgreSQL / OLAP 只需改 ``GOTY_AUDIT_DB_URL`` 为对应驱动 URL；
  ORM 模型与读写接口不变——这就是预留的「换数据库」接口。
- 工厂 :func:`create_audit_store` 按 ``async_`` 标志返回对应实现，调用方无感知。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import AnomalyEvent, AuditLog, Base

# 同步驱动 -> 异步驱动 的归一化映射；命中后自动改写，旧配置无需改。
_SYNC_TO_ASYNC = {
    "sqlite": "sqlite+aiosqlite",
    "mysql": "mysql+aiomysql",
    "mysql+pymysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "postgresql+psycopg2": "postgresql+asyncpg",
}

# 异步驱动 -> 同步驱动 的反向映射（供同步接口把 async URL 规范回 sync）。
_ASYNC_TO_SYNC = {
    "sqlite+aiosqlite": "sqlite",
    "mysql+aiomysql": "mysql+pymysql",
    "mysql+asyncmy": "mysql+pymysql",
    "postgresql+asyncpg": "postgresql+psycopg2",
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


def _to_sync_url(url: str) -> str:
    """把常见异步 SQLAlchemy URL 规范回同步驱动 URL；已是同步则原样返回。"""
    lowered = url.lower()
    for async_drv, sync_drv in _ASYNC_TO_SYNC.items():
        if async_drv in lowered:
            return url.replace(async_drv, sync_drv, 1)
    # 形如 postgresql+asyncpg:// 已被上面的精确映射覆盖；兜底：去掉未知 +driver 后缀
    return url


def _ensure_db_dir(url: str) -> None:
    """确保数据库文件所在目录存在（仅文件型 DSN 有意义）。"""
    try:
        db_path = make_url(url).database
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _audit_from_dict(data: dict[str, Any]) -> AuditLog:
    """从审计字典构造 ``AuditLog`` 行，忽略模型不存在的字段。"""
    allowed = {c.name for c in AuditLog.__table__.columns}
    return AuditLog(**{k: v for k, v in data.items() if k in allowed})


# ---------------------------------------------------------------------------
# 异步接口：AuditStore（FastAPI 中间件使用）
# ---------------------------------------------------------------------------


class AuditStore:
    """审计存储门面（**异步**接口）：对调用方屏蔽具体数据库，写入经 ``await`` 落库。

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
        _ensure_db_dir(async_url)
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
            s.add(_audit_from_dict(data))
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

    @asynccontextmanager
    async def _session(self) -> Any:
        s: AsyncSession = self._session_factory()
        try:
            yield s
        finally:
            await s.close()


# ---------------------------------------------------------------------------
# 同步接口：SyncAuditStore（运维脚本 / CLI / 同步测试使用）
# ---------------------------------------------------------------------------


class SyncAuditStore:
    """审计存储门面（**同步**接口）：与 ``AuditStore`` 共享同一套 ORM 模型，但底层为同步
    ``Session``，无需事件循环，适合脚本、管理命令与非异步上下文。

    用法::

        store = SyncAuditStore("sqlite:///./data/audit.db")  # 同步驱动，无需 aiosqlite 事件循环
        store.init()                          # 建表（幂等，纯同步）
        store.record_audit({...})             # 直接调用，无 await
        store.record_anomaly({...})
        n = store.count_audit("1.2.3.4")
    """

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        sync_url = _to_sync_url(url)
        self.sync_url = sync_url
        _ensure_db_dir(sync_url)
        self.engine = create_engine(sync_url, echo=echo, future=True)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )
        self._schema_ready = False

    def init(self) -> None:
        """建表（幂等，纯同步）。"""
        with self.engine.begin() as conn:
            Base.metadata.create_all(conn)
        self._schema_ready = True

    def _ensure_schema(self) -> None:
        """惰性建表：仅在尚未建过时执行一次（幂等）。"""
        if self._schema_ready:
            return
        self.init()

    def record_audit(self, data: dict[str, Any]) -> None:
        """写入一条审计记录（同步）。"""
        self._ensure_schema()
        with self._session() as s:
            s.add(_audit_from_dict(data))
            s.commit()

    def record_anomaly(self, data: dict[str, Any]) -> None:
        """写入一条异常事件（同步）。"""
        self._ensure_schema()
        with self._session() as s:
            s.add(AnomalyEvent(**data))
            s.commit()

    def count_audit(self, client_ip: str | None = None) -> int:
        """统计审计记录数（可选按 IP 过滤）。"""
        self._ensure_schema()
        with self._session() as s:
            stmt = select(func.count()).select_from(AuditLog)
            if client_ip:
                stmt = stmt.where(AuditLog.client_ip == client_ip)
            return int(s.scalar(stmt) or 0)

    def count_anomalies(self, client_ip: str | None = None) -> int:
        self._ensure_schema()
        with self._session() as s:
            stmt = select(func.count()).select_from(AnomalyEvent)
            if client_ip:
                stmt = stmt.where(AnomalyEvent.client_ip == client_ip)
            return int(s.scalar(stmt) or 0)

    @contextmanager
    def _session(self) -> Any:
        s: Session = self._session_factory()
        try:
            yield s
        finally:
            s.close()


# ---------------------------------------------------------------------------
# 工厂：按 async_ 标志返回对应实现
# ---------------------------------------------------------------------------


def create_audit_store(
    url: str, *, echo: bool = False, async_: bool = True
) -> AuditStore | SyncAuditStore:
    """构造审计存储：``async_=True`` 返回 :class:`AuditStore`（异步），否则返回
    :class:`SyncAuditStore`（同步）。模块据此同时暴露「同步 + 异步」两套接口，调用方按
    运行环境选型（FastAPI 中间件用异步，运维脚本用同步）。"""
    return AuditStore(url, echo=echo) if async_ else SyncAuditStore(url, echo=echo)
