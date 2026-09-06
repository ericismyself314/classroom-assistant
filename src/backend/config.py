"""全局配置：数据目录、日志级别，以及单例访问入口。

其余用户设置（默认模型、语言、时区、是否启用 GPU、并发数、API 设置等）保存在数据库的
app_settings 表中，由 db.py 的 SettingsStore 管理；这里只保存必须在打开数据库之前就知道的
少量信息（数据目录 + 日志级别）。
"""
from __future__ import annotations

import logging
import os

from . import paths
from .logutil import configure_logging, get_logger

LOG = get_logger("config")


class AppConfig:
    def __init__(self) -> None:
        self.data_dir = paths.load_data_dir()
        self.dirs = paths.DataDirs(self.data_dir)
        self.log_level = os.environ.get("CA_LOG_LEVEL", "INFO").upper()

    def set_data_dir(self, new_dir: str) -> None:
        paths.save_data_dir(new_dir)
        self.data_dir = new_dir
        self.dirs = paths.DataDirs(new_dir)
        LOG.info("数据目录已切换为: %s", new_dir)

    def reload(self) -> None:
        self.data_dir = paths.load_data_dir()
        self.dirs = paths.DataDirs(self.data_dir)


# 全局单例（在 main 启动早期创建）
CONFIG: "AppConfig | None" = None


def get_config() -> "AppConfig":
    global CONFIG
    if CONFIG is None:
        CONFIG = AppConfig()
        configure_logging(CONFIG)
    return CONFIG


def init_config() -> "AppConfig":
    global CONFIG
    CONFIG = AppConfig()
    configure_logging(CONFIG)
    return CONFIG
