"""app.partition——真机验证分区维护真的建出未来分区，且重复调用幂等
（``to_regclass`` 判存在，不是"先建、报错就忽略"）。用真实
infra_print/infra_print_rw，表结构与真实迁移一致。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.partition import _ensure_all_monthly, _ensure_all_weekly, _monday_of
from tests.helpers import dsn

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"
_logger = logging.getLogger("test_partition")


@pytest.mark.asyncio
async def test_ensure_all_weekly建出未来周分区且重复调用幂等() -> None:
    pool = await asyncpg.create_pool(dsn())
    try:
        await _ensure_all_weekly(pool, _ROLE, _SCHEMA)
        # 当前周应该总是存在——不管现在是哪一周，起始分区名字必然可算出来。
        week_start = _monday_of(datetime.now(UTC))
        name = f"event_outbox_{week_start:%Y_%m_%d}"
        exists = await pool.fetchval(f"SELECT to_regclass('{_SCHEMA}.{name}') IS NOT NULL")
        assert exists is True

        # 未来第 4 周（lookahead 边界）也该建出来。
        future = week_start + timedelta(days=7 * 4)
        future_name = f"event_outbox_{future:%Y_%m_%d}"
        future_exists = await pool.fetchval(f"SELECT to_regclass('{_SCHEMA}.{future_name}') IS NOT NULL")
        assert future_exists is True

        # 重复调用不报错（幂等，to_regclass 判存在）。
        await _ensure_all_weekly(pool, _ROLE, _SCHEMA)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_ensure_all_monthly建出未来月分区且重复调用幂等() -> None:
    pool = await asyncpg.create_pool(dsn())
    try:
        await _ensure_all_monthly(pool, _ROLE, _SCHEMA)
        now = datetime.now(UTC)
        name = f"print_jobs_{now:%Y_%m}_01"
        exists = await pool.fetchval(f"SELECT to_regclass('{_SCHEMA}.{name}') IS NOT NULL")
        assert exists is True

        await _ensure_all_monthly(pool, _ROLE, _SCHEMA)
    finally:
        await pool.close()
