"""Rendering and its limits, without a database: the bundled invoice gives the same bytes every time, the copy mark
changes them, the data is checked against the schema, and every limit holds (time, memory, output, queue); the
template reads no file and downloads no package."""
import asyncio
import io
import json
from importlib.resources import as_file

import jsonschema_rs
import pytest
from pypdf import PdfReader

from besdk import errs
from infra_print.module import TIMESTAMP
from infra_print.renderer import Limits, Renderer
from infra_print.templates import FONTS, Version, bundled
from tests.sample import invoice

LIMITS = Limits(timeout=20, memory=1 << 30, output=3 << 20, workers=2, queue_wait=5)


class Log:
    def info(self, *a, **k):
        pass


@pytest.fixture
async def renderer():
    with as_file(FONTS) as fonts:
        r = Renderer(str(fonts), LIMITS, Log())
        yield r
        await r.close()


def inputs(data, copy=False):
    return {"data": json.dumps(data, ensure_ascii=False, sort_keys=True), "copy": "true" if copy else "false",
            "preview": "false"}


def template():
    return next(b for b in bundled() if b.template == "invoice")


async def test_the_same_version_and_data_give_the_same_bytes(renderer):
    t = template()
    first = await renderer.render(t.content, inputs(invoice()), TIMESTAMP)
    assert first.startswith(b"%PDF")
    assert await renderer.render(t.content, inputs(invoice()), TIMESTAMP) == first
    await renderer.close()  # a new worker process
    assert await renderer.render(t.content, inputs(invoice()), TIMESTAMP) == first
    assert await renderer.render(t.content, inputs(invoice(), copy=True), TIMESTAMP) != first, "the copy mark"
    fonts = set()
    for page in PdfReader(io.BytesIO(first)).pages:
        for f in (page["/Resources"].get("/Font") or {}).values():
            fonts.add(str(f.get_object()["/BaseFont"]).split("+")[-1])
    assert fonts and all(f.startswith("NotoSansSC") for f in fonts), f"only the bundled font is embedded: {fonts}"


def test_the_data_is_checked_against_the_schema():
    t = template()
    v = Version(t.template, 1, t.content, t.schema, t.digest, jsonschema_rs.validator_for(t.schema))
    v.validate(invoice())
    bad = invoice()
    del bad["totals"]["gross"]
    bad["lines"][0]["cost"] = "1.00"
    with pytest.raises(errs.Error) as e:
        v.validate(bad)
    assert errs.is_(e.value, "infra/print", "VALIDATION_FAILED")
    assert len(e.value.violations) == 2


async def test_limits_hold(renderer):
    t = template()
    with as_file(FONTS) as fonts:
        for limits, data, why in ((Limits(0.001, 1 << 30, 3 << 20, 1, 5), invoice(), "time"),
                                  (Limits(30, 400 << 20, 64 << 20, 1, 5), invoice(5000), "memory"),
                                  (Limits(30, 1 << 30, 32 << 10, 1, 5), invoice(), "output")):
            r = Renderer(str(fonts), limits, Log())
            try:
                with pytest.raises(errs.Error) as e:
                    await r.render(t.content, inputs(data), TIMESTAMP)
                assert errs.is_(e.value, "infra/print", "RENDER_LIMIT_EXCEEDED"), why
                assert (await Renderer(str(fonts), LIMITS, Log()).render(t.content, inputs(invoice()), TIMESTAMP)), why
            finally:
                await r.close()
        busy = Renderer(str(fonts), Limits(30, 1 << 30, 64 << 20, 1, 0.05), Log())
        try:
            slow = asyncio.create_task(busy.render(t.content, inputs(invoice(1500)), TIMESTAMP))
            await asyncio.sleep(0.01)
            with pytest.raises(errs.Error) as e:
                await busy.render(t.content, inputs(invoice()), TIMESTAMP)
            assert errs.is_(e.value, "be", "SERVICE_BUSY")
            assert (await slow).startswith(b"%PDF")
        finally:
            await busy.close()


async def test_a_template_reads_no_file_and_downloads_no_package(renderer):
    for src in ('#read("/etc/passwd")', '#read("../../etc/hostname")', '#import "@preview/cetz:0.3.4": *\n= x',
                '#image("https://example.com/a.png")'):
        with pytest.raises(errs.Error) as e:
            await renderer.render(src, inputs(invoice()), TIMESTAMP)
        assert errs.is_(e.value, "infra/print", "RENDER_FAILED"), src
