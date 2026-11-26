from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    user_name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    user_password: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[int] = mapped_column(default=0, comment="用户状态,0:正常 1:禁用")
    identity: Mapped[int] = mapped_column(default=0, comment="用户身份,0:普通用户 1:管理员")
    image: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="用户头像")
