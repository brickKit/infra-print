"""The print module, assembly only: it holds the runtime, the version store and the render workers, registers the
user surface (templates and versions: templates.routes; the preview: rendering.render) and the system surface
(RenderService.Render for any component of the deployment: rendering.render), registers the bundled templates at
start (templates.bundled) and stops the workers at stop."""
from __future__ import annotations

import os
from importlib.resources import as_file

from besdk.http import Route
from besdk.system import ANY_COMPONENT, Method
from be.infra.print.v3 import print_pb2_grpc
from infra_print import OP_EDIT, OP_PREVIEW, OP_READ, OP_RENDER
from infra_print.rendering import render
from infra_print.rendering.pool import Limits, Renderer
from infra_print.templates import routes
from infra_print.templates.bundled import FONTS, register
from infra_print.templates.store import Store


class Print(print_pb2_grpc.RenderServiceServicer):
    def __init__(self, rt):
        self.rt, c = rt, rt.config
        self.store = Store(rt.db)
        self._fonts = as_file(FONTS)  # a real directory for the workers, also when the package is zipped
        self.limits = Limits(timeout=c.duration("PRINT_RENDER_TIMEOUT").total_seconds(),
                             memory=c.int("PRINT_RENDER_MEMORY_MIB") << 20, output=c.int("PRINT_RENDER_OUTPUT_KIB") << 10,
                             workers=c.int("PRINT_RENDER_WORKERS") or os.cpu_count() or 1,
                             queue_wait=c.duration("PRINT_RENDER_QUEUE_WAIT").total_seconds())
        self.renderer: Renderer | None = None

    async def start(self) -> None:
        self.renderer = Renderer(str(self._fonts.__enter__()), self.limits, self.rt.logger)
        await register(self.rt.db, self.rt.logger)

    async def stop(self) -> None:
        if self.renderer is not None:
            await self.renderer.close()
        self._fonts.__exit__(None, None, None)

    # ------------------------------------------------------------------ system surface

    def services(self, s) -> None:
        s.register(print_pb2_grpc.add_RenderServiceServicer_to_server, self,
                   {"Render": Method(OP_RENDER, [ANY_COMPONENT])})

    async def Render(self, request, context):  # noqa: N802 - the generated servicer's name
        return await render.render_call(self.store, self.renderer, request)

    # ------------------------------------------------------------------ user surface

    def routes(self, r) -> None:
        r.add(Route("GET", "/api/print/templates", self.list_templates, OP_READ))
        r.add(Route("GET", "/api/print/templates/{template}/versions", self.list_versions, OP_READ))
        r.add(Route("POST", "/api/print/templates/{template}/versions", self.new_version, OP_EDIT))
        r.add(Route("GET", "/api/print/templates/{template}/versions/{version}", self.get_version, OP_READ))
        r.add(Route("POST", "/api/print/templates/{template}/versions/{version}/preview", self.preview, OP_PREVIEW,
                    orchestration=True))

    async def list_templates(self, c):
        return await routes.list_templates(self.rt.db, c)

    async def list_versions(self, c):
        return await routes.list_versions(self.rt.db, c)

    async def get_version(self, c):
        return await routes.get_version(self.rt.db, c)

    async def new_version(self, c):
        return await routes.new_version(self.rt.db, c)

    async def preview(self, c):
        return await render.preview(self.store, self.renderer, c)
