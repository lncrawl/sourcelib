"""JSON Schema generation.

The schema is generated from the model rather than maintained beside it, so the two cannot
disagree. RFC-0001 section 3.10 requires CI to regenerate it and fail on a difference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from sourcelib.spec.model import SourceSpec

SCHEMA_ID = "https://raw.githubusercontent.com/lncrawl/sources/main/schema/source.v1.json"


def build() -> Dict[str, Any]:
    """The JSON Schema for one source definition."""
    schema = SourceSpec.model_json_schema(by_alias=True, mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = "Source definition"
    schema["description"] = (
        "A declarative description of how to read one website, as defined by RFC-0001."
    )
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
