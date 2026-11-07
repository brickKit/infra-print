"""The print component against PostgreSQL over its real surfaces (L2): the bundled template registered at start (once,
and never over a customer's version), the template lists and the version read, the preview, and RenderService.Render
called by components of the deployment with their service identity. Needs BE_SDK_TEST_PG_ADMIN (a PostgreSQL
superuser DSN); the database is brickkit_test_db, the schema infra_print_it (dropped and made again at the start).
Run: make test-integration (uv run pytest -q -m integration itest/)."""
import asyncio
import hashlib
import io
import json
import os
import socket
import time
from urllib.parse import urlsplit

import asyncpg
import grpc
import httpx
import pytest

from be.infra.print.v3 import print_pb2, print_pb2_grpc
from besdk import errs, launcher, reqctx
from besdk.calls.client import Member, options
from besdk.config import EnvSource
from besdk.testing import ALICE, BOB, World

import infra_print
from infra_print.templates import bundled as templates
from tests.sample import invoice

pytestmark = pytest.mark.integration
SCHEMA, DATABASE = "infra_print_it", "brickkit_test_db"


@pytest.fixture
async def world(tmp_path):
    w = await World(tmp_path).start()
    try:
        yield w
    finally:
        await w.stop()


def db_env() -> dict[str, str]:
    dsn = os.environ.get("BE_SDK_TEST_PG_ADMIN", "")
    assert dsn, "BE_SDK_TEST_PG_ADMIN is needed"
    u = urlsplit(dsn)
    return {"PG_HOST": u.hostname or "localhost", "PG_PORT": str(u.port or 5432), "PG_DATABASE": DATABASE,
            "PG_USER": u.username, "PG_PASSWORD": u.password, "PG_SCHEMA": SCHEMA, "PG_OWNER_USER": u.username,
            "PG_OWNER_PASSWORD": u.password}


async def admin() -> asyncpg.Connection:
    e = db_env()
    return await asyncpg.connect(host=e["PG_HOST"], port=int(e["PG_PORT"]), user=e["PG_USER"], password=e["PG_PASSWORD"],
                                 database=DATABASE)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start(world, grpc_port: int):
    """Migrates and runs the member; answers its user surface's base URL and how to stop it."""
    port, stop, out = free_port(), asyncio.Event(), io.StringIO()
    env = {"COMPONENT_ID": infra_print.ID, "COMPONENT_VERSION": infra_print.VERSION, "PORT": str(port),
           "STOP_DRAIN_DELAY": "50ms", **db_env(), **world.env(infra_print.ID)}
    o = launcher.Options(env=EnvSource(lookup_env=env.get), stdout=out, stderr=out, host="127.0.0.1")
    d = infra_print.definition()
    d.grpc_port = grpc_port
    assert await launcher.run(d, launcher.Options(args=["migrate"], env=o.env, stdout=out, stderr=out), stop) == 0, out.getvalue()
    task = asyncio.create_task(launcher.run(d, o, stop))
    async with httpx.AsyncClient() as c:
        for _ in range(1000):
            assert not task.done(), out.getvalue()
            try:
                if (await c.get(f"http://127.0.0.1:{port}/readyz")).status_code == 200:
                    break
            except httpx.ConnectError:
                pass
            await asyncio.sleep(0.01)
    return f"http://127.0.0.1:{port}", stop, task, out


async def render(world, component: str, grpc_port: int, **req) -> print_pb2.RenderResponse:
    m = Member()
    m.set_tls(world.tls(component))
    try:
        stub = print_pb2_grpc.RenderServiceStub(m.grpc(infra_print.ID, f"127.0.0.1:{grpc_port}"))
        with reqctx.deadline_scope(time.monotonic() + 20):
            return await stub.Render(print_pb2.RenderRequest(**req), metadata=options(idempotent=True))
    finally:
        await m.close()


async def test_print_end_to_end(world, monkeypatch):
    a = await admin()
    await a.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
    world.grant_all(ALICE, infra_print.authz())
    grpc_port = free_port()
    base, stop, task, out = await start(world, grpc_port)
    try:
        auth = {"Authorization": world.bearer(ALICE)}
        async with httpx.AsyncClient(base_url=base, timeout=30) as c:
            # the bundled invoice is version 1, current
            r = await c.get("/api/print/templates", headers=auth)
            assert r.status_code == 200, r.text
            assert r.json() == {"templates": [{"template": "invoice", "name": "销售发票", "engine": "typst",
                                               "current_version": 1, "state": "active"}], "next_cursor": ""}
            r = await c.get("/api/print/templates/invoice/versions/1", headers=auth)
            assert r.status_code == 200 and r.json()["origin"] == "bundled" and "lines" in r.json()["data_schema"]["properties"]
            digest = r.json()["digest"]
            assert (await c.get("/api/print/templates/invoice/versions/9", headers=auth)).status_code == 404
            # preview: the sample data, marked as a preview
            r = await c.post("/api/print/templates/invoice/versions/1/preview", headers=auth, content=json.dumps(invoice()))
            assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
            r = await c.post("/api/print/templates/invoice/versions/1/preview", headers=auth, content=json.dumps({"x": 1}))
            assert r.status_code == 422 and r.json()["reason"] == "VALIDATION_FAILED"
            r = await c.post("/api/print/templates/invoice/versions", headers=auth, content="{}")
            assert r.status_code == 501 and r.json()["reason"] == "CAPABILITY_UNSUPPORTED"
            # a reader without a grant sees an empty list (security 07), not a refusal
            r = await c.get("/api/print/templates", headers={"Authorization": world.bearer(BOB)})
            assert r.status_code == 200 and r.json()["templates"] == []

        # Render, for any component of the deployment: version 0 is the current one; the same data, the same bytes
        data = json.dumps(invoice(), ensure_ascii=False).encode()
        first = await render(world, "erp/billing", grpc_port, template="invoice", data=data, format=print_pb2.FORMAT_PDF)
        assert (first.template, first.version, first.version_digest) == ("invoice", 1, digest)
        assert first.media_type == "application/pdf" and first.content_digest == hashlib.sha256(first.content).hexdigest()
        again = await render(world, "erp/sales", grpc_port, template="invoice", version=1, data=data)
        assert again.content == first.content, "deterministic"
        copy = await render(world, "erp/billing", grpc_port, template="invoice", version=1, data=data, copy=True)
        assert copy.content != first.content
        for req, reason in ((dict(template="invoice", data=b'{"x": 1}'), "VALIDATION_FAILED"),
                            (dict(template="receipt", data=data), "NOT_FOUND"),
                            (dict(template="invoice", data=data, format=print_pb2.FORMAT_ZPL), "CAPABILITY_UNSUPPORTED")):
            with pytest.raises(errs.Error) as e:
                await render(world, "erp/billing", grpc_port, **req)
            assert e.value.reason == reason, req
    finally:
        stop.set()
        assert await task == 0, out.getvalue()

    # a customer's version stays current when the bundle changes; the same bundle registers nothing new
    await a.execute(f"INSERT INTO {SCHEMA}.template_version (template, version, content, data_schema, digest, origin) "
                    f"SELECT template, 2, content, data_schema, 'customer-edit', 'customer' FROM {SCHEMA}.template_version "
                    f"WHERE template = 'invoice' AND version = 1")
    await a.execute(f"UPDATE {SCHEMA}.template SET current_version = 2 WHERE template = 'invoice'")
    original = templates.bundled
    changed = lambda: [templates.Bundled(b.template, b.name, b.engine, b.content + "\n// v2", b.schema, b.digest + "-2")
                       for b in original()]
    monkeypatch.setattr(templates, "bundled", changed)
    for _ in range(2):
        base, stop, task, out = await start(world, free_port())
        stop.set()
        assert await task == 0, out.getvalue()
    rows = await a.fetch(f"SELECT version, origin FROM {SCHEMA}.template_version ORDER BY version")
    assert [(r["version"], r["origin"]) for r in rows] == [(1, "bundled"), (2, "customer"), (3, "bundled")]
    assert await a.fetchval(f"SELECT current_version FROM {SCHEMA}.template WHERE template = 'invoice'") == 2
    await a.close()


async def test_a_full_recoverys_checks(world):  # be-protocol P14.1-P14.3 (3.0.22)
    """The recovery tool asks the print component whether the audited templates are still there at the version an
    audit record names: the bundled invoice at its current version, a later version lost; another component may not
    ask."""
    from besdk._gen.be.recovery.v1 import check_pb2, check_pb2_grpc

    grpc_port = free_port()
    base, stop, task, out = await start(world, grpc_port)
    try:
        async def stub(component):
            m = Member()
            m.set_tls(world.tls(component))
            return m, check_pb2_grpc.CheckServiceStub(m.grpc(infra_print.ID, f"127.0.0.1:{grpc_port}"))

        m, s = await stub("ops/recover")
        try:
            with reqctx.deadline_scope(time.monotonic() + 10):
                ref = lambda v="": check_pb2.ObjectRef(type=infra_print.TEMPLATES, id="invoice", version=v)  # noqa: E731
                now = (await s.Present(check_pb2.PresentRequest(recovery="rec-1", objects=[ref()]),
                                       metadata=options(idempotent=True))).answers[0]
                assert now.answer == "present" and int(now.version) >= 1, "the bundled invoice at its current version"
                v = int(now.version)
                got = await s.Present(check_pb2.PresentRequest(recovery="rec-1", objects=[
                    ref(str(v)), ref(str(v + 1)), check_pb2.ObjectRef(type=infra_print.TEMPLATES, id="no-such")]),
                    metadata=options(idempotent=True))
                checks = await s.Check(check_pb2.CheckRequest(recovery="rec-1"), metadata=options(idempotent=True))
        finally:
            await m.close()
        assert [(a.answer, a.version) for a in got.answers] == [("present", str(v)), ("older", str(v)), ("absent", "")]
        assert {r.check for r in checks.results} >= {"be.projections", "be.audit_backlog"}
        m, s = await stub("erp/billing")
        try:
            with reqctx.deadline_scope(time.monotonic() + 10), pytest.raises(errs.Error) as e:
                await s.Check(check_pb2.CheckRequest(recovery="rec-1"), metadata=options(idempotent=True))
        finally:
            await m.close()
        assert e.value.reason == "PERMISSION_DENIED", "only the recovery tool"
    finally:
        stop.set()
        await task
