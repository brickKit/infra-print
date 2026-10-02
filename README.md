[English](README.md) · [中文](README.zh.md)

# infra/print

A pure rendering service; a template ID plus JSON data becomes a PDF byte stream or a ZPL command stream, with no business logic.

## Use it in a project

```bash
brickkit add infra/print@2.0.0
brickkit up
```

Prepare first: see "Before you deploy" in BRICKKIT.md (a PostgreSQL schema and login role, NATS, the authorization and JWKS URLs, and the fonts a hosting shell must carry).

## Documentation

| To find out | Read |
|---|---|
| What it does and does not do, how to configure it, what to prepare | [BRICKKIT.md](BRICKKIT.md) |
| Its gRPC, REST and event contracts | [contracts/](contracts/) |
| Why it is designed this way: boundary, data, the rendering sandbox, open questions | [docs/design.md](docs/design.md) |
| What it depends on (nothing) and its configuration keys | [component.yaml](component.yaml) |
| How to develop it: code map, tests, pitfalls | [AGENTS.md](AGENTS.md) |

## Development

Python 3.12, FastAPI, `grpc.aio`, asyncpg, yoyo-migrations, Jinja2 and WeasyPrint, on be-sdk-python. `make venv` builds `.venv`; tests need a real PostgreSQL (`TEST_PG_DSN`) and, for the PDF checks, `pdftotext` and the Noto CJK fonts on the machine. The commands and what success looks like are in AGENTS.md, section "Build and test". Inside the BrickEnterprise project, `make verify ID=infra/print FOCUS=1` at the project root runs it for real, in a container and as a local process, and tears down afterwards.
