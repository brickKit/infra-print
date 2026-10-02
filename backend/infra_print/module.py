"""create_module——本组件唯一的装配入口，签名固定为
``async def create_module(rt: Runtime) -> Module``。独立运行
（``run_standalone``）与进外壳（be/py-render）走同一份代码：模块只交回零件
（ASGI app、gRPC 注册函数、后台循环），不监听端口、不开连接池、不初始化
OTel、不装信号处理器，配置只从 ``rt.config`` 读。

迁移不挂在 Module 上：brickKit 用本组件自己的镜像跑 ``migration.command``
（``infra_print.migrate``），外壳镜像里没有本组件的 ``migrations/`` 目录。
"""

from __future__ import annotations

import asyncio

from besdk import Module, Runtime, new_fastapi_app, start_outbox_pump

from infra_print import partition
from infra_print.grpc.server import register as register_grpc
from infra_print.http.routes import new_router
from infra_print.service.print_service import PrintService


async def create_module(rt: Runtime) -> Module:
    schema = rt.config.string_or("PG_SCHEMA", "infra_print")
    role = f"{schema}_rw"  # 外壳以 shell_py_render 登录，每个事务 SET LOCAL ROLE 到这里

    svc = PrintService(rt.db, role, schema, rt.logger)

    app = new_fastapi_app(rt)
    app.include_router(new_router(svc))

    async def _start() -> None:
        # 三个后台循环并发跑：Outbox 推送、event_outbox 周分区、print_jobs 月分区。
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
        start=_start,
        stop=None,  # 三个后台循环收到 CancelledError 就退出，不需要另外收尾
    )
