"""create_module——唯一的装配入口（全局约束 §K、设计书 §12.5、§13.3
铁律七）。签名一个字都不许改：``async def create_module(rt: Runtime) ->
Module``——单跑与合并走同一份代码，模块只交回零件（ASGI app、gRPC 注册
函数、迁移目录、后台循环），不 Listen、不开池、不 init OTel、不装信号
处理器（总纲 §00 "阶段三 Task 1 后续补记"：Python 模块入口契约）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from besdk import Module, Runtime, new_fastapi_app, start_outbox_pump

from app import partition
from app.grpc.server import register as register_grpc
from app.http.routes import new_router
from app.service.print_service import PrintService

# ⚠️ 相对 CWD，不是相对 __file__：同 ``besdk.manifest.load_own_ports``
# 读 "component.yaml" 的既有约定（run_standalone 里那一行）——brickkit
# 启动进程时 CWD 就是组件自己的根目录。用 __file__ 反推会在
# ``pip install .``（非 editable）之后失真：那样 app 包被复制进
# site-packages，__file__ 往上三层再也不是仓库根目录。Dockerfile 里
# WORKDIR 就是仓库根，这里与本地开发（CWD 同样是仓库根）自然一致。
_MIGRATIONS_DIR = Path("migrations")


async def create_module(rt: Runtime) -> Module:
    schema = rt.config.string_or("pgSchema", "infra_print")
    role = f"{schema}_rw"

    svc = PrintService(rt.db, role, schema, rt.logger)

    app = new_fastapi_app(rt)
    app.include_router(new_router(svc))

    async def _start() -> None:
        # 三个后台循环必须并发跑，不能顺序调用：Outbox 推送 +
        # event_outbox 周分区维护 + print_jobs 月分区维护（同
        # infra-notification module.go 的既有并发结构）。
        tasks = [
            asyncio.create_task(start_outbox_pump(rt.db, schema, rt.nats)),
            asyncio.create_task(partition.start_weekly(rt.db, role, schema, rt.logger)),
            asyncio.create_task(partition.start_monthly(rt.db, role, schema, rt.logger)),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise

    return Module(
        asgi_app=app,
        register_grpc=register_grpc(svc),
        migrations_dir=_MIGRATIONS_DIR,
        start=_start,
        stop=None,  # 三个后台循环靠 asyncio.CancelledError 退出，无需额外收尾
    )
