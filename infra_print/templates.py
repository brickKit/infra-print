"""Templates and their versions (docs/design.md): the bundled templates registered at start by digest, a version's
content, data schema and digest read once and kept (a version never changes), the view model checked against the
schema before anything renders."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

import jsonschema_rs

from besdk import errs

ERR_VALIDATION = errs.define("infra/print", "VALIDATION_FAILED", errs.Code.INVALID_ARGUMENT, 422, errs.RETRY_NO,
                             "The data does not fit the template version's schema")
ERR_NO_TEMPLATE = "NOT_FOUND"

FONTS = files("infra_print") / "fonts"


def fonts_digest() -> dict[str, str]:
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(FONTS.iterdir(), key=lambda f: f.name)
            if f.name.endswith(".otf")}


def digest(content: str, schema: dict, fonts: dict[str, str]) -> str:
    """A version's digest: its content, its data schema and the fonts it renders with."""
    body = json.dumps({"content": hashlib.sha256(content.encode()).hexdigest(),
                       "schema": hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                       "fonts": fonts}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class Bundled:
    template: str
    name: str
    engine: str
    content: str
    schema: dict
    digest: str


def bundled() -> list[Bundled]:
    """The templates the component ships (infra_print/bundled/<template>/)."""
    fonts, out = fonts_digest(), []
    for d in sorted((files("infra_print") / "bundled").iterdir(), key=lambda d: d.name):
        if not d.is_dir():
            continue
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        content = (d / "template.typ").read_text(encoding="utf-8")
        schema = json.loads((d / "schema.json").read_text(encoding="utf-8"))
        out.append(Bundled(meta["template"], meta["name"], meta["engine"], content, schema, digest(content, schema, fonts)))
    return out


async def register(db, log) -> None:
    """Registers each bundled template's content as a version, unless a version with its digest exists. A new bundled
    version becomes current only when the current one is bundled too: a customer's version is never replaced."""
    for b in bundled():
        async def one(tx, b=b) -> str | None:
            await tx.execute("INSERT INTO template (template, name, engine) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                             b.template, b.name, b.engine)
            cur = await tx.fetchrow("SELECT current_version FROM template WHERE template = $1 FOR UPDATE", b.template)
            if await tx.fetchval("SELECT version FROM template_version WHERE template = $1 AND digest = $2",
                                 b.template, b.digest) is not None:
                return None
            n = await tx.fetchval("SELECT coalesce(max(version), 0) + 1 FROM template_version WHERE template = $1",
                                  b.template)
            await tx.execute("INSERT INTO template_version (template, version, content, data_schema, digest, origin) "
                             "VALUES ($1, $2, $3, $4::jsonb, $5, 'bundled')", b.template, n, b.content,
                             json.dumps(b.schema), b.digest)
            current_origin = await tx.fetchval("SELECT origin FROM template_version WHERE template = $1 AND version = $2",
                                               b.template, cur["current_version"]) if cur["current_version"] else None
            if current_origin in (None, "bundled"):
                await tx.execute("UPDATE template SET current_version = $2, updated_at = now() WHERE template = $1",
                                 b.template, n)
            return f"{b.template} v{n}"
        if (added := await db.tx(one)) is not None:
            log.info("a bundled template version was registered", version=added)


@dataclass
class Version:
    template: str
    version: int
    content: str
    schema: dict
    digest: str
    validator: Any = field(repr=False, default=None)

    def validate(self, data: Any) -> None:
        """The view model against the version's schema: VALIDATION_FAILED names every violation."""
        problems = [errs.Violation("/".join(str(p) for p in e.instance_path) or "/", "INVALID", e.message)
                    for e in self.validator.iter_errors(data)]
        if problems:
            raise ERR_VALIDATION.new(f"{len(problems)} problem(s) in the data").with_violations(*problems[:20])


class Store:
    """The versions, read once each (a version never changes); the current version is read every time."""

    def __init__(self, db):
        self.db, self._cache = db, {}

    async def version(self, template: str, version: int) -> Version:
        """A version, or the template's current one for 0; NOT_FOUND for no such template or version."""
        if version and (v := self._cache.get((template, version))):
            return v

        async def read(tx):
            n = version or await tx.fetchval("SELECT current_version FROM template WHERE template = $1 AND state = 'active'",
                                             template)
            if not n:
                return None
            return await tx.fetchrow("SELECT version, content, data_schema::text AS schema, digest FROM template_version "
                                     "WHERE template = $1 AND version = $2", template, n)
        row = await self.db.tx(read)
        if row is None:
            raise errs.new(ERR_NO_TEMPLATE, f"no template {template!r} version {version or 'current'}")
        schema = json.loads(row["schema"])
        v = Version(template, row["version"], row["content"], schema, row["digest"], jsonschema_rs.validator_for(schema))
        self._cache[(template, v.version)] = v
        return v
