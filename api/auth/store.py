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
from typing import Any, Protocol

from sqlalchemy import create_engine, delete, event, inspect, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import AuthBase, EmailToken, SessionRow, User
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


def _migrate_user_columns_sync(conn) -> None:
    """对存量 ``users`` 表增补 ``email_verified`` 列（兼容未迁移的旧库）。

    套路与审计库 ``_migrate_audit_columns_sync`` 一致：``inspect`` 探明已有列后按需 ALTER；
    SQLite 旧版本不支持 ``ADD COLUMN IF NOT EXISTS``，故显式判定。

    关键：存量标记只在本列「首次被加入」时执行一次——把所有**既有行**标记为已验证
    （受信任的历史账号），避免硬策略一刀切锁死老用户。若列已存在（已是迁移后的库），
    **不**再跑 UPDATE，否则重启会把「新注册但未验证」的用户也错误提升为已验证。
    """
    try:
        existing = {c["name"] for c in inspect(conn).get_columns("users")}
        if "email_verified" in existing:
            return  # 已是迁移后的库，无需（且不可再）批量提升
        conn.execute(text("ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0"))
        # 仅本次新加列时，把全部既有行标记为已验证（此后新注册账号以 False 起步）。
        conn.execute(text("UPDATE users SET email_verified=1"))
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
            await conn.run_sync(_migrate_user_columns_sync)
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

    async def register(
        self, username: str, password: str, email: str = "", email_verified: bool = False
    ) -> User:
        """注册新用户；用户名已存在抛 ``ValueError("username_taken")``。

        ``email_verified`` 默认 ``False``：自助注册的新账号需经邮件验证（硬策略下才能登录）。
        """
        await self._ensure_schema()
        async with self._session() as s:
            if await s.scalar(select(User).where(User.username == username)):
                raise ValueError("username_taken")
            u = User(
                username=username,
                email=email or "",
                password_hash=hash_password(password),
                email_verified=email_verified,
            )
            s.add(u)
            await s.commit()
            await s.refresh(u)
            return u

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱查用户（重发验证邮件时定位账号）；邮箱为空返回 ``None``。"""
        if not email:
            return None
        await self._ensure_schema()
        async with self._session() as s:
            return await s.scalar(select(User).where(User.email == email))

    async def mark_verified(self, user_id: int) -> None:
        """将指定用户标记为邮箱已验证（验证成功后调用）。"""
        await self._ensure_schema()
        async with self._session() as s:
            u = await s.get(User, user_id)
            if u is not None:
                u.email_verified = True
                await s.commit()

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
            _migrate_user_columns_sync(conn)
        self._schema_ready = True

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.init()

    def register(
        self, username: str, password: str, email: str = "", email_verified: bool = True
    ) -> User:
        """同步注册（脚本 / CLI 使用）。

        默认 ``email_verified=True``：CLI / 脚本建立的账号视为受信任、可直接登录；
        与自助注册（异步版默认 ``False``、需邮件验证）区分。
        """
        with self._session() as s:
            if s.scalar(select(User).where(User.username == username)):
                raise ValueError("username_taken")
            u = User(
                username=username,
                email=email or "",
                password_hash=hash_password(password),
                email_verified=email_verified,
            )
            s.add(u)
            s.commit()
            s.refresh(u)
            return u

    def get_by_email(self, email: str) -> User | None:
        if not email:
            return None
        with self._session() as s:
            return s.scalar(select(User).where(User.email == email))

    def mark_verified(self, user_id: int) -> None:
        with self._session() as s:
            u = s.get(User, user_id)
            if u is not None:
                u.email_verified = True
                s.commit()

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


# ---------------------------------------------------------------------------
# 邮箱验证令牌存储：可插拔（DB 表 / Redis），与 User.email_verified（持久）分离
# ---------------------------------------------------------------------------


class TokenStore(Protocol):
    """邮箱验证等临时令牌的存储抽象（部署驱动选择后端，见 :func:`create_token_store`）。

    令牌是**临时、单次、短命**数据，语义上 Redis 的 TTL 自动过期最贴合；但为保持零依赖
    本地可跑，默认走独立的 ``email_tokens`` 表（DB 后端）。两种后端共用此接口。
    """

    async def create(self, user_id: int, token: str, ttl: int) -> None:
        """写入令牌（覆盖该用户同类型旧令牌，使重发幂等）。"""
        ...

    async def consume(self, token: str) -> int | None:
        """原子：校验未过期 + 取出 user_id + 删除；不存在/过期返回 ``None``。"""
        ...

    async def clear_for_user(self, user_id: int) -> None:
        """删除该用户全部待验证令牌（备用，重发已由 create 覆盖）。"""
        ...


class DbEmailTokenStore:
    """DB 后端（默认，零新依赖）：令牌存独立的 ``email_tokens`` 表。

    复用 ``UserStore`` 的引擎与会话工厂，避免重复建连；表随 ``AuthBase.metadata.create_all``
    自动建立。消费为「查 + 删」原子（同一事务内），天然防双重消费。
    """

    def __init__(self, user_store: UserStore) -> None:
        self._store = user_store

    async def create(self, user_id: int, token: str, ttl: int) -> None:
        await self._store._ensure_schema()
        async with self._store._session() as s:
            # 覆盖旧令牌：删除该用户同类型旧令牌，再插入新令牌（重发幂等）。
            await s.execute(
                delete(EmailToken).where(EmailToken.user_id == user_id, EmailToken.type == "verify")
            )
            expires = datetime.now(UTC) + timedelta(seconds=ttl)
            s.add(EmailToken(token=token, user_id=user_id, type="verify", expires_at=expires))
            await s.commit()

    async def consume(self, token: str) -> int | None:
        await self._store._ensure_schema()
        async with self._store._session() as s:
            row = await s.get(EmailToken, token)
            if row is None:
                return None
            expires = row.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires < datetime.now(UTC):
                await s.delete(row)  # 过期行顺手清理
                await s.commit()
                return None
            user_id = row.user_id
            await s.delete(row)
            await s.commit()
            return user_id

    async def clear_for_user(self, user_id: int) -> None:
        await self._store._ensure_schema()
        async with self._store._session() as s:
            await s.execute(
                delete(EmailToken).where(EmailToken.user_id == user_id, EmailToken.type == "verify")
            )
            await s.commit()


class RedisEmailTokenStore:
    """Redis 后端（可选）：令牌存 ``goty:verify:{token}``，复用速率限制的同一条 Redis。

    利用 Redis 原生 ``EX`` TTL 自动过期，无需 ``expires_at`` 字段、无需扫表清理；
    额外维护 ``goty:verify:byuser:{user_id}`` 集合以支持 ``clear_for_user``。``redis`` 包
    按需懒导入——仅当配置了 ``rate_limit_redis_url`` 才构造本后端，保持零依赖默认体验。
    """

    def __init__(self, url: str) -> None:
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:  # pragma: no cover - 仅在显式配置 Redis 时触发
            raise RuntimeError("使用 Redis 令牌存储需先安装 redis：uv pip install redis") from exc
        self._redis = aioredis.from_url(url, decode_responses=True)
        self._prefix = "goty:verify:"

    async def create(self, user_id: int, token: str, ttl: int) -> None:
        key = f"{self._prefix}{token}"
        await self._redis.set(key, str(user_id), ex=ttl)
        await self._redis.sadd(f"{self._prefix}byuser:{user_id}", token)

    async def consume(self, token: str) -> int | None:
        key = f"{self._prefix}{token}"
        # GET + DEL 用 Lua 保证原子，避免并发下双重消费。
        script = (
            "local v = redis.call('get', KEYS[1]) if v then redis.call('del', KEYS[1]) end return v"
        )
        user_id = await self._redis.eval(script, 1, key)
        if user_id is None:
            return None
        return int(user_id)

    async def clear_for_user(self, user_id: int) -> None:
        set_key = f"{self._prefix}byuser:{user_id}"
        tokens = await self._redis.smembers(set_key)
        if tokens:
            await self._redis.delete(*[f"{self._prefix}{t}" for t in tokens])
        await self._redis.delete(set_key)


def create_token_store(settings, user_store: UserStore | None) -> TokenStore | None:
    """工厂：配置了 Redis（速率限制同一条）走 Redis 后端，否则走 DB 表后端。

    ``user_store`` 为空（auth 关闭）时返回 ``None``——邮箱验证整体惰性、不会触发。
    """
    if user_store is None:
        return None
    if getattr(settings, "rate_limit_redis_url", ""):
        return RedisEmailTokenStore(settings.rate_limit_redis_url)
    return DbEmailTokenStore(user_store)
