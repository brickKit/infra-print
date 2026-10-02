# infra/print

## Purpose

A pure rendering service: a template ID plus the data as JSON becomes a PDF (A4 documents such as delivery notes) or a ZPL command stream (Zebra barcode labels). The same template version and the same data always give the same output; the component holds no business logic and never looks anything up.

**Owns**

- Print templates and their versions: uploading a template (`PUT`) creates a new version, rolling back copies an old version into a new one, nothing is ever deleted. Each template has a channel, `PDF` (HTML + CSS, rendered by WeasyPrint) or `ZPL` (plain text), and an enabled flag.
- Rendering: Jinja2 fills the template (loops and filters, in a sandbox), then the channel decides the engine. PDF rendering loads no external resource: images and styles must be embedded as `data:` URIs.
- A record of every real render in `print_jobs` (who, which template, from which component, success or the error), and the side-channel event `infra.print.rendered.v1` for auditing.

**Does not own**

- Whether and when to print, and what the document says: the business component (for example `erp/sales` for delivery notes) decides, and passes all the data in the request.
- Who may see the business data printed: the caller checks its own permissions and data scopes before calling `Render`; templates here are shared by everyone.
- Printers, drivers, paper and print queues: the client receives bytes and sends them to the printer.
- Keeping the rendered files: the output is returned, never stored; long-term storage belongs to a storage or attachment component.

## Before you deploy

- **PostgreSQL**: a schema `infra_print`, plus `infra_print_archive` if your schema convention creates one (nothing writes to it yet). A login role `infra_print_rw` with `USAGE` and `CREATE` on `infra_print`, and its password. The migration (`python -m infra_print.migrate apply`, from this component's own image) logs in as this role and creates the tables and yoyo's state tables (`_yoyo_log`, `_yoyo_migration`, `_yoyo_version`, `yoyo_lock`) in the schema, so the role owns them; that matters because the running component creates the monthly partitions of `print_jobs` and the weekly partitions of `event_outbox` itself. BrickKit creates none of this; in the BrickEnterprise assembly project `make dev-env` writes the password into `.env` and `make db-init` creates the schemas, role and grants.
- **NATS** reachable at `NATS_URL`: the render event goes out through an outbox table and a background pump; the component starts without NATS reachable, and events wait in the outbox.
- **Authorization** (`infra/authz`) and **identity** (`infra/iam-casdoor`, or any IAM serving a JWKS) reachable at `AUTHZ_BUNDLE_URL` and `IAM_JWKS_URL` for the REST routes to answer anything but errors. They are configuration, not dependencies: the component starts without them. The gRPC service checks no token.
- **Fonts and system libraries** are in the image: WeasyPrint's Pango, Cairo, HarfBuzz and GDK-Pixbuf, and `fonts-noto-cjk` with the fontconfig cache built. A Python shell that hosts this component must install the same; without the CJK fonts the image builds and `/healthz` is green, but Chinese text in a PDF comes out garbled.
- **Running it as a local process** (`mode: local` in the deploy file, or `brickkit up --focus infra/print`): `local.runCommand` is `.venv/bin/python -m infra_print.main`, relative to the component directory, so run `make venv` there first; without `.venv` the process does not start. The machine then needs what the image otherwise provides: WeasyPrint's system libraries and the Noto CJK fonts.
- **Templates**: none ship with the component. Upload them with `PUT /infra/print/templates/{id}`; write `font-family: "Noto Sans CJK SC"` for Chinese text, and embed images as `data:` URIs. In the BrickEnterprise project, `make seed` in the component directory loads a default delivery note `sales.delivery_note` (two versions) and a shipping label `sales.shipping_label`, once the component is running.

## Dependencies

None. Callers send all the data the document needs, so the component calls no other component and consumes no events. Business components call it: over gRPC (`Render`), or a browser through REST (`POST /infra/print/render`).

The authorization bundle and the JWKS are fetched from the URLs in the configuration, not through a dependency edge. Without them every protected route fails closed: `IAM_JWKS_URL` empty → `403` even before the token is read; no token, or a token that does not verify (also when the JWKS is unreachable) → `401`; the bundle never fetched yet → `503`; a valid user without the permission key → `403`. `/healthz` stays `200` throughout: it reports only that this process is alive.

## Configuration

| Variable | Meaning |
|---|---|
| `PG_HOST` | PostgreSQL host. Usually the project's shared value (`$var:PG_HOST`). |
| `PG_PORT` | PostgreSQL port; default `5432`. |
| `PG_DATABASE` | The database holding the `infra_print` schema (`$var:PG_DATABASE`). |
| `PG_USER` | The login role, `infra_print_rw` as a literal. Inside a shell the shell logs in with its own role and switches to this one per transaction (`SET LOCAL ROLE`), so the role name must be `<PG_SCHEMA>_rw`. |
| `PG_PASSWORD` | Password of `PG_USER`. Secret: write `${INFRA_PRINT_DB_PASSWORD}` (or your secret store's reference), never the value. |
| `PG_SCHEMA` | Schema of all tables, the outbox and yoyo's state tables; default `infra_print`. Write the literal anyway so every component's schema is visible in one place. |
| `NATS_URL` | NATS server the outbox pump publishes to (`$var:NATS_URL`). |
| `OTEL_BASE_URL` | OpenTelemetry collector base URL; empty (the default) exports nothing. |
| `AUTHZ_BUNDLE_URL` | URL of the authorization bundle the permission check polls, for example `http://infra-authz-2-0-0:8223/authz/bundle`. Required: without it every protected route answers `503`. Keep it in step with the authz version the project runs. |
| `IAM_JWKS_URL` | URL of the JWKS used to verify user tokens locally, for example `http://infra-iam-casdoor-2-0-0:8200/.well-known/jwks.json`. Required: without it every protected route answers `403`. |

## Contracts

- `contracts/infra/print/v1/print.proto` — gRPC `infra.print.v1.PrintService`, for other components: `Render` (`template_id`, `data_json`, `source_component` → `content` bytes and `content_type`, `application/pdf` or `text/plain`; no idempotency key, a repeated call simply renders again; unknown template → `NOT_FOUND`, disabled → `FAILED_PRECONDITION`, malformed JSON or a template that fails to render → `INVALID_ARGUMENT`), `BatchGetTemplates` (in the order asked, missing IDs left out), `ListTemplates` (filter by channel, cursor paging by ID, page size up to 200, default 50). The generated Python package is `infra.print.v1` under `gen/`. gRPC messages are limited to 4 MB: render large batches of labels in several calls.
- `contracts/print.openapi.yaml` — REST under `/infra/print`, every route behind a permission key: `POST /render` (`infra.print.render`; `404` unknown, `409` disabled, `422` render failure), `GET /templates` and `GET /templates/{id}` (`infra.print.template.view`), `GET /templates/{id}/versions` (`infra.print.template.view`; newest first, `{ versions: [{ version, name, enabled, created_at }], next_cursor }`, `cursor` / `page_size`; `404` unknown template, `400` bad cursor), `PUT /templates/{id}` (`infra.print.template.edit`; creates the next version; `channel` must be `PDF` or `ZPL`, otherwise `422`), `POST /templates/{id}/rollback` (`infra.print.template.edit`; copies the given version into a new one), `POST /templates/{id}/preview` (`infra.print.template.edit`, because previewing is part of editing; renders without recording a job or publishing an event).
- `contracts/events/print.events.json` — published through the outbox: `infra.print.rendered.v1`, after every real render, success or failure (`job_id`, `template_id`, `actor` — the user's `sub` on REST, empty on gRPC —, `source_component` as the caller reported it, `success`, `error`). Previews publish nothing. No events are consumed.
- `assembly.yaml` — this project's metadata: the three permission keys above, the menu entry, the edge route `/infra/print/**`, the schema and role, and `data_scopes: none`.

## Shell declaration

Not a shell. It can be hosted in a Python shell (in the BrickEnterprise project, `be/py-render`) or run on its own; the code is the same either way. The shell imports `create_module` from `infra_print.module`; the component's top-level packages are `infra_print` and the namespace package `infra` (contract code), so other Python members do not overwrite them.
