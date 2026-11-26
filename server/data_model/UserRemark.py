from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from server.database import Base


class UserRemark(Base):
    __tablename__ = "user_remarks"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    remark: Mapped[str] = mapped_column(Text, nullable=False, default="")
