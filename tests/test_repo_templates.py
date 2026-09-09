"""app.repo.templates——真实 Postgres，走 infra_print/infra_print_rw
（真机迁移已跑过，见 migrations/001_create_print.sql）。
"""

from __future__ import annotations

import asyncpg
import pytest

from app.repo.templates import (
    TemplateVersionNotFoundError,
    batch_get_templates,
    get_template,
    list_templates,
    put_template,
    rollback_template,
)
from tests.helpers import dsn, unique

_ROLE = "infra_print_rw"
_SCHEMA = "infra_print"


@pytest.mark.asyncio
async def test_put_template创建新模板_get_template能读到() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("tpl")
    try:
        created = await put_template(pool, _ROLE, _SCHEMA, template_id, "送货单", "PDF", "<h1>{{ name }}</h1>")
        assert created.id == template_id
        assert created.version == 1
        assert created.enabled is True

        fetched = await get_template(pool, _ROLE, _SCHEMA, template_id)
        assert fetched is not None
        assert fetched.content == "<h1>{{ name }}</h1>"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_get_template查不到返回None() -> None:
    pool = await asyncpg.create_pool(dsn())
    try:
        result = await get_template(pool, _ROLE, _SCHEMA, unique("missing"))
        assert result is None
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_put_template第二次调用递增版本号并追加历史记录() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("tpl")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "v1", "PDF", "content-v1")
        second = await put_template(pool, _ROLE, _SCHEMA, template_id, "v2", "PDF", "content-v2")
        assert second.version == 2
        assert second.content == "content-v2"

        rolled_back = await rollback_template(pool, _ROLE, _SCHEMA, template_id, 1)
        # 回滚 = 把历史版本复制成新的当前版本，版本号继续往前走（3），
        # 不是把 version 倒退回 1——设计计划 §2 的既有判据。
        assert rolled_back.version == 3
        assert rolled_back.content == "content-v1"

        current = await get_template(pool, _ROLE, _SCHEMA, template_id)
        assert current.content == "content-v1"
        assert current.version == 3
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_rollback不存在的版本报错() -> None:
    pool = await asyncpg.create_pool(dsn())
    template_id = unique("tpl")
    try:
        await put_template(pool, _ROLE, _SCHEMA, template_id, "v1", "PDF", "content-v1")
        with pytest.raises(TemplateVersionNotFoundError):
            await rollback_template(pool, _ROLE, _SCHEMA, template_id, 99)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_rollback从未存在过的模板报的是版本不存在() -> None:
    """⚠️ 真机验证过：一个从未 put_template 过的 template_id 走
    rollback_template 时，第一步查 print_template_versions 就已经查不到
    任何行——报的是 ``TemplateVersionNotFoundError``，不是
    ``TemplateNotFoundError``。后者只在"print_template_versions 有这一
    版、但 print_templates 那一行没了"时才会触发，而 FK 约束（
    ``print_template_versions.template_id REFERENCES print_templates``)
    加上本组件目前没有删除模板的功能，让这条分支事实上不可达——保留它是
    防御性写法（万一将来加了删除模板），不是这里能测出来的路径。
    """
    pool = await asyncpg.create_pool(dsn())
    try:
        with pytest.raises(TemplateVersionNotFoundError):
            await rollback_template(pool, _ROLE, _SCHEMA, unique("missing"), 1)
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_batch_get_templates按需返回_查不到的id省略() -> None:
    pool = await asyncpg.create_pool(dsn())
    id_a, id_b, missing = unique("a"), unique("b"), unique("missing")
    try:
        await put_template(pool, _ROLE, _SCHEMA, id_a, "A", "PDF", "content-a")
        await put_template(pool, _ROLE, _SCHEMA, id_b, "B", "ZPL", "content-b")

        result = await batch_get_templates(pool, _ROLE, _SCHEMA, [id_a, missing, id_b])
        assert {t.id for t in result} == {id_a, id_b}
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_batch_get_templates空列表短路返回空() -> None:
    pool = await asyncpg.create_pool(dsn())
    try:
        result = await batch_get_templates(pool, _ROLE, _SCHEMA, [])
        assert result == []
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_list_templates按channel筛选并支持游标分页() -> None:
    """⚠️ 真机验证过一个真实的测试隔离坑：``print_templates`` 是全表共享
    （不像 outbox 测试那样能挑一个中立 schema 躲开），同一次 pytest 会话
    里其他测试建的模板（甚至历史运行留下的旧数据）字典序可能落在这次
    生成的 ``ids`` 中间，直接断言 ``page1 == ids[:2]`` 会被"谁先跑"污染。
    改成拿到全量结果后按自己的 id 集合过滤，只断言"筛选/排序/游标排他
    性"这三件事，不假设自己是表里唯一的数据。
    """
    pool = await asyncpg.create_pool(dsn())
    prefix = unique("list")
    ids = [f"{prefix}-{i}" for i in range(3)]
    try:
        for tid in ids:
            await put_template(pool, _ROLE, _SCHEMA, tid, tid, "PDF", "content")
        # 混一个不同 channel 的模板，验证筛选真的生效。
        await put_template(pool, _ROLE, _SCHEMA, f"{prefix}-zpl", "zpl-one", "ZPL", "^XA^XZ")

        # page_size 给够大，覆盖测试期间表里可能存在的全部数据，再筛出
        # 属于本次测试自己的那几条。
        all_pdf, _ = await list_templates(pool, _ROLE, _SCHEMA, "PDF", "", 200)
        ours = [t.id for t in all_pdf if t.id in set(ids)]
        assert ours == ids, "PDF 筛选应该拿到自己建的三条，顺序按 id 字典序"

        all_zpl, _ = await list_templates(pool, _ROLE, _SCHEMA, "ZPL", "", 200)
        assert f"{prefix}-zpl" in {t.id for t in all_zpl}
        assert f"{prefix}-zpl" not in {t.id for t in all_pdf}, "channel 筛选不该漏过滤"

        # 游标排他性：传 ids[0] 当 cursor，结果里不该再出现它自己。
        after_first, _ = await list_templates(pool, _ROLE, _SCHEMA, "PDF", ids[0], 200)
        ours_after = [t.id for t in after_first if t.id in set(ids)]
        assert ours_after == ids[1:], "cursor 必须是排他的（id > cursor，不含 cursor 本身）"
    finally:
        await pool.close()
