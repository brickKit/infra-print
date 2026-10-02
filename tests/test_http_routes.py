"""infra_print.http.routes——真实 FastAPI app（``new_fastapi_app`` 挂好的全部
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

from infra_print.module import create_module
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
            "print_editor": ["infra.print.template.view", "infra.print.template.edit"],
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


async def _put(client, token: str, template_id: str, channel: str = "ZPL", content: str = "^XA^FD{{ x }}^FS^XZ"):
    return await client.put(
        f"/infra/print/templates/{template_id}",
        json={"name": "模板", "channel": channel, "content": content, "enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_put_template的channel不是PDF或ZPL返回422(app_client) -> None:
    client, jwks, _pool = app_client
    token = jwks.sign(sub="admin-1", roles=["print_admin"])
    resp = await _put(client, token, unique("bad-channel"), channel="DOCX")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_preview要编辑权限_只有查看权限返回403(app_client) -> None:
    # preview 发生在编辑流程里，绑 infra.print.template.edit，不是 view。
    client, jwks, _pool = app_client
    template_id = unique("perm-preview")
    assert (await _put(client, jwks.sign(sub="admin-1", roles=["print_admin"]), template_id)).status_code == 200

    viewer = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    editor = jwks.sign(sub="editor-1", roles=["print_editor"])
    body = {"data": {"x": "hi"}}
    url = f"/infra/print/templates/{template_id}/preview"
    assert (await client.post(url, json=body, headers={"Authorization": f"Bearer {viewer}"})).status_code == 403
    ok = await client.post(url, json=body, headers={"Authorization": f"Bearer {editor}"})
    assert ok.status_code == 200
    assert ok.content == b"^XA^FDhi^FS^XZ"


@pytest.mark.asyncio
async def test_render要渲染权限_只有编辑权限返回403(app_client) -> None:
    # render 绑 infra.print.render：能改模板不等于能用它出单据。
    client, jwks, _pool = app_client
    template_id = unique("perm-render")
    assert (await _put(client, jwks.sign(sub="admin-1", roles=["print_admin"]), template_id)).status_code == 200

    editor = jwks.sign(sub="editor-1", roles=["print_editor"])
    resp = await client.post(
        "/infra/print/render",
        json={"template_id": template_id, "data": {"x": "hi"}},
        headers={"Authorization": f"Bearer {editor}"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version",
    [
        True,  # 宽松的 int 会把 true 当成 1，悄悄回滚到 v1
        2147483648,  # 超出列的 INT，撞库时才报错
        0,  # 版本号从 1 开始
        "1",  # 契约是 integer，不收字符串
    ],
)
async def test_rollback的version不是1到int4上界的整数返回422(app_client, version) -> None:
    client, jwks, _pool = app_client
    token = jwks.sign(sub="admin-1", roles=["print_admin"])
    template_id = unique("rollback-strict")
    assert (await _put(client, token, template_id)).status_code == 200
    resp = await client.post(
        f"/infra/print/templates/{template_id}/rollback",
        json={"version": version},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rollback的version是合法整数时照常回滚(app_client) -> None:
    client, jwks, _pool = app_client
    token = jwks.sign(sub="admin-1", roles=["print_admin"])
    template_id = unique("rollback-ok")
    assert (await _put(client, token, template_id, content="^XA^FD1^FS^XZ")).status_code == 200
    assert (await _put(client, token, template_id, content="^XA^FD2^FS^XZ")).status_code == 200
    resp = await client.post(
        f"/infra/print/templates/{template_id}/rollback",
        json={"version": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 3
    assert resp.json()["content"] == "^XA^FD1^FS^XZ"
