-- Templates and their versions (docs/design.md). A version never changes; a new content is a new version.

CREATE TABLE template (
    template        text PRIMARY KEY,
    name            text NOT NULL,
    engine          text NOT NULL,
    current_version int,
    state           text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'inactive')),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- content is the template's source as the engine reads it; data_schema the JSON Schema of the view model; digest the
-- sha256 of the content, the schema and the fonts (two versions with one digest are one version).
CREATE TABLE template_version (
    template    text NOT NULL REFERENCES template (template),
    version     int NOT NULL CHECK (version >= 1),
    content     text NOT NULL,
    data_schema jsonb NOT NULL,
    digest      text NOT NULL,
    origin      text NOT NULL CHECK (origin IN ('bundled', 'customer')),
    state       text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template, version),
    UNIQUE (template, digest)
);
