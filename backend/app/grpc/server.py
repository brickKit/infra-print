"""gRPC 面——组件间调用入口，同一套业务逻辑由 app.service.print_service
提供（REST/gRPC 共用，http/routes.py 的既有判据）。

⚠️ actor 留空：gRPC 面没有 ``besdk.scope_of()`` 可用（本项目目前没有
任何组件在 gRPC 侧转发/验证 JWT，同 infra-workflow AGENTS.md 的既有
判据）——print_jobs.actor 只在 REST 调用路径上才有意义。
"""

from __future__ import annotations

import json

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from infra.print.v1 import print_pb2, print_pb2_grpc

from app.repo.templates import Template, TemplateNotFoundError
from app.service.print_service import PrintService, RenderError, TemplateDisabledError

_CHANNEL_TO_PROTO = {
    "PDF": print_pb2.Channel.CHANNEL_PDF,
    "ZPL": print_pb2.Channel.CHANNEL_ZPL,
}
_CHANNEL_FROM_PROTO = {v: k for k, v in _CHANNEL_TO_PROTO.items()}


def _to_timestamp(dt) -> Timestamp:
    ts = Timestamp()
    ts.FromDatetime(dt)
    return ts


def _template_to_proto(t: Template) -> print_pb2.PrintTemplate:
    return print_pb2.PrintTemplate(
        id=t.id,
        name=t.name,
        channel=_CHANNEL_TO_PROTO[t.channel],
        content=t.content,
        version=t.version,
        enabled=t.enabled,
        created_at=_to_timestamp(t.created_at),
        updated_at=_to_timestamp(t.updated_at),
    )


class PrintServiceServicer(print_pb2_grpc.PrintServiceServicer):
    def __init__(self, svc: PrintService) -> None:
        self._svc = svc

    async def Render(
        self, request: print_pb2.RenderRequest, context: grpc.aio.ServicerContext
    ) -> print_pb2.RenderResponse:
        try:
            data = json.loads(request.data_json) if request.data_json else {}
        except ValueError:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "data_json 不是合法 JSON")

        try:
            content, content_type = await self._svc.render(
                request.template_id, data, actor="", source_component=request.source_component
            )
        except TemplateNotFoundError:
            await context.abort(grpc.StatusCode.NOT_FOUND, f"模板不存在：{request.template_id}")
        except TemplateDisabledError:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, f"模板已停用：{request.template_id}")
        except RenderError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return print_pb2.RenderResponse(content=content, content_type=content_type)

    async def BatchGetTemplates(
        self, request: print_pb2.BatchGetTemplatesRequest, context: grpc.aio.ServicerContext
    ) -> print_pb2.BatchGetTemplatesResponse:
        templates = await self._svc.batch_get_templates(list(request.template_ids))
        return print_pb2.BatchGetTemplatesResponse(templates=[_template_to_proto(t) for t in templates])

    async def ListTemplates(
        self, request: print_pb2.ListTemplatesRequest, context: grpc.aio.ServicerContext
    ) -> print_pb2.ListTemplatesResponse:
        channel = _CHANNEL_FROM_PROTO.get(request.channel) if request.channel else None
        templates, next_cursor = await self._svc.list_templates(channel, request.cursor, request.page_size)
        return print_pb2.ListTemplatesResponse(
            templates=[_template_to_proto(t) for t in templates], next_cursor=next_cursor
        )


def register(svc: PrintService):
    def _register(server: grpc.aio.Server) -> None:
        print_pb2_grpc.add_PrintServiceServicer_to_server(PrintServiceServicer(svc), server)

    return _register
