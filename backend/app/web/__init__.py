"""Web 层（工程包 C）。

职责：Jinja2 首屏骨架 + 原生 ES Module 静态资源。
硬约束：
- 无 npm / 无 CDN / 无外链字体与图片；
- 前端不持有权威状态，只渲染 ``/api/v1/*`` 的返回值；
- 所有用户输入一律以 ``textContent`` 渲染，禁止 ``innerHTML`` 拼接。

``main.py`` 按「目录存在才挂载」的方式使用这里的常量::

    from .web import TEMPLATE_DIR, STATIC_DIR
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
"""

from __future__ import annotations

from pathlib import Path

LOCAL_DIR: Path = Path(__file__).resolve().parent
TEMPLATE_DIR: Path = LOCAL_DIR / "templates"
STATIC_DIR: Path = LOCAL_DIR / "static"

INDEX_TEMPLATE: str = "index.html"

__all__ = [
    "LOCAL_DIR",
    "TEMPLATE_DIR",
    "STATIC_DIR",
    "INDEX_TEMPLATE",
]
