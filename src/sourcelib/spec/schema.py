"""JSON Schema generation.

The schema is generated from the model rather than maintained beside it, so the two cannot
disagree. RFC-0001 section 3.10 requires CI to regenerate it and fail on a difference.

That check runs in the repository holding the *specs*, not here, so the generated file
records which version of this package produced it. Without that, the definitions repository
would have to regenerate with whatever version happened to be latest, and a source pull
request could fail on a schema difference nobody in it caused. ``x-generator`` is an unknown
keyword to a validator and is ignored by one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from sourcelib import __version__
from sourcelib.spec.model import SourceSpec

SCHEMA_ID = "https://raw.githubusercontent.com/lncrawl/sources/main/schema/source.v1.json"

#: The keyword carrying the pip requirement that reproduces this file.
GENERATOR_KEY = "x-generator"


def generator() -> str:
    """A pip requirement that installs the version which generated this schema."""
    return f"lncrawl-sourcelib=={__version__}"


def _allow_deletion(schema: Dict[str, Any]) -> None:
    """Let every property also be null, throughout the document.

    The schema validates a *raw* document, and in one of those a null is not a value: it deletes
    what an ancestor set. A child replacing an inherited `from` with a `page` has no other way to
    say "not this", and the model never sees the null, because `extends` consumes deletions while
    merging and only the resolved document is model-validated.

    Without this, the one mechanism the format has for removing an inherited key was
    unrepresentable in the schema for any field whose type was not already optional, so a correct
    spec failed CI. `spec` is exempt: deleting the version a document declares is never meaningful.
    """
    for definition in [schema, *schema.get("$defs", {}).values()]:
        properties = definition.get("properties")
        if not isinstance(properties, dict):
            continue
        for name, entry in properties.items():
            if not isinstance(entry, dict) or name == "spec":
                continue
            if entry.get("type") == "null" or {"type": "null"} in entry.get("anyOf", []):
                continue
            kept = {k: entry.pop(k) for k in ("title", "description", "default") if k in entry}
            properties[name] = {"anyOf": [entry, {"type": "null"}], **kept}


def build() -> Dict[str, Any]:
    """The JSON Schema for one source definition."""
    schema = SourceSpec.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Source definition"
    schema["description"] = (
        "A declarative description of how to read one website, as defined by RFC-0001."
    )
    _allow_deletion(schema)
    schema[GENERATOR_KEY] = generator()
    return schema


def render() -> str:
    """The schema as it is written to disk: stable key order, trailing newline."""
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write(path: Path) -> bool:
    """Write the schema to *path*. Returns whether the file changed."""
    text = render()
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    print(render(), end="")


if __name__ == "__main__":
    main()
