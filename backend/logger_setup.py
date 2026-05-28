import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name: str = "ai_dcp", log_dir: str = "logs", level=logging.INFO):
    """
    配置全局日志记录，同时输出到控制台和文件（带有滚动机制）。
    """
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # 避免重复添加 Handler
    if not logger.handlers:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # 控制台 Handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 文件 Handler，文件大小最大为 5MB，最多保留 3 个备份
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# 全局单例 logger
global_logger = setup_logger()
