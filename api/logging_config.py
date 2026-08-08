"""探索 API 的日志配置。

提供两类日志：
- ``goty.api``：应用 / 安全日志（控制台 + 可选按**大小**滚动的文件）。
- ``goty.audit``：请求审计日志（可选按**时间周期**轮转的文件，每行一条 JSON）。

两者都不向 root 传播，避免与 uvicorn 重复。审计日志单独成 logger，便于后续接入
ELK / 数仓时单独采集、独立轮转策略。

通过环境变量（经 :class:`api.config.Settings` 传入）配置：
- GOTY_LOG_LEVEL          日志级别（默认 INFO）
- GOTY_LOG_FILE           应用日志文件路径（按大小滚动；留空=仅控制台）
- GOTY_AUDIT_LOG_FILE     审计日志文件路径（按时间轮转；留空=不写文件，仅入库）
- GOTY_AUDIT_ROTATE_WHEN  轮转单位，TimedRotatingFileHandler 的 when（默认 midnight）
- GOTY_AUDIT_ROTATE_BACKUP 保留备份数（默认 14）
"""

import json
import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler

from .config import Settings

_DATEFMT = "%Y-%m-%dT%H:%M:%S%z"


def _ensure_dir(path: str) -> None:
    try:
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _configure_audit_logger(settings: Settings | None) -> logging.Logger:
    """配置 ``goty.audit`` logger：按时间周期轮转的文件（JSON 行）。幂等。"""
    logger = logging.getLogger("goty.audit")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_file = getattr(settings, "audit_log_file", "") or "" if settings else ""
    if not log_file:
        return logger
    try:
        _ensure_dir(log_file)
        fh = TimedRotatingFileHandler(
            log_file,
            when=getattr(settings, "audit_rotate_when", "midnight") or "midnight",
            backupCount=getattr(settings, "audit_rotate_backup", 14) or 14,
            encoding="utf-8",
        )
        # 每行 = 时间戳 + JSON 载荷，机器可解析、人类可读
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", _DATEFMT))
        logger.addHandler(fh)
    except Exception:
        logging.getLogger("goty.api").warning(
            "无法写入审计日志文件 %s，仅使用控制台/入库", log_file
        )
    return logger


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    """配置 ``goty.api`` + ``goty.audit`` 两个 logger。幂等（重复调用只配一次）。

    在 :func:`api.app.create_app` 中传入 ``Settings`` 调用；不传则退回环境变量默认值。
    """
    level_name = (getattr(settings, "log_level", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("goty.api")
    if logger.handlers:
        _configure_audit_logger(settings)  # 幂等：仍确保审计 logger 配置到位
        return logger

    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", _DATEFMT)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    log_file = getattr(settings, "log_file", "") or "" if settings else ""
    if log_file:
        try:
            _ensure_dir(log_file)
            fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            logger.warning("无法写入日志文件 %s，仅使用控制台日志", log_file)

    _configure_audit_logger(settings)
    return logger


def get_audit_logger() -> logging.Logger:
    """返回审计 logger（时间轮转文件 / 采集管道）。"""
    return logging.getLogger("goty.audit")


def log_audit_event(record: dict) -> None:
    """把一条审计记录以 JSON 行写入审计 logger（时间轮转文件 / 后续采集）。

    即使没有配置审计文件 handler，调用也是安全的（logger 无 handler 时静默丢弃）。
    """
    get_audit_logger().info(json.dumps(record, ensure_ascii=False, default=str))
