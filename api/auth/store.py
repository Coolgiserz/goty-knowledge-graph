"""用户 / 会话存储：SQLAlchemy ORM 抽象，后端无关，**同时提供同步与异步两套接口**。

与 :mod:`api.audit.store` 对称：``UserStore``（异步，供 FastAPI 中间件/路由 ``await`` 调用）
与 ``SyncUserStore``（同步，供脚本 / CLI）共享同一套 ORM 模型（``User`` / ``SessionRow``），
仅底层引擎/会话不同。未来接 MySQL / PostgreSQL 只需改 ``GOTY_USERS_DB_URL``。

会话 Cookie 仅持有随机会话 id，校验在数据库完成；``create_session`` / ``get_session_user`` /
``delete_session`` 支持登录态的建立、解析与吊销。
"""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import AuthBase, SessionRow, User
from .password import hash_password, verify_password

# 同步驱动 -> 异步驱动 的归一化映射（与审计库一致）。
_SYNC_TO_ASYNC = {
    "sqlite": "sqlite+aiosqlite",
    "mysql": "mysql+aiomysql",
    "mysql+pymysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "postgresql+psycopg2": "postgresql+asyncpg",
}
_ASYNC_TO_SYNC = {
    "sqlite+aiosqlite": "sqlite",
    "mysql+aiomysql": "mysql+pymysql",
    "postgresql+asyncpg": "postgresql+psycopg2",
}


def _to_async_url(url: str) -> str:
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
    lowered = url.lower()
    for async_drv, sync_drv in _ASYNC_TO_SYNC.items():
        if async_drv in lowered:
            return url.replace(async_drv, sync_drv, 1)
    return url


def _with_busy_timeout(url: str) -> str:
    """文件型 SQLite 追加 ``busy_timeout=30s``，降低并发写 ``database is locked``。"""
    try:
        parsed = make_url(url)
    except Exception:
        return url
    if parsed.drivername.split("+")[0] != "sqlite":
        return url
    db = parsed.database
    if not db or db == ":memory:":
        return url
    if "timeout" in (parsed.query or {}):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}timeout=30"


def _ensure_db_dir(url: str) -> None:
    try:
        db_path = make_url(url).database
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _set_sync_sqlite_pragmas(dbapi_conn, _conn_record) -> None:
    try:
        cur = dbapi_conn.cursor()
        if hasattr(cur, "__await__"):  # aiosqlite 连接：cursor() 是协程，跳过
            return
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
    except Exception:
        pass


def _new_session_id() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# 异步接口：UserStore（FastAPI 路由 / 中间件使用）
# ---------------------------------------------------------------------------


class UserStore:
    """用户/会话存储门面（**异步**接口）。"""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        async_url = _with_busy_timeout(_to_async_url(url))
        self.async_url = async_url
        _ensure_db_dir(async_url)
        self.engine = create_async_engine(async_url, echo=echo, future=True)
        self._session_factory = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def _create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(AuthBase.metadata.create_all)
        self._schema_ready = True

    def init(self) -> None:
        """建表（幂等）。无运行中的事件循环时同步建；否则留给首次写入惰性建。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._create_all())

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await self._create_all()

    async def register(self, username: str, password: str, email: str = "") -> User:
        """注册新用户；用户名已存在抛 ``ValueError("username_taken")``。"""
        await self._ensure_schema()
        async with self._session() as s:
            if await s.scalar(select(User).where(User.username == username)):
                raise ValueError("username_taken")
            u = User(
                username=username,
                email=email or "",
                password_hash=hash_password(password),
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u

    async def authenticate(self, username: str, password: str) -> User | None:
        """校验凭据；用户不存在 / 停用 / 密码错误均返回 ``None``。"""
        await self._ensure_schema()
        async with self._session() as s:
            u = await s.scalar(select(User).where(User.username == username))
            if u is None or not u.is_active:
                return None
            if not verify_password(password, u.password_hash):
                return None
            return u

    async def get_user(self, user_id: int) -> User | None:
        await self._ensure_schema()
        async with self._session() as s:
            return await s.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        await self._ensure_schema()
        async with self._session() as s:
            return await s.scalar(select(User).where(User.username == username))

    async def list_users(self) -> list[User]:
        await self._ensure_schema()
        async with self._session() as s:
            return list(await s.scalars(select(User).order_by(User.id)))

    async def create_session(self, user_id: int, ip: str, ttl: int) -> str:
        """建立会话，返回会话 id（写入 Cookie）。"""
        await self._ensure_schema()
        sid = _new_session_id()
        expires = datetime.now(UTC) + timedelta(seconds=ttl)
        async with self._session() as s:
            s.add(SessionRow(id=sid, user_id=user_id, ip=ip or "", expires_at=expires))
            await s.commit()
        return sid

    async def get_session_user(self, session_id: str | None) -> User | None:
        """按会话 id 解析用户；缺失 / 过期 / 用户停用均返回 ``None``（过期则顺手清理）。"""
        await self._ensure_schema()
        if not session_id:
            return None
        async with self._session() as s:
            row = await s.get(SessionRow, session_id)
            if row is None:
                return None
            # SQLite 读回的 DateTime(timezone=True) 为 naive（UTC），Postgres 为 aware；
            # 统一按 UTC 归一化后再比较，跨驱动稳健。
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < datetime.now(UTC):
                await s.delete(row)
                await s.commit()
                return None
            u = await s.get(User, row.user_id)
            if u is None or not u.is_active:
                return None
            return u

    async def delete_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        await self._ensure_schema()
        async with self._session() as s:
            row = await s.get(SessionRow, session_id)
            if row is not None:
                await s.delete(row)
                await s.commit()

    @asynccontextmanager
    async def _session(self) -> Any:
        s: AsyncSession = self._session_factory()
        try:
            yield s
        finally:
            await s.close()


# ---------------------------------------------------------------------------
# 同步接口：SyncUserStore（脚本 / CLI 使用）
# ---------------------------------------------------------------------------


class SyncUserStore:
    """用户/会话存储门面（**同步**接口）。"""

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        sync_url = _with_busy_timeout(_to_sync_url(url))
        self.sync_url = sync_url
        _ensure_db_dir(sync_url)
        self.engine = create_engine(sync_url, echo=echo, future=True)
        event.listen(self.engine, "connect", _set_sync_sqlite_pragmas)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self._schema_ready = False

    def init(self) -> None:
        with self.engine.begin() as conn:
            AuthBase.metadata.create_all(conn)
        self._schema_ready = True

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.init()

    def register(self, username: str, password: str, email: str = "") -> User:
        with self._session() as s:
            if s.scalar(select(User).where(User.username == username)):
                raise ValueError("username_taken")
            u = User(username=username, email=email or "", password_hash=hash_password(password))
            s.add(u)
            s.commit()
            s.refresh(u)
            return u

    def authenticate(self, username: str, password: str) -> User | None:
        with self._session() as s:
            u = s.scalar(select(User).where(User.username == username))
            if u is None or not u.is_active:
                return None
            if not verify_password(password, u.password_hash):
                return None
            return u

    def get_user(self, user_id: int) -> User | None:
        with self._session() as s:
            return s.get(User, user_id)

    def create_session(self, user_id: int, ip: str, ttl: int) -> str:
        sid = _new_session_id()
        expires = datetime.now(UTC) + timedelta(seconds=ttl)
        with self._session() as s:
            s.add(SessionRow(id=sid, user_id=user_id, ip=ip or "", expires_at=expires))
            s.commit()
        return sid

    def get_session_user(self, session_id: str | None) -> User | None:
        if not session_id:
            return None
        with self._session() as s:
            row = s.get(SessionRow, session_id)
            if row is None:
                return None
            # SQLite 读回的 DateTime(timezone=True) 为 naive（UTC），Postgres 为 aware；
            # 统一按 UTC 归一化后再比较，跨驱动稳健。
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < datetime.now(UTC):
                s.delete(row)
                s.commit()
                return None
            u = s.get(User, row.user_id)
            if u is None or not u.is_active:
                return None
            return u

    def delete_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._session() as s:
            row = s.get(SessionRow, session_id)
            if row is not None:
                s.delete(row)
                s.commit()

    @contextmanager
    def _session(self) -> Any:
        s: Session = self._session_factory()
        try:
            yield s
        finally:
            s.close()
