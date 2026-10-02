"""模板版本列表 GET /infra/print/templates/{id}/versions（repo 与 HTTP 两层，真实 Postgres）。

响应 { versions: [{ version, name, enabled, created_at }], next_cursor }：新的在前，游标是
上一页最后一个版本号；enabled 是写入那个版本时模板的启用状态（回滚不改启用状态，所以
回滚出的新版本沿用当时的状态）。
"""

from __future__ import annotations

import asyncpg
import pytest

from infra_print.repo.templates import (
    TemplateNotFoundError,
    list_template_versions,
    put_template,
    rollback_template,
)
from tests.helpers import dsn, unique
from tests.test_http_routes import app_client  # noqa: F401 - pytest 夹具

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"


@pytest.mark.asyncio
async def test_版本列表新的在前_带启用状态_游标翻页不重不漏() -> None:
    pool = await asyncpg.create_pool(dsn())
    tid = unique("ver")
    try:
        await put_template(pool, _ROLE, _SCHEMA, tid, "初稿", "PDF", "<p>1</p>")
        await put_template(pool, _ROLE, _SCHEMA, tid, "停用稿", "PDF", "<p>2</p>", enabled=False)
        await rollback_template(pool, _ROLE, _SCHEMA, tid, 1)  # v3 = v1 的内容，启用状态沿用当前（停用）

        page1, cur1 = await list_template_versions(pool, _ROLE, _SCHEMA, tid, None, 2)
        assert [(v.version, v.name, v.enabled) for v in page1] == [(3, "初稿", False), (2, "停用稿", False)]
        assert cur1 == "2"
        page2, cur2 = await list_template_versions(pool, _ROLE, _SCHEMA, tid, int(cur1), 2)
        assert [(v.version, v.name, v.enabled) for v in page2] == [(1, "初稿", True)]
        assert cur2 == ""
        assert all(v.created_at is not None for v in page1 + page2)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_版本列表模板不存在报TemplateNotFound() -> None:
    pool = await asyncpg.create_pool(dsn())
    try:
        with pytest.raises(TemplateNotFoundError):
            await list_template_versions(pool, _ROLE, _SCHEMA, unique("missing"), None, 50)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_HTTP版本列表_查看权限即可_响应形状(app_client) -> None:  # noqa: F811
    client, jwks, _pool = app_client
    admin = jwks.sign(sub="admin-1", roles=["print_admin"])
    viewer = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    tid = unique("http-ver")
    for content in ("^XA^FD1^FS^XZ", "^XA^FD2^FS^XZ"):
        resp = await client.put(
            f"/infra/print/templates/{tid}",
            json={"name": "标签", "channel": "ZPL", "content": content, "enabled": True},
            headers={"Authorization": f"Bearer {admin}"},
        )
        assert resp.status_code == 200

    url = f"/infra/print/templates/{tid}/versions"
    assert (await client.get(url)).status_code == 401

    resp = await client.get(url, headers={"Authorization": f"Bearer {viewer}"})
    assert resp.status_code == 200
    body = resp.json()
    assert [v["version"] for v in body["versions"]] == [2, 1]
    assert set(body["versions"][0]) == {"version", "name", "enabled", "created_at"}
    assert body["versions"][0]["enabled"] is True
    assert body["next_cursor"] == ""

    resp = await client.get(url, params={"page_size": 1}, headers={"Authorization": f"Bearer {viewer}"})
    assert [v["version"] for v in resp.json()["versions"]] == [2]
    resp = await client.get(
        url, params={"page_size": 1, "cursor": resp.json()["next_cursor"]}, headers={"Authorization": f"Bearer {viewer}"}
    )
    assert [v["version"] for v in resp.json()["versions"]] == [1]


@pytest.mark.asyncio
async def test_HTTP版本列表_模板不存在404_游标不对400(app_client) -> None:  # noqa: F811
    client, jwks, _pool = app_client
    viewer = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    h = {"Authorization": f"Bearer {viewer}"}
    assert (await client.get(f"/infra/print/templates/{unique('missing')}/versions", headers=h)).status_code == 404

    admin = jwks.sign(sub="admin-1", roles=["print_admin"])
    tid = unique("http-ver-cursor")
    await client.put(
        f"/infra/print/templates/{tid}",
        json={"name": "标签", "channel": "ZPL", "content": "^XA^XZ"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    resp = await client.get(f"/infra/print/templates/{tid}/versions", params={"cursor": "abc"}, headers=h)
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    [
        "²",  # str.isdigit() 认上标数字，int() 却解析不了
        "٣",  # 阿拉伯-印度数字：isdigit() 为真，int() 会悄悄当成 3
        "99999999999",  # 超出 int4，撞库时才报错
        "2147483648",  # int4 上界 + 1
        "0",  # 版本号从 1 开始，游标不会是 0
        "01",  # 本组件发出的游标没有前导零
        "-1",
        " 1",
    ],
)
async def test_HTTP版本列表_游标只认1到int4上界的ASCII十进制_其余400(app_client, cursor) -> None:  # noqa: F811
    client, jwks, _pool = app_client
    admin = jwks.sign(sub="admin-1", roles=["print_admin"])
    viewer = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    tid = unique("http-ver-strict-cursor")
    await client.put(
        f"/infra/print/templates/{tid}",
        json={"name": "标签", "channel": "ZPL", "content": "^XA^XZ"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    url = f"/infra/print/templates/{tid}/versions"
    resp = await client.get(url, params={"cursor": cursor}, headers={"Authorization": f"Bearer {viewer}"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_HTTP版本列表_游标取int4上界照常翻页(app_client) -> None:  # noqa: F811
    client, jwks, _pool = app_client
    admin = jwks.sign(sub="admin-1", roles=["print_admin"])
    viewer = jwks.sign(sub="viewer-1", roles=["print_viewer"])
    tid = unique("http-ver-max-cursor")
    await client.put(
        f"/infra/print/templates/{tid}",
        json={"name": "标签", "channel": "ZPL", "content": "^XA^XZ"},
        headers={"Authorization": f"Bearer {admin}"},
    )
    url = f"/infra/print/templates/{tid}/versions"
    resp = await client.get(url, params={"cursor": "2147483647"}, headers={"Authorization": f"Bearer {viewer}"})
    assert resp.status_code == 200
    assert [v["version"] for v in resp.json()["versions"]] == [1]
