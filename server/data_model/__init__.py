"""数据库模型。"""

from server.data_model.User import User
from server.data_model.Conversation import Conversation
from server.data_model.UserRemark import UserRemark
from importlib import import_module

SolutionConversation = import_module("server.data-operations.database.solution_conversation").SolutionConversation

__all__ = ["User", "Conversation", "UserRemark", "SolutionConversation"]
