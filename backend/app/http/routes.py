"""REST 面——挂载路径 /infra/print/**（assembly.yaml 的 edge_routes）。

⚠️ 三个权限键与 assembly.yaml 的 permissions 段一字不差——Go 版漏写编译
不过，这里靠 make gates 的裸路由扫描守（authz.py 的既有判据）。

⚠️ 错误映射：be-sdk-python 目前没有 be-sdk-go 那种统一的
``service.ToStatus(err)`` + gin 中间件把 domain error 自动翻成 HTTP
状态码（fastapi_app.py 的中间件目前只有 OTel/RED/日志），所以这里在
每个 handler 里显式 try/except 转成 ``HTTPException``——这是本组件自己
的判断，等 be-sdk-python 补上统一的错误映射后再回来削减这层重复。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Query, Response, status
from pydantic import BaseModel

import besdk
from app.repo.templates import Template, TemplateNotFoundError, TemplateVersionNotFoundError
from app.service.print_service import PrintService, RenderError, TemplateDisabledError


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


class RenderRequestBody(BaseModel):
    template_id: str
    data: dict


class TemplateUpdateBody(BaseModel):
    name: str
    channel: str
    content: str
    enabled: bool = True


class RollbackBody(BaseModel):
    version: int


class PreviewBody(BaseModel):
    data: dict = {}


def new_router(svc: PrintService) -> APIRouter:
    router = APIRouter()

    async def render_handler(body: RenderRequestBody) -> Response:
        # REST 调用方是真人经浏览器点出来的，不是组件间 gRPC 调用，
        # source_component 留空——只有 gRPC 的 Render 才由调用方组件自报
        # 身份（contracts 的 RenderRequest.source_component 注释）。
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

    besdk.post(router, "/infra/print/render", "infra.print.render", render_handler)
    # preview 是编辑模板时的所见即所得校验，归编辑权限，不是查看权限
    # （契约本身没说，这是本组件自己的判断）。
    besdk.post(router, "/infra/print/templates/{id}/preview", "infra.print.template.edit", preview_handler)
    besdk.get(router, "/infra/print/templates", "infra.print.template.view", list_templates_handler)
    besdk.get(router, "/infra/print/templates/{id}", "infra.print.template.view", get_template_handler)
    besdk.put(router, "/infra/print/templates/{id}", "infra.print.template.edit", put_template_handler)
    besdk.post(router, "/infra/print/templates/{id}/rollback", "infra.print.template.edit", rollback_template_handler)

    return router
