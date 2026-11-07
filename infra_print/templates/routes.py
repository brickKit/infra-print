"""The user surface of templates and versions: the template and version lists, a version's read, and the online edit
(not in this version); each checks its operation (module.py registers the routes)."""
from __future__ import annotations

import json
import re

from fastapi.responses import JSONResponse

from besdk import authz, errs, lists
from infra_print import OP_EDIT, OP_READ

TEMPLATE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")

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


def path(c) -> tuple[str, int]:
    """The route's template and version (0: none given); NOT_FOUND for a name that cannot be one."""
    template, version = c.param("template"), c.param("version")
    if not TEMPLATE_NAME.fullmatch(template) or (version and not (version.isdigit() and int(version) >= 1)):
        raise errs.new("NOT_FOUND", "no such template version")
    return template, int(version or 0)


async def _page(db, c, spec: lists.Spec, req: lists.Request, table: str, cols: str, out, keys):
    req.limit, req.cursor = lists.parse_limit(c.query("limit")), c.query("cursor")
    q = lists.build(authz.subject(), spec, req)
    return await lists.page(db, q, table, cols, out, keys)


async def list_templates(db, c):
    page, nxt = await _page(db, c, TEMPLATE_LIST, lists.Request(sort=[lists.Order("template")]), "template",
                            TEMPLATE_COLS, _template_out, lambda t: ([t["template"]], t["template"]))
    return JSONResponse({"templates": page, "next_cursor": nxt})


async def list_versions(db, c):
    template, _ = path(c)
    page, nxt = await _page(db, c, VERSION_LIST, lists.Request(sort=[lists.Order("version", desc=True)],
                                                               filters=[lists.Filter("template", "eq", [template])]),
                            "template_version", VERSION_COLS, _version_out,
                            lambda v: ([str(v["version"])], str(v["version"])))
    return JSONResponse({"versions": page, "next_cursor": nxt})


async def get_version(db, c):
    template, version = path(c)
    authz.subject().check(OP_READ, {})

    async def read(tx):
        return await tx.fetchrow(f"SELECT {VERSION_COLS} FROM template_version v WHERE v.template = $1 AND v.version = $2",
                                 template, version)
    row = await db.tx(read)
    if row is None:
        raise errs.new("NOT_FOUND", "no such template version")
    return JSONResponse(_version_out(row))


async def new_version(db, c):
    path(c)
    authz.subject().check(OP_EDIT, {})
    raise errs.new("CAPABILITY_UNSUPPORTED", "editing templates online is not in this version of infra/print")
