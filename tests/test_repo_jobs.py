"""app.repo.jobs——record_job_and_publish 必须在同一个事务里落
print_jobs 与 event_outbox（Outbox Pattern，设计计划 §3.10）。真实
Postgres，本组件此时尚未部署，直接用自己的真实 schema 不会有真实容器
的 outbox pump 跟这里的断言竞态（同 be-sdk-python test_outbox.py 注释
里提到的风险——那里是因为测的是 SDK 本身、要避开所有真实组件；这里测
的就是这个组件自己，等它真的部署之后再跑这类测试要小心同一个风险）。
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from app.repo.jobs import STATUS_FAILED, STATUS_SUCCESS, record_job_and_publish
from tests.helpers import dsn, unique

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"


@pytest.mark.asyncio
async def test_record_job_and_publish成功路径写job与outbox() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("tpl")
    try:
        job_id = await record_job_and_publish(
            pool, _ROLE, _SCHEMA,
            template_id=template_id, actor="user-1", source_component="erp/sales",
            status=STATUS_SUCCESS,
        )

        job_row = await pool.fetchrow(
            f"SELECT template_id, actor, source_component, status, error FROM {_SCHEMA}.print_jobs WHERE id = $1",
            job_id,
        )
        assert job_row["template_id"] == template_id
        assert job_row["actor"] == "user-1"
        assert job_row["source_component"] == "erp/sales"
        assert job_row["status"] == "SUCCESS"
        assert job_row["error"] == ""

        outbox_row = await pool.fetchrow(
            f"SELECT subject, aggregate_id, payload FROM {_SCHEMA}.event_outbox WHERE aggregate_id = $1",
            str(job_id),
        )
        assert outbox_row["subject"] == "infra.print.rendered.v1"
        payload = json.loads(outbox_row["payload"])
        assert payload == {
            "job_id": str(job_id),
            "template_id": template_id,
            "actor": "user-1",
            "source_component": "erp/sales",
            "success": True,
            "error": "",
        }
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_record_job_and_publish失败路径记录错误原因() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("tpl")
    try:
        job_id = await record_job_and_publish(
            pool, _ROLE, _SCHEMA,
            template_id=template_id, actor="", source_component="",
            status=STATUS_FAILED, error="模板不存在",
        )

        job_row = await pool.fetchrow(f"SELECT status, error FROM {_SCHEMA}.print_jobs WHERE id = $1", job_id)
        assert job_row["status"] == "FAILED"
        assert job_row["error"] == "模板不存在"

        outbox_row = await pool.fetchrow(
            f"SELECT payload FROM {_SCHEMA}.event_outbox WHERE aggregate_id = $1", str(job_id)
        )
        payload = json.loads(outbox_row["payload"])
        assert payload["success"] is False
        assert payload["error"] == "模板不存在"
    finally:
        await pool.close()
