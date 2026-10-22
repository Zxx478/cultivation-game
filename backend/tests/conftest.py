"""pytest 共享 fixtures：内存 DB + 配置加载 + 种子数据。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# 把 backend 目录加入 sys.path，便于直接 import app.*
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def fresh_db():
    """每个测试函数使用全新内存数据库，互不污染。"""
    from app.db import init_db, seed_quest_pool, seed_techniques
    from app.db.repository import _thread_local
    from app.config import reload

    # 清掉 lru_cache 以防修改 YAML 后未重载
    reload()

    conn = init_db(":memory:", run_schema=True)
    _thread_local.conn = conn
    seed_quest_pool()
    seed_techniques()    # v1.7：technique/pill/equipment 种子

    yield conn

    conn.close()
    # 清理 lru_cache 避免后续测试命中旧配置
    reload()
