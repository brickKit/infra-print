"""The print component (infra/print): template versions and deterministic rendering (protocol 10). A template
version plus the caller's view model become a PDF; the renderer is Typst in isolated worker processes with time,
memory and output limits. The rules are docs/design.md."""
from __future__ import annotations

from datetime import timedelta
from importlib.resources import files

from besdk.authz import Definitions, Field, Operation, Resource
from besdk.component import Definition
from besdk.config import Key, Type

ID, VERSION, GRPC_PORT = "infra/print", "3.0.0", 9400

# Resources and operations (contract/openapi.yaml names each route's operation).
TEMPLATES = "infra.print.template"
OP_READ, OP_PREVIEW, OP_EDIT = "infra.print.template.read", "infra.print.template.preview", "infra.print.template.edit"
SYSTEM, OP_RENDER = "infra.print.system", "infra.print.system.render"

FIELDS = ["template", "name", "engine", "current_version", "state", "version", "digest", "origin", "data_schema",
          "created_at"]

# The render limits (docs/design.md, "资源上限"): configuration keys with their defaults.
KEYS = [
    Key("PRINT_RENDER_TIMEOUT", Type.DURATION, default=timedelta(seconds=10)),
    Key("PRINT_RENDER_MEMORY_MIB", Type.INTEGER, default=1024, min=256, max=16384),
    Key("PRINT_RENDER_OUTPUT_KIB", Type.INTEGER, default=3072, min=64, max=3584),
    Key("PRINT_RENDER_WORKERS", Type.INTEGER, default=0, min=0, max=256),
    Key("PRINT_RENDER_QUEUE_WAIT", Type.DURATION, default=timedelta(seconds=5)),
]
OWN = {"PRINT_RENDER_TIMEOUT": "one render's limit; past it the worker is killed (RENDER_LIMIT_EXCEEDED)",
       "PRINT_RENDER_MEMORY_MIB": "a worker's address space (RLIMIT_AS), MiB; a worker idles at about 300",
       "PRINT_RENDER_OUTPUT_KIB": "the largest document, KiB; one gRPC message (the SDK's 4 MiB limit) must hold it",
       "PRINT_RENDER_WORKERS": "renders at once (worker processes); 0: the number of CPUs",
       "PRINT_RENDER_QUEUE_WAIT": "how long a render waits for a worker before SERVICE_BUSY"}


def authz() -> Definitions:
    ordinary = {f: Field("ordinary") for f in FIELDS}
    return Definitions(ID, [Resource(TEMPLATES, OP_READ, {}, ordinary), Resource(SYSTEM, "infra.print.system.read", {}, {})],
                       [Operation(OP_READ, TEMPLATES, "read", "low"),
                        Operation(OP_PREVIEW, TEMPLATES, "read", "low"),
                        Operation(OP_EDIT, TEMPLATES, "manage", "low", side_effects=["business_write"], audit=True),
                        Operation("infra.print.system.read", SYSTEM, "read", "low"),
                        Operation(OP_RENDER, SYSTEM, "action", "low")])


def definition() -> Definition:
    from infra_print.module import Print

    return Definition(ID, VERSION, Print, keys=list(KEYS), migrations=files("infra_print") / "migrations",
                      grpc_port=GRPC_PORT, authz=authz())
