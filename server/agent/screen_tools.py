"""兼容实验报告桌面截图工具的旧导入路径。"""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("server.agent.lab-report.screen_tools")
