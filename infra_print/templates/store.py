"""A template's versions as the renderer uses them (docs/design.md): a version's content, data schema and digest read
once and kept (a version never changes; the current version is read every time), and the view model checked against
the version's schema before anything renders."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema_rs

from besdk import errs

ERR_VALIDATION = errs.define("infra/print", "VALIDATION_FAILED", errs.Code.INVALID_ARGUMENT, 422, errs.RETRY_NO,
                             "The data does not fit the template version's schema")
ERR_NO_TEMPLATE = "NOT_FOUND"


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
