"""The print module: the user surface (templates, versions, preview), the system surface (RenderService.Render for
any component of the deployment), the bundled templates registered at start and the render workers stopped at stop."""
from __future__ import annotations

import hashlib
import json
import os
import re
from importlib.resources import as_file

from fastapi.responses import JSONResponse, Response

from besdk import authz, errs, lists
from besdk.http import Route
from besdk.system import ANY_COMPONENT, Method
from be.infra.print.v3 import print_pb2, print_pb2_grpc
from infra_print import OP_EDIT, OP_PREVIEW, OP_READ, OP_RENDER
from infra_print.renderer import Limits, Renderer
from infra_print.templates import FONTS, Store, register

# Every render's PDF creation date: fixed, so the same version and data give the same bytes (docs/design.md).
TIMESTAMP = 946684800  # 2000-01-01T00:00:00Z
MAX_DATA = 1 << 20

_TEMPLATE = re.compile(r"[a-z][a-z0-9_]{0,63}")

TEMPLATE_LIST = lists.Spec(OP_READ, "t", "template", lists.TEXT,
                           [lists.Column("template", "template", lists.TEXT, sortable=True)])
VERSION_LIST = lists.Spec(OP_READ, "v", "version", lists.DECIMAL,
                          [lists.Column("version", "version", lists.DECIMAL, sortable=True),
                           lists.Column("template", "template", lists.TEXT, filterable=True)])
TEMPLATE_COLS = "t.template, t.name, t.engine, t.current_version, t.state"
VERSION_COLS = "v.template, v.version, v.digest, v.origin, v.state, v.data_schema::text AS data_schema, v.created_at"


def _template_out(r) -> dict:
    return {"template": r["template"], "name": r["name"], "engine": r["engine"], "current_version": r["current_version"],
            "state": r["state"]}


def _version_out(r) -> dict:
    return {"template": r["template"], "version": r["version"], "digest": r["digest"], "origin": r["origin"],
            "state": r["state"], "data_schema": json.loads(r["data_schema"]),
            "created_at": r["created_at"].strftime("%Y-%m-%dT%H:%M:%S.%fZ")}


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

    async def render(self, template: str, version: int, data: bytes, copy: bool, preview: bool) -> tuple[bytes, object]:
        """Validates the view model against the version's schema, then renders it; answers the PDF and the version."""
        v = await self.store.version(template, version)
        if len(data) > MAX_DATA:
            raise errs.new("REQUEST_INVALID", "the data is larger than 1 MiB")
        try:
            model = json.loads(data)
        except ValueError:
            raise errs.new("REQUEST_INVALID", "the data is not JSON") from None
        v.validate(model)
        pdf = await self.renderer.render(v.content, {"data": json.dumps(model, ensure_ascii=False, sort_keys=True),
                                                     "copy": "true" if copy else "false",
                                                     "preview": "true" if preview else "false"}, TIMESTAMP)
        return pdf, v

    # ------------------------------------------------------------------ system surface

    def services(self, s) -> None:
        s.register(print_pb2_grpc.add_RenderServiceServicer_to_server, self,
                   {"Render": Method(OP_RENDER, [ANY_COMPONENT])})

    async def Render(self, request, context):  # noqa: N802 - the generated servicer's name
        if request.format not in (print_pb2.FORMAT_PDF, print_pb2.FORMAT_UNSPECIFIED):
            raise errs.new("CAPABILITY_UNSUPPORTED", "only PDF is rendered in this version of infra/print")
        if not _TEMPLATE.fullmatch(request.template) or request.version < 0:
            raise errs.new("REQUEST_INVALID", "template is a template's name and version 0 (current) or more")
        pdf, v = await self.render(request.template, request.version, request.data, request.copy, False)
        return print_pb2.RenderResponse(content=pdf, media_type="application/pdf", template=v.template, version=v.version,
                                        version_digest=v.digest, content_digest=hashlib.sha256(pdf).hexdigest())

    # ------------------------------------------------------------------ user surface

    def routes(self, r) -> None:
        r.add(Route("GET", "/api/print/templates", self.list_templates, OP_READ))
        r.add(Route("GET", "/api/print/templates/{template}/versions", self.list_versions, OP_READ))
        r.add(Route("POST", "/api/print/templates/{template}/versions", self.new_version, OP_EDIT))
        r.add(Route("GET", "/api/print/templates/{template}/versions/{version}", self.get_version, OP_READ))
        r.add(Route("POST", "/api/print/templates/{template}/versions/{version}/preview", self.preview, OP_PREVIEW,
                    orchestration=True))

    @staticmethod
    def _path(c) -> tuple[str, int]:
        template, version = c.param("template"), c.param("version")
        if not _TEMPLATE.fullmatch(template) or (version and not (version.isdigit() and int(version) >= 1)):
            raise errs.new("NOT_FOUND", "no such template version")
        return template, int(version or 0)

    async def _page(self, c, spec: lists.Spec, req: lists.Request, table: str, cols: str, out, keys):
        req.limit, req.cursor = lists.parse_limit(c.query("limit")), c.query("cursor")
        q = lists.build(authz.subject(), spec, req)
        return await lists.page(self.rt.db, q, table, cols, out, keys)

    async def list_templates(self, c):
        page, nxt = await self._page(c, TEMPLATE_LIST, lists.Request(sort=[lists.Order("template")]), "template",
                                     TEMPLATE_COLS, _template_out, lambda t: ([t["template"]], t["template"]))
        return JSONResponse({"templates": page, "next_cursor": nxt})

    async def list_versions(self, c):
        template, _ = self._path(c)
        page, nxt = await self._page(c, VERSION_LIST, lists.Request(sort=[lists.Order("version", desc=True)],
                                                                    filters=[lists.Filter("template", "eq", [template])]),
                                     "template_version", VERSION_COLS, _version_out,
                                     lambda v: ([str(v["version"])], str(v["version"])))
        return JSONResponse({"versions": page, "next_cursor": nxt})

    async def get_version(self, c):
        template, version = self._path(c)
        authz.subject().check(OP_READ, {})

        async def read(tx):
            return await tx.fetchrow(f"SELECT {VERSION_COLS} FROM template_version v WHERE v.template = $1 AND v.version = $2",
                                     template, version)
        row = await self.rt.db.tx(read)
        if row is None:
            raise errs.new("NOT_FOUND", "no such template version")
        return JSONResponse(_version_out(row))

    async def new_version(self, c):
        self._path(c)
        authz.subject().check(OP_EDIT, {})
        raise errs.new("CAPABILITY_UNSUPPORTED", "editing templates online is not in this version of infra/print")

    async def preview(self, c):
        """Renders a version with the caller's sample data, marked as a preview."""
        template, version = self._path(c)
        authz.subject().check(OP_PREVIEW, {})
        pdf, _ = await self.render(template, version, await c.body(), False, True)
        return Response(pdf, media_type="application/pdf")
