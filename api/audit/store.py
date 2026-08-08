"""请求审计存储：SQLAlchemy ORM 抽象，后端无关。

设计要点：
- 默认用 SQLite 打通流程（``sqlite:///...``）。
- 未来接 MySQL / OLAP 只需改连接串（如 ``mysql+pymysql://user:pwd@host/db``、
  ``clickhouse+http://...``），ORM 模型与读写接口不变——这就是预留的「换数据库」接口。
- 同步引擎 + ``asyncio.to_thread`` 调用，避免阻塞 FastAPI 事件循环
  （对齐 FastAPI 官方 async 文档：阻塞 I/O 不应留在主循环）。

线程安全：SQLite 默认单连接，这里显式 ``check_same_thread=False`` 并交由连接池管理；
多进程部署时请改用外部数据库（MySQL/PG/OLAP）URL。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, make_url, select
from sqlalchemy.orm import Session, sessionmaker

from .models import AnomalyEvent, AuditLog, Base


class AuditStore:
    """审计存储门面：对调用方屏蔽具体数据库。

    用法::

        store = AuditStore("sqlite:///./data/audit.db")
        store.init()  # 建表（幂等）
        store.record_audit({...})  # 由中间件经 asyncio.to_thread 调用
        store.record_anomaly({...})
    """

    def __init__(self, url: str, echo: bool = False) -> None:
        self.url = url
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
            # 确保 sqlite 文件路径的父目录存在（mysql/OLAP 等无此问题）
            try:
                db_path = make_url(url).database
                if db_path and db_path != ":memory:":
                    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        self.engine = create_engine(url, echo=echo, future=True, connect_args=connect_args)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def init(self) -> None:
        """建表（幂等）。生产环境建议改用 Alembic 迁移管理。"""
        Base.metadata.create_all(self.engine)

    def record_audit(self, data: dict[str, Any]) -> None:
        """写入一条审计记录（同步，调用方负责放进 to_thread）。"""
        with self._session() as s:
            s.add(self._audit_from_dict(data))
            s.commit()

    def record_anomaly(self, data: dict[str, Any]) -> None:
        """写入一条异常事件（同步）。"""
        with self._session() as s:
            s.add(AnomalyEvent(**data))
            s.commit()

    def count_audit(self, client_ip: str | None = None) -> int:
        """统计审计记录数（可选按 IP 过滤），用于运维/测试核对。"""
        with self._session() as s:
            stmt = select(func.count()).select_from(AuditLog)
            if client_ip:
                stmt = stmt.where(AuditLog.client_ip == client_ip)
            return int(s.scalar(stmt) or 0)

    def count_anomalies(self, client_ip: str | None = None) -> int:
        with self._session() as s:
            stmt = select(func.count()).select_from(AnomalyEvent)
            if client_ip:
                stmt = stmt.where(AnomalyEvent.client_ip == client_ip)
            return int(s.scalar(stmt) or 0)

    # ---- 内部 ----

    @staticmethod
    def _audit_from_dict(data: dict[str, Any]) -> AuditLog:
        allowed = {c.name for c in AuditLog.__table__.columns}
        return AuditLog(**{k: v for k, v in data.items() if k in allowed})

    @contextmanager
    def _session(self):
        s: Session = self._session_factory()
        try:
            yield s
        finally:
            s.close()
