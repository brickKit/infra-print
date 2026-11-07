"""The bundled templates (infra_print/bundled/<template>/): read with their digest (content, data schema and the
fonts they render with), registered at start as versions by digest (docs/design.md); a customer's current version is
never replaced."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files

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
