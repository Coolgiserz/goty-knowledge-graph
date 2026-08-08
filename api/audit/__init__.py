"""请求审计存储包。

对外暴露 :class:`api.audit.store.AuditStore` 与 ORM 模型。存储后端对调用方透明：
默认用 SQLite 打通流程，未来接 MySQL / OLAP 只需改 ``GOTY_AUDIT_DB_URL`` 连接串，
ORM 模型与读写接口保持不变（即「换数据库」的预留接口）。
"""
