"""兼容旧模块路径。"""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("server.agent.lab-report.file_tools")
