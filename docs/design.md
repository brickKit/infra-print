[English](design.md) · [中文](design.zh.md)

# infra/print design

Conclusions only, for whoever changes this component's design. How to use it: `BRICKKIT.md`; how the code is laid out: `AGENTS.md`.

## Boundaries

This component renders: a template ID plus data becomes a PDF or a ZPL command stream. It stores the templates and their versions, and records each render. It is a pure function: the same template version and the same data give the same output.

| Not here | Owner | Why |
|---|---|---|
| Whether and when to print ("print only orders above 100 000") | the business component | That is business logic; this component renders whatever it is asked to |
| Where the data comes from, and whether it is right | the caller, which passes all of it as JSON | Looking data up would mean knowing every business schema; this component does not even know whether it prints a real order |
| Who may see the printed business data | the caller | Templates are shared assets; the caller checks its own permissions and data scopes before calling `Render` |
| Printers, drivers, paper | the client | The output is bytes; getting them to a printer is the client's job |
| Keeping the rendered files | a storage or attachment component | The output is returned and never stored |

Two render paths share the entry and nothing else:

| | A4 documents | Barcode labels |
|---|---|---|
| Template | HTML + CSS (paged media) | ZPL command text (`^XA … ^XZ`) |
| Output | PDF bytes | plain text sent straight to a Zebra printer |
| Engine | WeasyPrint | none: string substitution only |

Labels are never made by rendering HTML to an image: that would make the label, the thing that must be exact to the dot, depend on the whole HTML stack.

## Owned data

| Table | Partitioned | Notes |
|---|---|---|
| `print_templates` | no | The current version of each template: `id` (text, chosen by whoever uploads it, such as `sales.delivery_note`), name, `channel` (`PDF` or `ZPL`, checked), content, `version`, `enabled`. `version` is both the version number and the optimistic-concurrency counter: every edit adds exactly one |
| `print_template_versions` | no | Every version ever written, insert only: name, channel, content, `version` (unique per template), `enabled` (the template's enabled state when this version was written), `created_at`. References `print_templates` |
| `print_jobs` | monthly, by `created_at` | One row per real render: template, `actor`, `source_component`, `SUCCESS` / `FAILED`, the error. Metadata only, never the rendered bytes (hundreds of KB per print). Written once, terminal at insert |
| `event_outbox` | weekly, by `created_at` | The outbox the pump publishes from. There is no `event_inbox`: nothing is consumed |

There is no `command_idempotency` table. Templates live in the database, not in files: upload, preview and rollback are run-time actions, a component manifest has no volumes, and in a shell the image has no copy of this repository.

Rollback copies an old version into a new current version (the version number keeps increasing); no version is ever deleted or rewritten. Rollback does not change the enabled flag, so the new version records the state the template had. Rows written before `enabled` existed were backfilled: the current version from the template, older versions as enabled.

## Contract surface

gRPC `infra.print.v1.PrintService`, for other components:

| rpc | Kind | Notes |
|---|---|---|
| `Render` | command without side effects | `template_id` + `data_json` + `source_component` → bytes + `content_type`. No idempotency key: a repeat simply renders again, so a caller that timed out retries without asking for a status first |
| `BatchGetTemplates` | read | The batched read; results in the order asked, missing IDs left out |
| `ListTemplates` | read | Filter by channel; cursor = template ID (`id > cursor`), never offset |

`source_component` is the caller's own word: gRPC between components carries no verified caller identity, so it is recorded for troubleshooting and not checked.

REST under `/infra/print`, every route behind a key: `POST /render` (`infra.print.render`), `GET /templates`, `GET /templates/{id}`, `GET /templates/{id}/versions` (`infra.print.template.view`), `PUT /templates/{id}`, `POST /templates/{id}/rollback`, `POST /templates/{id}/preview` (`infra.print.template.edit`). Preview needs the edit key because it is the what-you-see check while editing; it renders with sample data and records nothing. Being allowed to edit templates does not imply being allowed to print documents: `render` has its own key.

`GET /templates/{id}/versions` lists versions newest first, `{ versions: [{ version, name, enabled, created_at }], next_cursor }`, so that rollback can offer a choice instead of a typed number. The cursor is the last version number of the previous page (`version < cursor`); page size 1–200, default 50. An unknown template is `404`, not an empty list; a cursor that is not a version number is `400`.

There is no status endpoint: nothing here is a long-running write.

**Templates** are full Jinja2, so that a delivery note can loop over its line items. The language is not cut down to plain substitution; instead:

- Rendering runs in Jinja2's `SandboxedEnvironment`: template expressions cannot reach Python internals (`__class__`, `__globals__` …). Templates are uploaded by people with the edit key and run inside the service process, in a shell inside every member's process.
- PDF output is HTML-escaped (`autoescape`), because the data carries user input (customer names, remarks); ZPL is not, because escaping would corrupt the commands.
- WeasyPrint loads only `data:` URIs. `file://` would let a template attach any file the process can read to the PDF, `/proc/self/environ` with the database password included; `http(s)://` would reach internal services. Refused resources are logged and skipped; images are embedded as `data:` URIs.
- Keeping business rules (tax rates, discounts) out of templates is a review matter, not a syntax restriction.

The engine sits behind `render(template, data) -> bytes`; nothing outside `render/` imports WeasyPrint. Engines do get replaced (two well-known open-source ERPs have been moving off wkhtmltopdf), and a replacement then touches one file and no contract.

gRPC messages are limited to 4 MB. A delivery-note PDF is around 100 KB; printing hundreds of labels at once must be split into several calls.

## Events

Published, through the outbox in the same transaction as the `print_jobs` row:

| Subject | When | Payload |
|---|---|---|
| `infra.print.rendered.v1` | after every real render (`Render`, `POST /render`), success or failure | `job_id`, `template_id`, `actor`, `source_component`, `success`, `error` |

A side-channel event, for an audit component: printing changes no business state and nothing downstream waits for it. Previews publish nothing.

Consumed: none. Consuming a business event would mean understanding business meaning, which this component must not.

## Dependencies

None, by design.

| Not a dependency | Why |
|---|---|
| any business component | the data arrives with the request; looking it up would break both "no business logic" and "no knowledge of business schemas" |
| a storage or attachment component | the output is returned, not stored |
| `infra/iam-casdoor` | tokens are verified locally against the JWKS at `IAM_JWKS_URL`: configuration, not an edge |
| `infra/authz` | the permission bundle is polled from `AUTHZ_BUNDLE_URL`: configuration, not an edge |
| NATS | a base resource reached through `NATS_URL`, not a component |

## Place in the synchronous call graph

A leaf with incoming edges only; it calls no one, so it can never close a cycle. Today the PC frontend calls it through REST (the delivery-note button renders `sales.delivery_note`); a business component that prints server-side calls `Render` over gRPC and declares the dependency. It has no part in the three hubs or in the "no synchronous edge between CRM and ERP" rule: it is infrastructure.

Because it is made to be depended on, its alternatives (see Reference implementations) are forks of this component that keep `metadata.id` and the contract, not a family of interchangeable slot components.

## Partitioning and archiving

| Data | Kept hot | Archived when | Where to |
|---|---|---|---|
| `print_templates`, `print_template_versions` | always | never: every version must stay available for rollback | — |
| `print_jobs` | the last three months | whole monthly partitions older than that | `infra_print_archive` |

`print_jobs` is audit evidence ("who printed this invoice"), so old rows are archived, not deleted. The component creates monthly partitions of `print_jobs` three months ahead and weekly partitions of `event_outbox` four weeks ahead (checked at start and every 24 hours); the archiving step itself is not implemented yet (see Open questions).

## Data scopes

None (`data_scopes: none`). Templates are shared assets: one company has one delivery-note template, and everyone who may open the template pages sees all of them. The rendered output does contain business data, but access to that data is decided by the caller before it calls `Render`; this component cannot know whose order it is printing.

## Reference implementations

| Project | Version | Module consulted | What was borrowed | License | Usage |
|---|---|---|---|---|---|
| ERPNext / Frappe | — | Print Format (Jinja HTML templates) and PDF generation | HTML templates as the document format; the move from wkhtmltopdf to Chromium as evidence that the engine must be replaceable | GPL-3 | Borrowed reasoning |
| Odoo | — | QWeb reports → wkhtmltopdf | HTML templates again; the cost of binding to one engine at every call site | LGPL-3 | Borrowed reasoning |
| OCA `report_py3o`, Tryton `relatorio` | — | ODT (LibreOffice) templates, documentation only | that a second school exists: business users edit templates in LibreOffice without a developer | AGPL-3 / GPL-3 | Borrowed practice |
| WeasyPrint | 63.1 | paged-media support, fonts and CJK, the `url_fetcher` hook | the default engine; CJK needs `fonts-noto-cjk` and an explicit `Noto Sans CJK SC` | BSD-3 | Library |
| Jinja2 | 3.1.6 | `jinja2.sandbox` | the sandbox for user-supplied templates | BSD-3 | Library |
| Zebra ZPL II | — | the command reference | the label path renders ZPL text directly | proprietary specification | Borrowed practice |

**Deliberately avoided**: a print module that queries business tables to fill the document (common in ERPs); an engine bound at every call site; labels rendered from HTML through an image. Java report engines (JasperReports, BIRT) were not considered further: they would need either a different stack or a sidecar process a shell cannot host.

**Real variations**, both forks rather than slots (callers depend on this component by ID): the template form (HTML here; ODT for customers without an IT team) and the engine (WeasyPrint here; Chromium where layout fidelity matters more than image size). Either is a fork that replaces the implementation behind `render()` and keeps the `Render` contract.

## Open questions

| Question | Current answer |
|---|---|
| Archiving `print_jobs` after three months | Intended (see Partitioning and archiving), not implemented: monthly partitions accumulate in the hot schema until an archiving loop is written |
| Rendering hundreds of labels at once hits the 4 MB gRPC limit | Callers split the batch. Later: store the output in a storage component and return a download URL |
| Two concurrent `PUT`s of the same template | Both compute the same next version; the second fails on the unique (template, version) index with a server error. Template editing is a rare admin action; a retry on that conflict would remove it |
| Logos and other images in templates | Only `data:` URIs. If templates need shared images, a storage component plus an allow-list in the fetcher is the way, never open `http(s)` fetching |
| Chinese text on ZPL labels | Needs `^CI28` and a CJK font on the printer; the template author's responsibility, not checked here |
