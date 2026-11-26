"""本地 MySQL 连接与 SQLAlchemy 会话配置。"""
import datetime
import os
from functools import lru_cache
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL
from sqlalchemy.orm import DeclarativeBase, Session, Mapped, mapped_column

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def mysql_settings() -> dict:
    """读取项目根目录 .env；进程环境变量优先。"""
    file_settings = dotenv_values(ENV_FILE)

    def setting(name: str, default: str | None = None) -> str | None:
        return os.environ[name] if name in os.environ else file_settings.get(name) or default

    return {
        "host": setting("MYSQL_HOST", "127.0.0.1"),
        "port": int(setting("MYSQL_PORT", "3306")),
        "user": setting("MYSQL_USER", "root"),
        "password": setting("MYSQL_PASSWORD", ""),
        "database": setting("MYSQL_DATABASE") or None,
    }


def connect_mysql() -> pymysql.connections.Connection:
    """建立原生 PyMySQL 连接，调用方负责提交并关闭。"""
    return pymysql.connect(**mysql_settings(), charset="utf8mb4", connect_timeout=5)


class Base(DeclarativeBase):
    id: Mapped[int] = mapped_column(primary_key=True)
    create_time: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now)
    update_time: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.now, onupdate=datetime.datetime.now)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = mysql_settings()
    url = URL.create(
        "mysql+pymysql",
        username=settings["user"],
        password=settings["password"],
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
    )
    return create_engine(url, pool_pre_ping=True)


def get_session() -> Session:
    """创建 SQLAlchemy 会话，调用方使用 with 并显式提交。"""
    return Session(get_engine())
