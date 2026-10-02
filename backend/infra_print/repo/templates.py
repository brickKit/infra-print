"""模板存储与版本管理——print_templates（当前版本）/
print_template_versions（历史，只增不改，设计计划 §2）。

⚠️ ``put_template``/``rollback_template`` 的写入顺序不能颠倒：必须先
upsert ``print_templates``（父行），再 insert ``print_template_versions``
——后者有 ``REFERENCES print_templates (id)`` 的外键，新模板第一次保存
时如果反过来写会撞外键违例（这张表此时还没有这一行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from besdk import with_tx

if TYPE_CHECKING:
    import asyncpg


@dataclass
class Template:
    id: str
    name: str
    channel: str
    content: str
    version: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class TemplateNotFoundError(Exception):
    def __init__(self, template_id: str) -> None:
        super().__init__(f"模板不存在：{template_id}")
        self.template_id = template_id


class TemplateVersionNotFoundError(Exception):
    def __init__(self, template_id: str, version: int) -> None:
        super().__init__(f"模板 {template_id} 没有版本 {version}")
        self.template_id = template_id
        self.version = version


@dataclass
class TemplateVersion:
    version: int
    name: str
    enabled: bool  # 写入这个版本时模板的启用状态
    created_at: datetime


_SELECT_COLUMNS = "id, name, channel, content, version, enabled, created_at, updated_at"
_INSERT_VERSION = (
    "INSERT INTO print_template_versions (template_id, name, channel, content, version, enabled) "
    "VALUES ($1, $2, $3, $4, $5, $6)"
)


def _row_to_template(row: "asyncpg.Record") -> Template:
    return Template(
        id=row["id"],
        name=row["name"],
        channel=row["channel"],
        content=row["content"],
        version=row["version"],
        enabled=row["enabled"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def get_template(pool: "asyncpg.Pool", role: str, schema: str, template_id: str) -> Template | None:
    async def _read(conn: "asyncpg.Connection") -> Template | None:
        row = await conn.fetchrow(f"SELECT {_SELECT_COLUMNS} FROM print_templates WHERE id = $1", template_id)
        return _row_to_template(row) if row else None

    return await with_tx(pool, role, schema, _read)


async def batch_get_templates(pool: "asyncpg.Pool", role: str, schema: str, template_ids: list[str]) -> list[Template]:
    """防 N+1 的唯一合法批量读方式（§3.8）。查不到的 id 直接省略。"""
    if not template_ids:
        return []

    async def _read(conn: "asyncpg.Connection") -> list[Template]:
        rows = await conn.fetch(
            f"SELECT {_SELECT_COLUMNS} FROM print_templates WHERE id = ANY($1::text[])", template_ids
        )
        return [_row_to_template(r) for r in rows]

    # 契约要求顺序与 template_ids 一致；SQL 的返回顺序由执行计划决定，在这里按请求排回去。
    by_id = {t.id: t for t in await with_tx(pool, role, schema, _read)}
    return [by_id[i] for i in dict.fromkeys(template_ids) if i in by_id]


async def list_templates(
    pool: "asyncpg.Pool", role: str, schema: str, channel: str | None, cursor: str, page_size: int
) -> tuple[list[Template], str]:
    """⚠️ 禁止深 Offset（决策 53）——游标是 ``id`` 本身（TEXT 主键，字典序
    比较），不是数字自增 id，但判据完全一样：``id > cursor``。
    """
    page_size = page_size if 0 < page_size <= 200 else 50

    async def _read(conn: "asyncpg.Connection") -> list[Template]:
        query = f"SELECT {_SELECT_COLUMNS} FROM print_templates WHERE id > $1"
        args: list[object] = [cursor or ""]
        if channel:
            args.append(channel)
            query += f" AND channel = ${len(args)}"
        args.append(page_size + 1)
        query += f" ORDER BY id LIMIT ${len(args)}"
        rows = await conn.fetch(query, *args)
        return [_row_to_template(r) for r in rows]

    templates = await with_tx(pool, role, schema, _read)
    next_cursor = ""
    if len(templates) > page_size:
        templates = templates[:page_size]
        next_cursor = templates[-1].id
    return templates, next_cursor


async def put_template(
    pool: "asyncpg.Pool",
    role: str,
    schema: str,
    template_id: str,
    name: str,
    channel: str,
    content: str,
    *,
    enabled: bool = True,
) -> Template:
    """上传新版本——同一个事务里：算下一个版本号 → upsert 当前版本 →
    追加一条历史版本记录。两张表在同一个事务里一起改，不会出现"当前版本
    已更新但历史记录没写"这种半成品状态。
    """

    async def _write(conn: "asyncpg.Connection") -> Template:
        next_version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM print_template_versions WHERE template_id = $1",
            template_id,
        )
        row = await conn.fetchrow(
            f"""
            INSERT INTO print_templates (id, name, channel, content, version, enabled, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, channel = EXCLUDED.channel, content = EXCLUDED.content,
                version = EXCLUDED.version, enabled = EXCLUDED.enabled, updated_at = now()
            RETURNING {_SELECT_COLUMNS}
            """,
            template_id,
            name,
            channel,
            content,
            next_version,
            enabled,
        )
        await conn.execute(_INSERT_VERSION, template_id, name, channel, content, next_version, enabled)
        return _row_to_template(row)

    return await with_tx(pool, role, schema, _write)


async def rollback_template(pool: "asyncpg.Pool", role: str, schema: str, template_id: str, target_version: int) -> Template:
    """回滚 = 把某个历史版本复制成新的当前版本，不是删掉新版本（设计
    计划 §2）——读目标版本与写新版本在同一个事务里，避免"读的时候还在、
    写的时候被别的请求改没了"这类竞态（虽然本组件的模板管理不是高并发
    场景，但同一份代码在合并部署形态下没有理由降低这层保证）。
    """

    async def _write(conn: "asyncpg.Connection") -> Template:
        old = await conn.fetchrow(
            "SELECT name, channel, content FROM print_template_versions WHERE template_id = $1 AND version = $2",
            template_id,
            target_version,
        )
        if old is None:
            raise TemplateVersionNotFoundError(template_id, target_version)

        next_version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM print_template_versions WHERE template_id = $1",
            template_id,
        )
        row = await conn.fetchrow(
            f"""
            UPDATE print_templates SET
                name = $2, channel = $3, content = $4, version = $5, updated_at = now()
            WHERE id = $1
            RETURNING {_SELECT_COLUMNS}
            """,
            template_id,
            old["name"],
            old["channel"],
            old["content"],
            next_version,
        )
        if row is None:
            raise TemplateNotFoundError(template_id)
        # 回滚不改启用状态：新版本记下的是回滚后模板的状态
        await conn.execute(
            _INSERT_VERSION, template_id, old["name"], old["channel"], old["content"], next_version, row["enabled"]
        )
        return _row_to_template(row)

    return await with_tx(pool, role, schema, _write)


async def list_template_versions(
    pool: "asyncpg.Pool", role: str, schema: str, template_id: str, before_version: int | None, page_size: int
) -> tuple[list[TemplateVersion], str]:
    """模板的历史版本，新的在前。``before_version`` 是游标（上一页最后一个版本号，不含），
    ``None`` 从最新版本开始。返回 (这一页, 下一页的游标)；没有更早的版本时游标为空串。
    模板不存在抛 ``TemplateNotFoundError``（区别于"存在但还没有历史"）。"""
    page_size = page_size if 0 < page_size <= 200 else 50

    async def _read(conn: "asyncpg.Connection") -> list[TemplateVersion]:
        if not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM print_templates WHERE id = $1)", template_id):
            raise TemplateNotFoundError(template_id)
        rows = await conn.fetch(
            "SELECT version, name, enabled, created_at FROM print_template_versions "
            "WHERE template_id = $1 AND ($2::int IS NULL OR version < $2) ORDER BY version DESC LIMIT $3",
            template_id,
            before_version,
            page_size + 1,
        )
        return [TemplateVersion(r["version"], r["name"], r["enabled"], r["created_at"]) for r in rows]

    versions = await with_tx(pool, role, schema, _read)
    if len(versions) > page_size:
        versions = versions[:page_size]
        return versions, str(versions[-1].version)
    return versions, ""
