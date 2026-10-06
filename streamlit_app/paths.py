"""仓库根目录与 Streamlit 应用目录；统一 sys.path（见改进2.md）。"""
from __future__ import annotations

import os
import sys

STREAMLIT_APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(STREAMLIT_APP_DIR)


def ensure_paths() -> None:
    """幂等：`import common` 必须指向仓库根目录的 common.py（含 setup_logger）。

    若先把 streamlit_app 插在 sys.path[0]，可能载入错误模块或旧缓存路径下的 common，
    导致 AttributeError: module 'common' has no attribute 'setup_logger'。
    正确顺序：**PROJECT_ROOT 在 STREAMLIT_APP_DIR 之前**。
    """
    for p in (STREAMLIT_APP_DIR, PROJECT_ROOT):
        while p in sys.path:
            sys.path.remove(p)
    sys.path.insert(0, STREAMLIT_APP_DIR)
    sys.path.insert(0, PROJECT_ROOT)
