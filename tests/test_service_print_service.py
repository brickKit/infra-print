"""app.service.print_service——端到端：真实 Postgres + 真实 WeasyPrint
渲染，验证中文内容不是乱码（同本次实现前用一次性容器实测过的
fonts-noto-cjk 结论，host 机器已装同一套字体，见 AGENTS.md）。
"""

from __future__ import annotations

import logging
import subprocess

import asyncpg
import pytest

_logger = logging.getLogger("test_service_print_service")

from app.repo.templates import put_template
from app.service.print_service import (
    PrintService,
    RenderError,
    TemplateDisabledError,
    TemplateNotFoundError,
)
from tests.helpers import dsn, unique

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"

_PDF_TEMPLATE = """
<html><head><style>
body { font-family: "Noto Sans CJK SC", sans-serif; }
</style></head>
<body>
<h1>测试送货单 Test Delivery Note</h1>
<table>
{% for item in items %}
<tr><td>{{ item.name }}</td><td>{{ item.qty }}</td></tr>
{% endfor %}
</table>
</body></html>
"""

_ZPL_TEMPLATE = "^XA^FO50,50^A0N,50,50^FD{{ label }}^FS^XZ"


def _pdf_to_text(pdf_bytes: bytes) -> str:
    result = subprocess.run(  # noqa: S603 - pdftotext 是本机固定可执行文件，输入是我们自己刚生成的 PDF 字节
        ["/usr/bin/pdftotext", "-", "-"], input=pdf_bytes, capture_output=True, check=True
    )
    return result.stdout.decode("utf-8")


@pytest.mark.asyncio
async def test_render真实生成PDF_中文内容提取不乱码_并落print_jobs与outbox() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("pdf-tpl")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "送货单", "PDF", _PDF_TEMPLATE)
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)

        content, content_type = await svc.render(
            template_id,
            {"items": [{"name": "螺丝钉", "qty": 100}, {"name": "螺母", "qty": 50}]},
            actor="user-1",
            source_component="erp/sales",
        )

        assert content_type == "application/pdf"
        assert content[:4] == b"%PDF"
        text = _pdf_to_text(content)
        assert "测试送货单 Test Delivery Note" in text
        assert "螺丝钉" in text
        assert "螺母" in text

        job_row = await pool.fetchrow(
            f"SELECT status, actor, source_component FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
        )
        assert job_row["status"] == "SUCCESS"
        assert job_row["actor"] == "user-1"
        assert job_row["source_component"] == "erp/sales"

        outbox_row = await pool.fetchrow(
            f"SELECT subject FROM {_SCHEMA}.event_outbox WHERE aggregate_id = (SELECT id::text FROM {_SCHEMA}.print_jobs WHERE template_id = $1)",
            template_id,
        )
        assert outbox_row["subject"] == "infra.print.rendered.v1"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_render_ZPL通道返回纯文本指令() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("zpl-tpl")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "条码标签", "ZPL", _ZPL_TEMPLATE)
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)

        content, content_type = await svc.render(template_id, {"label": "SKU-001"}, actor="user-1")

        assert content_type == "text/plain"
        assert content == b"^XA^FO50,50^A0N,50,50^FDSKU-001^FS^XZ"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_render模板不存在报错并落一条FAILED_job() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("missing")
    try:
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)
        with pytest.raises(TemplateNotFoundError):
            await svc.render(template_id, {}, actor="user-1")

        job_row = await pool.fetchrow(
            f"SELECT status, error FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
        )
        assert job_row["status"] == "FAILED"
        assert job_row["error"] == "模板不存在"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_render模板已停用报错并落一条FAILED_job() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("disabled")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "停用模板", "PDF", "<p>x</p>", enabled=False)
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)

        with pytest.raises(TemplateDisabledError):
            await svc.render(template_id, {}, actor="user-1")

        job_row = await pool.fetchrow(
            f"SELECT status, error FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
        )
        assert job_row["status"] == "FAILED"
        assert job_row["error"] == "模板已停用"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_render模板语法错误抛RenderError并落FAILED_job() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("broken")
    try:
        # {% for %} 没有匹配的 {% endfor %}，Jinja2 编译期就会报错。
        await put_template(pool, _ROLE, _SCHEMA, template_id, "坏模板", "PDF", "{% for x in items %}")
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)

        with pytest.raises(RenderError):
            await svc.render(template_id, {"items": []}, actor="user-1")

        job_row = await pool.fetchrow(
            f"SELECT status FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
        )
        assert job_row["status"] == "FAILED"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_preview不写print_jobs() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("preview-tpl")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "预览模板", "ZPL", "^XA^FD{{ x }}^FS^XZ")
        svc = PrintService(pool, _ROLE, _SCHEMA, logger=_logger)

        content, content_type = await svc.preview(template_id, {"x": "hello"})
        assert content == b"^XA^FDhello^FS^XZ"
        assert content_type == "text/plain"

        count = await pool.fetchval(
            f"SELECT count(*) FROM {_SCHEMA}.print_jobs WHERE template_id = $1", template_id
        )
        assert count == 0
    finally:
        await pool.close()
