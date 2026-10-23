"""The documents match the code: component.yaml's configSchema and events, the OpenAPI document and the routes; the
module is merge safe (it runs in the Python render shell)."""
import pathlib

from besdk.http import Routes
from besdk.manifest import event_problems, manifest_problems, openapi_problems
from besdk.mergesafety import scan

import infra_print
from infra_print.module import Print

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_manifest_declares_what_the_sdk_reads():
    assert manifest_problems((ROOT / "component.yaml").read_text(), infra_print.definition(), infra_print.OWN) == []


def test_the_manifest_lists_no_events():
    assert event_problems((ROOT / "component.yaml").read_text(), ROOT / "contract" / "events") == []


def test_the_routes_are_the_openapi_document():
    r = Routes()
    Print.routes(object.__new__(Print), r)
    assert openapi_problems((ROOT / "contract" / "openapi.yaml").read_text(), r.list()) == []


def test_the_module_is_merge_safe():
    assert scan([str(p) for p in (ROOT / "infra_print").glob("*.py") if p.name != "worker.py"]) == []
