"""探索 API 的日志配置。

提供结构化、可检索的请求与安全防护日志：
- 专用 logger ``goty.api``（不向 root 传播，避免与 uvicorn 重复）。
- 控制台 + 可选 RotatingFileHandler（按大小滚动，避免单文件无限膨胀）。
- 消息本身内联关键字段（client IP / 方法 / 路径 / 状态 / 耗时），
  便于 grep / 后续接入 ELK 等。

通过环境变量配置：
- GOTY_LOG_LEVEL  日志级别（默认 INFO）
- GOTY_LOG_FILE   日志文件路径（为空则仅控制台）
"""

import logging
import os


def setup_logging() -> logging.Logger:
    level = getattr(logging, os.environ.get("GOTY_LOG_LEVEL", "INFO").upper(), logging.INFO)
    log_file = os.environ.get("GOTY_LOG_FILE", "")

    logger = logging.getLogger("goty.api")
    if logger.handlers:  # 已配置，幂等
        return logger

    logger.setLevel(level)
    logger.propagate = False  # 不污染 uvicorn / root

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        "%Y-%m-%dT%H:%M:%S%z",
    )

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    if log_file:
        try:
            from logging.handlers import RotatingFileHandler

            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except Exception:
            logger.warning("无法写入日志文件 %s，仅使用控制台日志", log_file)

    return logger
