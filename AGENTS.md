[English](AGENTS.md) · [中文](AGENTS.zh.md)

# infra/print

The AI guide to developing this component. How to use it, its boundaries and contracts: BRICKKIT.md. Why it is shaped this way: `docs/design.md`. Dependencies, configuration and deployment: component.yaml.

## Code map

| Path | Owns |
|---|---|
| `backend/infra_print/module.py` | The only entry, `create_module(rt)`: builds the service, the FastAPI app (`new_fastapi_app`), the gRPC registration, and the start loop (outbox pump + two partition loops). Same function standalone and in a shell |
| `backend/infra_print/main.py` | `run_standalone(create_module)`; refuses any argument (exit 2) |
| `backend/infra_print/migrate.py` | The migration container's entry, `python -m infra_print.migrate apply`: yoyo, `PG_*`, `search_path` through libpq `options`. The only file that reads the process environment |
| `backend/infra_print/render/` | The renderer interface (`base.py`), the channel switch `get_renderer` (`__init__.py`), PDF (`pdf.py`: sandboxed Jinja2 + WeasyPrint with a `data:`-only fetcher) and ZPL (`zpl.py`) |
| `backend/infra_print/repo/templates.py` | Templates and their versions: get, batch get, list, put, rollback, list versions |
| `backend/infra_print/repo/jobs.py` | One `print_jobs` row plus the outbox event, in one transaction |
| `backend/infra_print/service/print_service.py` | Shared by REST and gRPC: render (records a job), preview (records nothing), structural checks, the version cursor |
| `backend/infra_print/http/routes.py` | REST routes, each registered with `besdk.get`, `besdk.post` or `besdk.put` and a permission key; error → status mapping |
| `backend/infra_print/grpc/server.py` | `infra.print.v1.PrintService` |
| `backend/infra_print/partition.py` | Monthly partitions of `print_jobs` (three months ahead), weekly partitions of `event_outbox` (four weeks ahead) |
| `migrations/` | yoyo SQL migrations with their `.rollback.sql` |
| `contracts/` | proto, OpenAPI, event schema |
| `gen/infra/print/v1/` | Generated Python code (`grpc_tools.protoc`), the package `infra.print.v1`; never edited by hand. `gen/infra/` has no `__init__.py` on purpose |
| `scripts/check.py` | The ast scans behind `make import-scan` and `make module-check` |
| `scripts/seed.sh` | Local demo data: the templates in `scripts/templates/`, then one real render over gRPC |
| `tests/` | pytest against a real PostgreSQL, real WeasyPrint, a real in-process gRPC server, real JWTs and bundle polling (`tests/authz_helpers.py`) |

| Feature | Start here | Then |
|---|---|---|
| A new REST route | `contracts/print.openapi.yaml` (append only) | `backend/infra_print/http/routes.py` (`besdk.get` with a key from `assembly.yaml`), `backend/infra_print/service/print_service.py` |
| A new rpc or field | `contracts/infra/print/v1/print.proto` | regenerate `gen/` (Build and test), `backend/infra_print/grpc/server.py` |
| A new column | `migrations/` (the next `00N_*.sql` and its rollback) | `backend/infra_print/repo/templates.py` |
| Another render engine or channel | `backend/infra_print/render/base.py` (the interface) | `backend/infra_print/render/__init__.py`, the `channel` CHECK in `migrations/`, the enum in the contracts |
| A rendering security question | `backend/infra_print/render/pdf.py` | `tests/test_render_safety.py` |
| An error answering with the wrong status | `backend/infra_print/http/routes.py` | `backend/infra_print/grpc/server.py` |

## Build and test

```bash
make venv                    # .venv with Python 3.12, this component (editable) and the dev tools: "✓ .venv 就绪"
# tests run against the test database brickkit_test_db, never brickkit_db (make test-db-init ID=infra/print at the project root)
export TEST_PG_DSN="postgres://postgres:<password>@localhost:5432/brickkit_test_db?sslmode=disable"
make test                    # "N passed", no "skipped" line; refuses to run without TEST_PG_DSN
make check-version dag-check contract-check import-scan module-check   # each ends with one ✓ line
make docs-check              # "0 with errors, 0 warnings"
# migrations, as the login role:
PG_HOST=localhost PG_PORT=5432 PG_DATABASE=brickkit_test_db PG_USER=infra_print_rw \
  PG_PASSWORD=<password of infra_print_rw> PG_SCHEMA=infra_print make migrate-idempotent   # ✓ 迁移幂等
```

The PDF tests call `/usr/bin/pdftotext` and need the Noto CJK fonts installed on the machine. After changing the `.proto`, regenerate with the pinned `grpcio-tools`:

```bash
.venv/bin/python -m grpc_tools.protoc -I contracts \
  -I "$(.venv/bin/python -c 'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')" \
  --python_out=gen --grpc_python_out=gen --pyi_out=gen contracts/infra/print/v1/print.proto
```

`make contract-check` fails when `gen/` is not what this command produces. On a real machine, from the project root: `make verify ID=infra/print ROUTE=/infra/print/templates FOCUS=1 SEED=1` builds the image, starts only what this component needs, checks migration, health and the permission check, loads the demo templates, runs a focus run, and tears down. `make image` additionally checks the image can import the component and has the CJK font.

## Design decisions

- **A pure function, so no idempotency key.** The same template version and data give the same bytes; `Render` takes no `idempotency_key`, unlike the write commands of other components, and there is no `command_idempotency` table. Callers retry by calling again.
- **Templates live in the database**, versioned append-only; rollback copies an old version forward. Nothing reads template or migration files at run time: in a shell the image has no copy of this repository.
- **Full Jinja2, in a sandbox.** Real documents need loops over line items, so the language is not cut down; `SandboxedEnvironment` keeps expressions away from Python internals. Keeping business rules out of templates is a review matter.
- **PDF rendering fetches nothing.** The WeasyPrint `url_fetcher` accepts only `data:` URIs; refused resources are logged and skipped.
- **Two render paths behind one interface**: HTML → PDF and template → ZPL share `get_renderer(channel)` and nothing else. Swapping WeasyPrint for another engine changes `render/pdf.py` only.
- **Migrations are not part of the module**: brickKit runs `python -m infra_print.migrate apply` from this image; `Module` carries no migrations directory.
- **Package names are unique per component**: the code is `infra_print`, the contract code is `infra.print.v1` under the namespace package `infra`, so a Python shell can host several members.

## Pitfalls

| Never | Symptom | Why |
|---|---|---|
| Add an `idempotency_key` to `Render`, or a `command_idempotency` table | Nothing fails; the table stays empty forever and the next person copies the pattern | Rendering has no side effect worth deduplicating; a repeat is just another render |
| Render templates with a plain `jinja2.Environment` | Tests of normal templates pass; anyone who may edit a template can run code in the service process (and in a shell, in every member's process) | `{{ cycler.__init__.__globals__.os… }}` reaches `os`; `tests/test_render_safety.py` covers it |
| Let WeasyPrint use its default URL fetcher, or give it a `base_url` | PDFs look right; `<link rel="attachment" href="file:///proc/self/environ">` embeds the process environment, database password included, in the PDF, and `http://` reaches internal services | Only `data:` URIs are loaded |
| Install Pango / Cairo / GDK-Pixbuf without `fonts-noto-cjk` and `fc-cache -f` (here or in the hosting shell's image) | Image builds, `/healthz` is green; Chinese in the PDF is garbled and repeated, not boxes | WeasyPrint falls back to DejaVu |
| Leave out `font-family: "Noto Sans CJK SC"` in a Chinese template | Text renders, with Japanese glyph variants | Without a language hint fontconfig picks the JP face of Noto CJK |
| Insert into `print_template_versions` before upserting `print_templates` | The first save of a new template fails on the foreign key | The version row references the template row |
| Read a file relative to the working directory or `__file__` in module code | Works standalone; breaks in a shell, whose image has no `migrations/` or templates from this repository | Only `migrate.py` reads `migrations/`, inside this component's own image |
| Add `gen/infra/__init__.py`, or name the code package `app` again | A second Python member in the same shell overwrites or shadows this one's packages | `infra` is a namespace package; `make import-scan` rejects `import app` |
| Regenerate `gen/` with a different `grpcio-tools` than `pyproject.toml` pins | `make contract-check` fails on files that changed only in generator output | The check compares against freshly generated code |

## Before changing code

1. Is this about rendering (here) or about what to print and when (the caller)? The second never goes here, nor does looking data up.
2. Am I adding a dependency, a consumed event, an idempotency key or row-level scoping? Stop: see Design decisions and `docs/design.md`.
3. Does the change touch `render/`? Keep the sandbox and the `data:`-only fetcher; run `tests/test_render_safety.py`.
4. Contract change? Append only: a new field, rpc, path or query parameter. Regenerate `gen/` with the pinned tool.
5. A new REST route is registered with `besdk.get` / `post` / `put` and a key from `assembly.yaml`.
6. New rule → write the failing test first (real database for anything SQL), then the code.
7. Bump `metadata.version` (and `pyproject.toml`) before the first change after a release; update BRICKKIT.md and `docs/design.md` in the same commit as the code.

<!-- brickkit:managed:begin lang=en -->
<!-- maintained by brickkit (init, add, remove, upgrade, skills update): edits between these markers are overwritten -->

## BrickKit

This is a BrickKit component: `component.yaml` is all the platform reads. The rules it relies on:

- `configSchema` keys are the environment variable names the code reads. Never use a reserved name: `COMPONENT_ID`, `COMPONENT_VERSION`, `PORT`, `BRICKKIT_SERVED_MEMBERS`, `BRICKKIT_SERVED_MEMBERS_CONFIG`, or any `*_ENDPOINT`.
- Dependencies are exact versions. A dependency's address arrives as `<ID>_ENDPOINT`; an optional dependency that is absent has no variable at all, so read it with a fallback.
- `/healthz` checks only this process, never a dependency. The migration command runs from the same image and must fail on an argument it does not know.
- `BRICKKIT.md` travels to every project that uses this component and is read there without the repository: keep it in step with the code, with no relative links.
- Release: raise `metadata.version`, commit, push, `brickkit release`. `brickkit lint` checks the manifest and these docs — inside a project, run in this directory, it checks only this component (`--all` for the whole project).
- The full rules are in the `brickkit-component` skill (`.claude/skills/brickkit-component/SKILL.md` at the root of the project or repository where skills are installed; `brickkit skills update` installs it); for flags ask `brickkit <command> --help`; BrickKit's own documentation is `brickkit docs`.
<!-- brickkit:managed:end -->
