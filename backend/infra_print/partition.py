"""Module.start 的后台循环之一：为 event_outbox 建未来周分区、
print_jobs 建未来月分区。移植自 Go 版 partition.go/monthly.go（
infra-notification/erp-inventory 的既有实现），边界算法逐字对应，只是
语言从 Go 换成 Python：

- ``to_regclass`` 判存在、``CREATE TABLE ... PARTITION OF`` 建分区，不
  反过来"先建、报 already exists 就忽略"——一条语句真的执行失败会让
  整个事务 aborted。
- 边界锚点固定在 UTC 周一 00:00 / 月初 00:00，不是"从现在起 N 天"的
  滑动窗口，否则相邻两次检查算出来的边界会对不上。
- 建分区不需要额外 ``ALTER TABLE ... OWNER TO``：``with_tx`` 已经
  ``SET LOCAL ROLE`` 过，``CREATE TABLE`` 建出来的所有者就是当前角色
  本身（真机验证过的既有结论，同 infra-notification monthly.go 的
  注释）——本组件目前没有专门为这一条在 Python/asyncpg 下重新验证，
  沿用 Go 版已确认的 PostgreSQL 行为（与驱动语言无关）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from besdk import with_tx

if TYPE_CHECKING:
    import asyncpg

_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_LOOKAHEAD_WEEKS = 4  # 提前建好当前周 + 未来 4 周
_LOOKAHEAD_MONTHS = 3  # 提前建好当前月 + 未来 3 个月

_WEEKLY_TABLES = ("event_outbox",)
_MONTHLY_TABLES = ("print_jobs",)


def _monday_of(dt: datetime) -> datetime:
    d = datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
    return d - timedelta(days=d.isoweekday() - 1)


def _first_of_month(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, tzinfo=UTC)


def _add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    return datetime(dt.year + month_index // 12, month_index % 12 + 1, 1, tzinfo=UTC)


async def _ensure_partition(conn: "asyncpg.Connection", table: str, name: str, start: datetime, end: datetime) -> None:
    exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)
    if exists:
        return
    # table 只来自本模块内固定清单，start/end 是格式化过的日期字符串，
    # 都不是外部输入，拼 SQL 是安全的（同 Go 版 ensurePartition 注释）。
    await conn.execute(
        f"CREATE TABLE {name} PARTITION OF {table} FOR VALUES FROM ('{start:%Y-%m-%d}') TO ('{end:%Y-%m-%d}')"
    )


async def _ensure_all_weekly(pool: "asyncpg.Pool", role: str, schema: str) -> None:
    async def _write(conn: "asyncpg.Connection") -> None:
        week_start = _monday_of(datetime.now(UTC))
        for i in range(_LOOKAHEAD_WEEKS + 1):
            start = week_start + timedelta(days=7 * i)
            end = start + timedelta(days=7)
            for table in _WEEKLY_TABLES:
                await _ensure_partition(conn, table, f"{table}_{start:%Y_%m_%d}", start, end)

    await with_tx(pool, role, schema, _write)


async def _ensure_all_monthly(pool: "asyncpg.Pool", role: str, schema: str) -> None:
    async def _write(conn: "asyncpg.Connection") -> None:
        month_start = _first_of_month(datetime.now(UTC))
        for i in range(_LOOKAHEAD_MONTHS + 1):
            start = _add_months(month_start, i)
            end = _add_months(month_start, i + 1)
            for table in _MONTHLY_TABLES:
                await _ensure_partition(conn, table, f"{table}_{start:%Y_%m}_01", start, end)

    await with_tx(pool, role, schema, _write)


async def start_weekly(pool: "asyncpg.Pool", role: str, schema: str, logger: logging.Logger) -> None:
    """周分区维护——event_outbox。立刻检查一次，之后每 24 小时检查一次；
    单次失败只记日志，不让循环退出（``asyncio.CancelledError`` 必须
    原样返回，不能被下面的 ``except Exception`` 吞掉——同 be-sdk-python
    outbox.py 的 ``start_outbox_pump`` 既有写法）。
    """
    while True:
        try:
            await _ensure_all_weekly(pool, role, schema)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("周分区维护失败")
        try:
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return


async def start_monthly(pool: "asyncpg.Pool", role: str, schema: str, logger: logging.Logger) -> None:
    """月分区维护——print_jobs。"""
    while True:
        try:
            await _ensure_all_monthly(pool, role, schema)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("月分区维护失败")
        try:
            await asyncio.sleep(_CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return
