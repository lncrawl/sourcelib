"""Reading a spec document off disk.

Two departures from PyYAML's defaults, both required rather than preferred.

Duplicate keys are refused. PyYAML keeps the last of a repeated key silently, which in a
spec means one of two selectors disappearing with nothing reported.

And booleans follow YAML 1.2, not 1.1. PyYAML implements 1.1, where ``on``, ``off``,
``yes`` and ``no`` are booleans, so ``on: url`` parses as ``True: "url"`` and the ``on``
field of a var vanishes. RFC-0001 specifies YAML 1.2, where only ``true`` and ``false``
are booleans, so the loader implements that. Leaving it alone would mean a document that
reads correctly in a JavaScript or Go parser and wrongly here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

import yaml

from sourcelib.spec.model import SourceSpec

__all__ = ["load_document", "load_file", "parse_yaml"]

#: YAML 1.2's boolean set, replacing YAML 1.1's eleven spellings.
_BOOL_1_2 = re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$")


class _StrictLoader(yaml.SafeLoader):
    """A SafeLoader implementing YAML 1.2 booleans, refusing duplicate mapping keys."""


# The inherited table has bool resolvers registered under y, Y, n, N, o, O and more.
# Adding a resolver would not displace them, so the table is rebuilt without any bool
# entry and the 1.2 one is registered on its own.
_StrictLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictLoader.add_implicit_resolver("tag:yaml.org,2002:bool", _BOOL_1_2, list("tTfF"))


def _no_duplicates(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.YAMLError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _no_duplicates,
)


def parse_yaml(text: str) -> Dict[str, Any]:
    """Parse a YAML document, refusing duplicate keys."""
    data = yaml.load(text, Loader=_StrictLoader)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("a spec document must be a mapping")
    return data


def load_document(data: Dict[str, Any]) -> SourceSpec:
    """Validate an already-parsed document."""
    return SourceSpec.model_validate(data)


def load_file(path: Path) -> SourceSpec:
    """Read and validate one spec document."""
    return load_document(parse_yaml(Path(path).read_text(encoding="utf-8")))
