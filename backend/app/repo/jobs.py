"""渲染记录——print_jobs，纯审计与排障，不存渲染结果字节（设计计划
§2）。记一条 job 与发 ``infra.print.rendered.v1`` 旁路事件必须在同一个
事务里（Outbox Pattern，设计计划 §3.10）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from besdk import Event, publish_outbox, with_tx

if TYPE_CHECKING:
    import asyncpg

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"


async def record_job_and_publish(
    pool: "asyncpg.Pool",
    role: str,
    schema: str,
    *,
    template_id: str,
    actor: str,
    source_component: str,
    status: str,
    error: str = "",
) -> int:
    async def _write(conn: "asyncpg.Connection") -> int:
        job_id: int = await conn.fetchval(
            "INSERT INTO print_jobs (template_id, actor, source_component, status, error) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            template_id,
            actor,
            source_component,
            status,
            error,
        )
        payload = json.dumps(
            {
                "job_id": str(job_id),
                "template_id": template_id,
                "actor": actor,
                "source_component": source_component,
                "success": status == STATUS_SUCCESS,
                "error": error,
            }
        ).encode("utf-8")
        ev = Event(subject="infra.print.rendered.v1", aggregate_id=str(job_id), version=1, payload=payload)
        await publish_outbox(conn, schema, ev)
        return job_id

    return await with_tx(pool, role, schema, _write)
