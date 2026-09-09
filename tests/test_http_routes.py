"""app.http.routes——真实 FastAPI app（``new_fastapi_app`` 挂好的全部
中间件）+ 真实 RS256 JWT + 真实 bundle 轮询，走 httpx 的 ASGITransport
直接对 ASGI app 发真请求（不经过真实 socket，但请求/响应、权限判定链路
是真的，不是绕过 ``require_permission`` 直接调 handler 函数）。
"""

from __future__ import annotations

import asyncio
import logging

import asyncpg
import httpx
import nats
import pytest
from besdk.authz import _set_authz_runtime, setup_authz_runtime
from besdk.metrics import new_registry
from besdk.otel import get_meter, get_tracer, init_otel
from besdk.runtime import Config, Runtime

from app.module import create_module
from tests.authz_helpers import FakeBundleServer, FakeJWKSServer
from tests.helpers import dsn, nats_url, unique

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"
_logger = logging.getLogger("test_http_routes")


@pytest.fixture
async def app_client():
    pool = await asyncpg.create_pool(dsn())
    nc = await nats.connect(nats_url())

    jwks = FakeJWKSServer()
    bundle_server = FakeBundleServer(
        {
            "print_admin": [
                "infra.print.render",
                "infra.print.template.view",
                "infra.print.template.edit",
            ],
            "print_viewer": ["infra.print.template.view"],
        }
    )

    verifier, bundle = setup_authz_runtime(jwks.url, bundle_server.url, _logger)
    _set_authz_runtime(verifier, bundle)
    deadline = asyncio.get_event_loop().time() + 5
    while not bundle.has_ever_fetched() and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
    assert bundle.has_ever_fetched(), "真实 bundle 轮询应该在 5 秒内至少成功过一次"

    shutdown_otel = await init_otel("infra-print-test", "")
    rt = Runtime(
        component_id="infra/print",
        component_version="test",
        config=Config({}),
        db=pool,
        nats=nc,
        logger=_logger,
        tracer=get_tracer("infra-print-test"),
        meter=get_meter("infra-print-test"),
        registry=new_registry(),
        http_port=0,
    )
    mod = await create_module(rt)

    transport = httpx.ASGITransport(app=mod.asgi_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client, jwks, pool
    finally:
        await client.aclose()
        jwks.close()
        bundle_server.close()
        await shutdown_otel()
        await nc.close()
        await pool.close()


@pytest.mark.asyncio
async def test_未带token访问需权限的接口fail_closed_401(app_client) -> None:
    client, _jwks, _pool = app_client
    resp = await client.get("/infra/print/templates")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_角色没有对应权限键返回403(app_client) -> None:
    client, jwks, _pool = app_client
    token = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    resp = await client.post(
        "/infra/print/render",
        json={"template_id": "x", "data": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_有权限的角色能真实渲染并拿到PDF字节(app_client) -> None:
    client, jwks, pool = app_client
    template_id = unique("http-tpl")
    await pool.execute(
        f"INSERT INTO {_SCHEMA}.print_templates (id, name, channel, content, version) "
        f"VALUES ($1, 'HTTP 模板', 'PDF', '<h1>{{{{ x }}}}</h1>', 1)",
        template_id,
    )

    token = jwks.sign(sub="admin-1", roles=["print_admin"])
    resp = await client.post(
        "/infra/print/render",
        json={"template_id": template_id, "data": {"x": "hello"}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.content[:4] == b"%PDF"

    job_row = await pool.fetchrow(
        f"SELECT actor FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
    )
    assert job_row["actor"] == "admin-1"


@pytest.mark.asyncio
async def test_渲染不存在的模板返回404(app_client) -> None:
    client, jwks, _pool = app_client
    token = jwks.sign(sub="admin-1", roles=["print_admin"])
    resp = await client.post(
        "/infra/print/render",
        json={"template_id": unique("missing"), "data": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_put_template再get回来能看到新版本(app_client) -> None:
    client, jwks, _pool = app_client
    template_id = unique("http-put")
    token = jwks.sign(sub="admin-1", roles=["print_admin"])

    put_resp = await client.put(
        f"/infra/print/templates/{template_id}",
        json={"name": "新模板", "channel": "ZPL", "content": "^XA^XZ", "enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["version"] == 1

    get_resp = await client.get(
        f"/infra/print/templates/{template_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["content"] == "^XA^XZ"
