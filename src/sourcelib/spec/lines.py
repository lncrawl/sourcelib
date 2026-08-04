"""Where each key of a spec document sits, so an error can point at it.

RFC-0001 section 4.4 requires a failure to name the spec field responsible, its file and its
line. Without the line, fixing a source means reading the whole document to find the selector
that broke, which is most of the cost of a repair.

Line numbers come from the YAML node tree rather than from the loaded mapping, because loading
throws them away.
"""

from __future__ import annotations

from typing import Any, Dict, List, Union

import yaml

__all__ = ["line_map", "locate"]


def line_map(text: str) -> Dict[str, int]:
    """Dotted path to one-based line number, for every key and list entry in *text*.

    ``toc.items.fields.url`` maps to the line declaring it. A list entry appears under its
    index, as in ``novel.title.fallback.0``.
    """
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    if root is None:
        return {}

    found: Dict[str, int] = {}
    _walk(root, "", found)
    return found


def _walk(node: Any, path: str, found: Dict[str, int]) -> None:
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = getattr(key_node, "value", None)
            if key is None:
                continue
            child = f"{path}.{key}" if path else str(key)
            found[child] = key_node.start_mark.line + 1
            _walk(value_node, child, found)
    elif isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            child = f"{path}.{index}" if path else str(index)
            found[child] = item.start_mark.line + 1
            _walk(item, child, found)


def locate(lines: Dict[str, int], field: str) -> Union[int, None]:
    """The line for *field*, or the nearest ancestor's when the exact key is absent.

    A failure often names a path one level deeper than anything the document declares, such as
    ``toc.items.url`` where the document has ``toc.items.fields.url``. Reporting the ancestor is
    more useful than reporting nothing.
    """
    if field in lines:
        return lines[field]
    parts: List[str] = field.split(".")
    while parts:
        parts.pop()
        candidate = ".".join(parts)
        if candidate in lines:
            return lines[candidate]
    return None
