"""编排层：REST 与 gRPC 共用同一套业务逻辑（同 be-sdk-go 组件的既有
判据）。⚠️ 这一层只做"模板存不存在/启不启用"这类结构性校验，不做任何
业务规则判断——本组件是纯函数，不理解调用方传来的 data 是什么业务对象
（设计计划 §6.11）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.render import get_renderer
from app.repo import jobs as jobs_repo
from app.repo import templates as templates_repo
from app.repo.templates import Template, TemplateNotFoundError, TemplateVersionNotFoundError

if TYPE_CHECKING:
    import asyncpg
    from asyncpg import Pool


class TemplateDisabledError(Exception):
    def __init__(self, template_id: str) -> None:
        super().__init__(f"模板已停用：{template_id}")
        self.template_id = template_id


class RenderError(Exception):
    pass


class PrintService:
    def __init__(self, pool: "Pool", role: str, schema: str, logger: object) -> None:
        self._pool = pool
        self._role = role
        self._schema = schema
        self._logger = logger

    async def render(self, template_id: str, data: dict, *, actor: str = "", source_component: str = "") -> tuple[bytes, str]:
        """真正落 print_jobs + 发 infra.print.rendered.v1 的正式渲染路径
        （§3 契约表里的 ``Render`` rpc、``POST /render``）。
        """
        template = await templates_repo.get_template(self._pool, self._role, self._schema, template_id)
        if template is None:
            await self._record(template_id, actor, source_component, ok=False, error="模板不存在")
            raise TemplateNotFoundError(template_id)
        if not template.enabled:
            await self._record(template_id, actor, source_component, ok=False, error="模板已停用")
            raise TemplateDisabledError(template_id)

        try:
            content, content_type = self._do_render(template, data)
        except Exception as exc:  # noqa: BLE001 - 渲染失败要落 job 记录原因，再原样抛给调用方
            await self._record(template_id, actor, source_component, ok=False, error=str(exc))
            msg = f"渲染失败：{exc}"
            raise RenderError(msg) from exc

        await self._record(template_id, actor, source_component, ok=True)
        return content, content_type

    async def preview(self, template_id: str, data: dict) -> tuple[bytes, str]:
        """用样例数据预览——不写 print_jobs（设计计划 §3：不算一次正式
        渲染），所以不经过 ``_record``。
        """
        template = await templates_repo.get_template(self._pool, self._role, self._schema, template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        if not template.enabled:
            raise TemplateDisabledError(template_id)
        try:
            return self._do_render(template, data)
        except Exception as exc:  # noqa: BLE001 - 预览失败原样抛给调用方，不落审计记录
            msg = f"渲染失败：{exc}"
            raise RenderError(msg) from exc

    def _do_render(self, template: Template, data: dict) -> tuple[bytes, str]:
        renderer = get_renderer(template.channel)
        content = renderer.render(template.content, data)
        return content, renderer.content_type

    async def _record(self, template_id: str, actor: str, source_component: str, *, ok: bool, error: str = "") -> None:
        await jobs_repo.record_job_and_publish(
            self._pool,
            self._role,
            self._schema,
            template_id=template_id,
            actor=actor,
            source_component=source_component,
            status=jobs_repo.STATUS_SUCCESS if ok else jobs_repo.STATUS_FAILED,
            error=error,
        )

    async def batch_get_templates(self, template_ids: list[str]) -> list[Template]:
        return await templates_repo.batch_get_templates(self._pool, self._role, self._schema, template_ids)

    async def list_templates(self, channel: str | None, cursor: str, page_size: int) -> tuple[list[Template], str]:
        return await templates_repo.list_templates(self._pool, self._role, self._schema, channel, cursor, page_size)

    async def get_template(self, template_id: str) -> Template:
        template = await templates_repo.get_template(self._pool, self._role, self._schema, template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return template

    async def put_template(self, template_id: str, name: str, channel: str, content: str, *, enabled: bool = True) -> Template:
        return await templates_repo.put_template(
            self._pool, self._role, self._schema, template_id, name, channel, content, enabled=enabled
        )

    async def rollback_template(self, template_id: str, target_version: int) -> Template:
        return await templates_repo.rollback_template(self._pool, self._role, self._schema, template_id, target_version)


__all__ = [
    "PrintService",
    "RenderError",
    "TemplateDisabledError",
    "TemplateNotFoundError",
    "TemplateVersionNotFoundError",
]
