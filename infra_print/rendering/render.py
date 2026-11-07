"""A render: the view model checked against the version's schema, then rendered by the pool with the PDF's fixed
creation date (the same version and data give the same bytes, docs/design.md); the system surface's
RenderService.Render for any component of the deployment, and the user surface's preview."""
from __future__ import annotations

import hashlib
import json

from fastapi.responses import Response

from besdk import authz, errs
from be.infra.print.v3 import print_pb2
from infra_print import OP_PREVIEW
from infra_print.rendering.pool import Renderer
from infra_print.templates.routes import TEMPLATE_NAME, path
from infra_print.templates.store import Store

# Every render's PDF creation date: fixed, so the same version and data give the same bytes (docs/design.md).
TIMESTAMP = 946684800  # 2000-01-01T00:00:00Z
MAX_DATA = 1 << 20


async def render(store: Store, renderer: Renderer, template: str, version: int, data: bytes, copy: bool,
                 preview: bool) -> tuple[bytes, object]:
    """Validates the view model against the version's schema, then renders it; answers the PDF and the version."""
    v = await store.version(template, version)
    if len(data) > MAX_DATA:
        raise errs.new("REQUEST_INVALID", "the data is larger than 1 MiB")
    try:
        model = json.loads(data)
    except ValueError:
        raise errs.new("REQUEST_INVALID", "the data is not JSON") from None
    v.validate(model)
    pdf = await renderer.render(v.content, {"data": json.dumps(model, ensure_ascii=False, sort_keys=True),
                                            "copy": "true" if copy else "false",
                                            "preview": "true" if preview else "false"}, TIMESTAMP)
    return pdf, v


async def render_call(store: Store, renderer: Renderer, request) -> print_pb2.RenderResponse:
    """RenderService.Render: PDF only in this version; a template's name and version 0 (current) or more."""
    if request.format not in (print_pb2.FORMAT_PDF, print_pb2.FORMAT_UNSPECIFIED):
        raise errs.new("CAPABILITY_UNSUPPORTED", "only PDF is rendered in this version of infra/print")
    if not TEMPLATE_NAME.fullmatch(request.template) or request.version < 0:
        raise errs.new("REQUEST_INVALID", "template is a template's name and version 0 (current) or more")
    pdf, v = await render(store, renderer, request.template, request.version, request.data, request.copy, False)
    return print_pb2.RenderResponse(content=pdf, media_type="application/pdf", template=v.template, version=v.version,
                                    version_digest=v.digest, content_digest=hashlib.sha256(pdf).hexdigest())


async def preview(store: Store, renderer: Renderer, c):
    """Renders a version with the caller's sample data, marked as a preview."""
    template, version = path(c)
    authz.subject().check(OP_PREVIEW, {})
    pdf, _ = await render(store, renderer, template, version, await c.body(), False, True)
    return Response(pdf, media_type="application/pdf")
