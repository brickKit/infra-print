"""app.grpc.server——真实 grpc.aio in-process server + 真实 client stub
调用（不是直接调 servicer 的方法，走真实的 gRPC 序列化/反序列化）。
"""

from __future__ import annotations

import json
import logging

import asyncpg
import grpc
import pytest
from infra.print.v1 import print_pb2, print_pb2_grpc

from app.grpc.server import register
from app.repo.templates import put_template
from app.service.print_service import PrintService
from tests.helpers import dsn, unique

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"
_logger = logging.getLogger("test_grpc_server")


@pytest.fixture
async def grpc_channel():
    pool = await asyncpg.create_pool(dsn())
    svc = PrintService(pool, _ROLE, _SCHEMA, _logger)

    server = grpc.aio.server()
    register(svc)(server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield channel, pool
    finally:
        await channel.close()
        await server.stop(grace=1)
        await pool.close()


@pytest.mark.asyncio
async def test_render真实gRPC调用ZPL通道(grpc_channel) -> None:
    channel, pool = grpc_channel
    stub = print_pb2_grpc.PrintServiceStub(channel)
    template_id = unique("grpc-zpl")
    await put_template(pool, _ROLE, _SCHEMA, template_id, "标签", "ZPL", "^XA^FD{{ sku }}^FS^XZ")

    resp = await stub.Render(
        print_pb2.RenderRequest(
            template_id=template_id,
            data_json=json.dumps({"sku": "ABC-1"}),
            source_component="erp/sales",
        )
    )

    assert resp.content == b"^XA^FDABC-1^FS^XZ"
    assert resp.content_type == "text/plain"

    job_row = await pool.fetchrow(
        f"SELECT source_component FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
    )
    assert job_row["source_component"] == "erp/sales"


@pytest.mark.asyncio
async def test_render模板不存在返回NOT_FOUND(grpc_channel) -> None:
    channel, _pool = grpc_channel
    stub = print_pb2_grpc.PrintServiceStub(channel)

    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await stub.Render(print_pb2.RenderRequest(template_id=unique("missing"), data_json="{}"))

    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_BatchGetTemplates防N加1批量读(grpc_channel) -> None:
    channel, pool = grpc_channel
    stub = print_pb2_grpc.PrintServiceStub(channel)
    id_a, id_b, missing = unique("a"), unique("b"), unique("missing")
    await put_template(pool, _ROLE, _SCHEMA, id_a, "A", "PDF", "<p>a</p>")
    await put_template(pool, _ROLE, _SCHEMA, id_b, "B", "ZPL", "^XA^XZ")

    resp = await stub.BatchGetTemplates(print_pb2.BatchGetTemplatesRequest(template_ids=[id_a, missing, id_b]))

    assert {t.id for t in resp.templates} == {id_a, id_b}
    for t in resp.templates:
        if t.id == id_a:
            assert t.channel == print_pb2.Channel.CHANNEL_PDF
        else:
            assert t.channel == print_pb2.Channel.CHANNEL_ZPL


@pytest.mark.asyncio
async def test_ListTemplates按channel筛选(grpc_channel) -> None:
    channel, pool = grpc_channel
    stub = print_pb2_grpc.PrintServiceStub(channel)
    template_id = unique("list-grpc")
    await put_template(pool, _ROLE, _SCHEMA, template_id, "X", "ZPL", "^XA^XZ")

    resp = await stub.ListTemplates(
        print_pb2.ListTemplatesRequest(channel=print_pb2.Channel.CHANNEL_ZPL, cursor="", page_size=200)
    )

    assert template_id in {t.id for t in resp.templates}
