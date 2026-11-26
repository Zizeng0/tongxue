"""Compatibility import for the PowerPoint tools."""

import sys
from importlib import import_module

sys.modules[__name__] = import_module("server.General_Tools.ppt-operations.presentation_writer")
