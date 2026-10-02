"""REST 面，路径都在 /infra/print/** 下（assembly.yaml 的 edge_routes）。

每条路由都经 ``besdk.get/post/put`` 带权限键注册，键与 assembly.yaml 的 permissions 段
一字不差；裸 ``router.get`` 没有任何权限检查，``make gates`` 的裸路由扫描会拦。

错误映射在每个 handler 里显式 try/except 成 ``HTTPException``：be-sdk-python 的
``new_fastapi_app`` 不把领域异常映射成状态码（be-sdk-go 有 ``service.ToStatus``）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field, StrictInt

import besdk
from infra_print.repo.templates import Template, TemplateNotFoundError, TemplateVersion, TemplateVersionNotFoundError
from infra_print.service.print_service import InvalidCursorError, PrintService, RenderError, TemplateDisabledError


def _template_to_dto(t: Template) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "channel": t.channel,
        "content": t.content,
        "version": t.version,
        "enabled": t.enabled,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _version_to_dto(v: TemplateVersion) -> dict:
    return {"version": v.version, "name": v.name, "enabled": v.enabled, "created_at": v.created_at.isoformat()}


class RenderRequestBody(BaseModel):
    template_id: str
    data: dict


class TemplateUpdateBody(BaseModel):
    name: str
    channel: Literal["PDF", "ZPL"]  # 契约的 enum；别的值由 FastAPI 回 422，不撞库表的 CHECK
    content: str
    enabled: bool = True


class RollbackBody(BaseModel):
    # StrictInt：宽松的 int 会把 true 当成 1、把 "1" 当成 1；上界是列的 INT（int4），
    # 超出的值不能留到撞库时才变成 500。越界、非整数由 FastAPI 回 422。
    version: StrictInt = Field(ge=1, le=2**31 - 1)


class PreviewBody(BaseModel):
    data: dict = {}


def new_router(svc: PrintService) -> APIRouter:
    router = APIRouter()

    async def render_handler(body: RenderRequestBody) -> Response:
        # REST 的调用方是人（actor 取自 token），source_component 留空；只有组件间
        # gRPC Render 由调用方自报 source_component。
        actor = besdk.scope_of().owner
        try:
            content, content_type = await svc.render(body.template_id, body.data, actor=actor, source_component="")
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except TemplateDisabledError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except RenderError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return Response(content=content, media_type=content_type)

    async def preview_handler(body: PreviewBody, template_id: str = Path(alias="id")) -> Response:
        try:
            content, content_type = await svc.preview(template_id, body.data)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except TemplateDisabledError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except RenderError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return Response(content=content, media_type=content_type)

    async def list_templates_handler(
        channel: str | None = Query(default=None),
        cursor: str = Query(default=""),
        page_size: int = Query(default=50),
    ) -> dict:
        templates, next_cursor = await svc.list_templates(channel, cursor, page_size)
        return {"templates": [_template_to_dto(t) for t in templates], "next_cursor": next_cursor}

    async def get_template_handler(template_id: str = Path(alias="id")) -> dict:
        try:
            t = await svc.get_template(template_id)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return _template_to_dto(t)

    async def put_template_handler(body: TemplateUpdateBody, template_id: str = Path(alias="id")) -> dict:
        t = await svc.put_template(template_id, body.name, body.channel, body.content, enabled=body.enabled)
        return _template_to_dto(t)

    async def rollback_template_handler(body: RollbackBody, template_id: str = Path(alias="id")) -> dict:
        try:
            t = await svc.rollback_template(template_id, body.version)
        except (TemplateNotFoundError, TemplateVersionNotFoundError) as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return _template_to_dto(t)

    async def list_versions_handler(
        template_id: str = Path(alias="id"),
        cursor: str = Query(default=""),
        page_size: int = Query(default=50),
    ) -> dict:
        try:
            versions, next_cursor = await svc.list_template_versions(template_id, cursor, page_size)
        except TemplateNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except InvalidCursorError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"versions": [_version_to_dto(v) for v in versions], "next_cursor": next_cursor}

    besdk.post(router, "/infra/print/render", "infra.print.render", render_handler)
    # preview 是编辑模板时的所见即所得校验，归编辑权限，不是查看权限。
    besdk.post(router, "/infra/print/templates/{id}/preview", "infra.print.template.edit", preview_handler)
    besdk.get(router, "/infra/print/templates", "infra.print.template.view", list_templates_handler)
    besdk.get(router, "/infra/print/templates/{id}", "infra.print.template.view", get_template_handler)
    besdk.get(router, "/infra/print/templates/{id}/versions", "infra.print.template.view", list_versions_handler)
    besdk.put(router, "/infra/print/templates/{id}", "infra.print.template.edit", put_template_handler)
    besdk.post(router, "/infra/print/templates/{id}/rollback", "infra.print.template.edit", rollback_template_handler)

    return router
