"""内容层：16 个 YAML → 冻结只读结构 → 索引 → 内容哈希。"""
from __future__ import annotations

from .loader import (
    CONTENT_DIR,
    Content,
    ContentLoadError,
    content_hash_of,
    load_all,
    reload_all,
)
from .registry import Registry, get_registry
from .validators import ContentValidationError, check_all, validate

__all__ = [
    "CONTENT_DIR",
    "Content",
    "ContentLoadError",
    "content_hash_of",
    "load_all",
    "reload_all",
    "Registry",
    "get_registry",
    "ContentValidationError",
    "check_all",
    "validate",
]
