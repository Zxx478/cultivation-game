"""本地启动入口：``python run.py``。

默认只绑定 127.0.0.1:5088（单机单人；不要绑 0.0.0.0）。
环境变量：
- ``HUANGTIANJI_DB``    覆盖 SQLite 路径（默认 ``data/game.db``）
- ``HUANGTIANJI_DATA``  覆盖数据目录
- ``SAVE_SIGN_SECRET``  存档 HMAC 密钥（生产必须设置）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

if os.environ.get("HUANGTIANJI_DB"):
    os.environ["HUANGTIANJI_DATA"] = str(Path(os.environ["HUANGTIANJI_DB"]).resolve().parent)

import uvicorn  # noqa: E402


def main() -> None:
    """启动 uvicorn（单进程）。"""
    port = int(os.environ.get("PORT", "5088"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port, reload=False, workers=1)


if __name__ == "__main__":
    main()
