"""API 冒烟：应用能起、/health 通、既有宗门路由可达（不要求 200，非 500 即可）。

本文件同时守护 B0 的线程连接修复：TestClient 的请求跑在工作线程，
只有 `get_conn()` 正确回落到 `_global_conn` 才不会 500。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    """用内存库创建独立 app（不污染其他测试）。"""
    from app.main import create_app

    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        yield c


def test_health_ok(client):
    """/health 必须 200 且 status=ok。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_sect_status_route_not_500(client):
    """既有宗门状态接口可达：200/404 均可，不能 500。"""
    resp = client.get("/api/v1/sect/status/1/qingyun_sword")
    assert resp.status_code != 500, resp.text
    assert resp.status_code in (200, 404)


def test_root_returns_html_or_503(client):
    """首页：模板存在则 200，不存在则 503/404（不得 500）。"""
    resp = client.get("/")
    assert resp.status_code != 500, resp.text
    assert resp.status_code in (200, 404, 503)
