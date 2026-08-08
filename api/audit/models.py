"""审计相关的 SQLAlchemy ORM 模型。

- ``AuditLog``：每一条后端请求审计记录（客户端 IP / 设备 / 接口 / 参数 / 响应等）。
- ``AnomalyEvent``：每一次请求源异常命中记录（便于事后复盘与策略调参）。

模型与具体数据库无关：SQLite / MySQL / OLAP 的 DSN 差异由引擎层处理，模型不动。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    client_ip: Mapped[str] = mapped_column(String(64), index=True)
    client_device: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512), index=True)
    query: Mapped[str] = mapped_column(Text, default="")
    request_body: Mapped[str] = mapped_column(Text, default="")
    status_code: Mapped[int] = mapped_column(Integer, index=True)
    duration_ms: Mapped[float] = mapped_column(default=0.0)
    is_anomaly: Mapped[bool] = mapped_column(default=False, index=True)
    anomaly_reasons: Mapped[str] = mapped_column(Text, default="")
    response_snippet: Mapped[str] = mapped_column(Text, default="")

    # ---- 访问统计维度（v1.7.0 新增；存量库经 ALTER TABLE 迁移补齐）----
    # visitor_id：sha256(ip|UA)[:16] 指纹，不下发 Cookie；UV 去重依据。
    visitor_id: Mapped[str] = mapped_column(String(32), index=True, default="")
    # referer：来源页（直接访问为空），用于「从哪来」归因。
    referer: Mapped[str] = mapped_column(Text, default="")
    # route_type：page / api / asset，由中间件按响应 Content-Type 判定；
    # 默认 'api' 以便迁移前旧行（皆为 api 行为）仍能正确归类。
    route_type: Mapped[str] = mapped_column(String(8), index=True, default="api")

    # ---- 已登录用户身份（v1.8.0 新增；匿名/未登录留空）----
    # user_id：关联 api.auth.models.User.id；未登录为 NULL。审计可按用户维度追溯。
    user_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    username: Mapped[str] = mapped_column(String(64), default="")


class AnomalyEvent(Base):
    __tablename__ = "anomaly_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(36), index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    client_ip: Mapped[str] = mapped_column(String(64), index=True)
    rule: Mapped[str] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(String(32), default="")
